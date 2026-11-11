"""Room server: the RoomStore log and worker bundles over HTTP, behind bearer tokens.

Stage 2 trust model: the host starts the server with the tokens of the intended
participants, so the token list is the membership list. Identity comes only from
the token. Plain HTTP: trusted LAN or tunnel only until stage 3 adds TLS and
end-to-end encryption. Nothing here logs tokens, message bodies or bundle bytes.
"""

from __future__ import annotations

import hashlib
import hmac
import errno
import json
import logging
import os
import re
import tempfile
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse

from aletheore.council.accounts import AccountError, AccountService
from aletheore.council.bundle_store import OBJECT_MAGIC, OBJECT_MIN, OBJECT_SLACK, ROOM_RE
from aletheore.council.identity import PublicIdentity, safety_words
from aletheore.council.envelope import HostOnlyError, check_envelope
from aletheore.council.protocol import (
    MAX_BODY_BYTES,
    MAX_ENVELOPE_BYTES,
    EventDraft,
    EventType,
    MAX_BUNDLE_BYTES,
    ProtocolError,
    event_to_json,
    is_host_only,
    validate_draft,
)
from aletheore.council.server_config import Metrics, RateLimiter, ServerSettings, TransferBudget, client_ip
from aletheore.council.store import CRYPTO_MODES, MAX_TITLE, ROOM_MODES, RoomStore

log = logging.getLogger("council.server")

_SHA_RE = re.compile(r"^[0-9a-f]{64}\Z")


def _stored_path(base: Path, room_id: str, sha: str, suffix: str) -> Path:
    """Where a room's stored object lives, checked to be inside the storage folder (ids are validated by the callers too)."""
    if not ROOM_RE.match(room_id) or room_id in (".", "..") or not _SHA_RE.match(sha):
        raise HTTPException(status_code=422, detail="not a valid room or object id")
    root = os.path.realpath(str(base))
    full = os.path.realpath(os.path.join(root, room_id, sha + suffix))
    if not full.startswith(root + os.sep):
        raise HTTPException(status_code=422, detail="not a valid room or object id")
    return Path(full)
_EVENT_LIMIT = MAX_BODY_BYTES + 4096
MAX_GRANTS = 64 * 1024
MAX_WRAPPED = 8192
MAX_GRANT = 8192


def parse_tokens(token_args: list[str], token_file: str | None, allow_empty: bool = False) -> dict[str, str]:
    """Returns {token: participant}. Accepts NAME=TOKEN arguments and/or a JSON file of {name: token}."""
    tokens: dict[str, str] = {}

    def add(name: str, tok: str) -> None:
        if not name or not tok or not tok.strip():
            raise ValueError("every token entry needs a non-empty name and a non-empty token")
        if tok in tokens:
            raise ValueError("two participants share the same token; each needs their own")
        tokens[tok] = name

    for arg in token_args:
        name, sep, tok = arg.partition("=")
        if not sep:
            raise ValueError("--token must look like NAME=TOKEN")
        add(name, tok)
    if token_file:
        for name, tok in json.loads(Path(token_file).read_text(encoding="utf-8")).items():
            add(str(name), str(tok))
    if not tokens and not allow_empty:
        raise ValueError("at least one token is required")
    return tokens


def create_app(
    store: RoomStore,
    tokens: dict[str, str],
    bundle_dir: Path,
    retention_days: int = 7,
    clock: Callable[[], float] = time.time,
    max_events_per_room: int = 50_000,
    max_bundles_per_room: int = 50,
    max_room_bundle_bytes: int = 500 * 1024 * 1024,
    max_rooms_per_host: int | None = None,
    settings: ServerSettings | None = None,
    ops_clock: Callable[[], float] = time.monotonic,
    accounts: AccountService | None = None,
    host_identity=None,
) -> FastAPI:
    """`settings=None` is the plain stage 2 behaviour (no rate limits); the hosted server always passes settings."""
    limits_on = settings is not None
    settings = settings or ServerSettings()
    budget = TransferBudget(settings.file_transfer_cap_mb_per_day * 1024 * 1024, clock)
    if max_rooms_per_host is None:
        max_rooms_per_host = settings.max_rooms_per_host
    if (not tokens and accounts is None) or any(not t or not t.strip() for t in tokens):
        raise ValueError("at least one non-empty token is required (or an account service)")
    if accounts is not None:
        clash = sorted(p for p in set(tokens.values()) if accounts.handle_exists(p))
        if clash:
            raise ValueError(f"token participants collide with registered account handles: {', '.join(clash)}")
    bundle_dir = Path(bundle_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(_app):
        import anyio.to_thread

        anyio.to_thread.current_default_thread_limiter().total_tokens = settings.threads  # database work runs on this many threads
        yield
        close = getattr(store, "close", None)  # release database connections on a clean shutdown
        if close is not None:
            close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    metrics = Metrics()
    create_lock = threading.Lock()  # per server process; with several instances the room limit is a soft anti-abuse limit
    ip_limiter = RateLimiter(settings.rate_limit_ip_per_min, settings.rate_limit_ip_burst, ops_clock) if limits_on else None
    fail_limiter = RateLimiter(settings.auth_fail_per_min, settings.auth_fail_burst, ops_clock) if limits_on else None
    events_limiter = RateLimiter(settings.rate_limit_events_per_min, settings.rate_limit_events_burst, ops_clock) if limits_on else None

    def too_many(limiter: RateLimiter, key: str, what: str) -> HTTPException:
        metrics.inc("council_rate_limited_total", {"kind": what})
        return HTTPException(status_code=429, detail="too many requests; slow down", headers={"Retry-After": str(limiter.retry_after(key))})

    @app.exception_handler(ConnectionError)
    async def storage_unavailable(_request: Request, _exc: ConnectionError) -> JSONResponse:
        metrics.inc("council_storage_unavailable_total")
        return JSONResponse(status_code=503, content={"detail": "storage temporarily unavailable; try again"}, headers={"Retry-After": "2"})

    class Ops:
        """Per-request bookkeeping as plain ASGI middleware (rate limit per client address, metrics, log line). Starlette's
        BaseHTTPMiddleware spawns a task and memory streams for every request; measured under load, that was a large share of the CPU."""

        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                await self.inner(scope, receive, send)
                return
            started = time.perf_counter()
            state = scope.setdefault("state", {})
            client = scope.get("client")
            forwarded = next((v.decode("latin-1") for k, v in scope["headers"] if k == b"x-forwarded-for"), None)
            ip = client_ip(client[0] if client else "", forwarded, settings.trusted_proxies)
            state["ip"], state["who_hash"] = ip, None
            status = 500  # what is recorded if the application raises

            async def send_wrapper(message):
                nonlocal status
                if message["type"] == "http.response.start":
                    status = message["status"]
                await send(message)

            try:
                if ip_limiter is not None and scope["path"] not in ("/healthz", "/readyz") and not ip_limiter.allow(ip):
                    exc = too_many(ip_limiter, ip, "ip")
                    await JSONResponse(status_code=429, content={"detail": exc.detail}, headers=exc.headers)(scope, receive, send_wrapper)
                else:
                    await self.inner(scope, receive, send_wrapper)
            finally:
                route = scope.get("route")
                template = getattr(route, "path", "unmatched")
                elapsed = time.perf_counter() - started
                metrics.inc("council_requests_total", {"route": template, "status": str(status)})
                metrics.observe("council_request_seconds", elapsed)
                if settings.log_requests == "all" or (settings.log_requests == "errors" and status >= 400):
                    log.info(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "method": scope["method"],
                                         "route": template, "status": status, "ms": round(elapsed * 1000, 1), "who": state.get("who_hash")}))

    app.add_middleware(Ops)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        try:
            store.host_of("__readyz__")
        except KeyError:
            pass  # the store answered: it is reachable
        except ConnectionError:
            return JSONResponse(status_code=503, content={"status": "not ready"})
        return JSONResponse(content={"status": "ready"})

    @app.get("/metrics")
    async def metrics_endpoint(request: Request) -> PlainTextResponse:
        if not settings.admin_token_hash:
            raise HTTPException(status_code=404, detail="not found")
        header = request.headers.get("authorization", "")
        presented = header[7:] if header.lower().startswith("bearer ") else ""
        if not hmac.compare_digest(hashlib.sha256(presented.encode()).hexdigest(), settings.admin_token_hash):
            raise HTTPException(status_code=401, detail="admin token required")
        return PlainTextResponse(metrics.render())

    def purge() -> None:
        cutoff = clock() - retention_days * 86400
        for f in [*bundle_dir.glob("*/*.bundle"), *bundle_dir.glob("*/*.blob")]:
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass

    purge()

    # Token -> participant by the token's hash: one lookup per request however many tokens there are.
    token_index = {hashlib.sha256(t.encode()).digest(): participant for t, participant in tokens.items()}

    def who(request: Request) -> str:
        ip = getattr(request.state, "ip", "")
        if fail_limiter is not None and fail_limiter.blocked(ip):
            raise too_many(fail_limiter, ip, "auth_fail")  # too many wrong tokens from this address: even a right one waits
        header = request.headers.get("authorization", "")
        presented = header[7:] if header.lower().startswith("bearer ") else ""
        found = None
        if accounts is not None and presented:
            session = accounts.session_participant(presented)  # a signed-in account device
            if session is not None:
                found = session[0]
                request.state.via_session, request.state.device_id = True, session[1]
        if found is None:
            found = token_index.get(hashlib.sha256(presented.encode()).digest())  # the stage 2 token map
        if found is None:
            if fail_limiter is not None:
                fail_limiter.take(ip)
            metrics.inc("council_auth_failures_total")
            raise HTTPException(status_code=401, detail="missing or invalid token")
        request.state.who_hash = hashlib.sha256(("council-log:" + found).encode()).hexdigest()[:10]  # never the name in logs
        return found

    async def awho(request: Request) -> str:
        """`who` for async handlers: a session lookup may touch the database, so it runs on a worker thread."""
        if accounts is None:
            return who(request)
        return await run_in_threadpool(who, request)

    def room_for(room_id: str, me: str, host_only: bool = False) -> str:
        """The room's host, after checking the caller belongs to the room (or is its host, when `host_only`)."""
        return room_info_for(room_id, me, host_only).host

    def room_info_for(room_id: str, me: str, host_only: bool = False):
        """The room's record, after the same checks."""
        if not ROOM_RE.match(room_id) or room_id in (".", ".."):
            raise HTTPException(status_code=404, detail="unknown room")
        try:
            info = store.room_info(room_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown room")
        if me not in info.members:
            raise HTTPException(status_code=403, detail="not a member of this room; ask its host to invite you")
        if host_only and me != info.host:
            raise HTTPException(status_code=403, detail="only the room host can do this")
        return info

    def bundle_path(room_id: str, sha: str) -> Path:
        if not _SHA_RE.match(sha):
            raise HTTPException(status_code=422, detail="bundle id must be a lowercase sha-256")
        return _stored_path(bundle_dir, room_id, sha, ".bundle")

    if accounts is not None:
        async def json_body(request: Request, limit: int = 8192) -> dict:
            raw = await request.body()
            if len(raw) > limit:
                raise HTTPException(status_code=413, detail="request too large")
            try:
                data = json.loads(raw)
            except (ValueError, RecursionError):  # RecursionError: JSON nested deeper than the parser allows
                raise HTTPException(status_code=422, detail="body must be JSON")
            if not isinstance(data, dict):
                raise HTTPException(status_code=422, detail="body must be a JSON object")
            return data

        def text_field(data: dict, key: str, max_len: int = 256) -> str:
            v = data.get(key)
            if not isinstance(v, str) or not v or len(v) > max_len:
                raise HTTPException(status_code=422, detail=f"{key} is required")
            return v

        def hex_field(data: dict, key: str, size: int | None = None, max_len: int = 2 * MAX_GRANTS) -> bytes:
            v = data.get(key)
            if not isinstance(v, str) or len(v) > max_len:
                raise HTTPException(status_code=422, detail=f"{key} must be hex")
            try:
                raw = bytes.fromhex(v)
            except ValueError:
                raise HTTPException(status_code=422, detail=f"{key} must be hex")
            if size is not None and len(raw) != size:
                raise HTTPException(status_code=422, detail=f"{key} has the wrong length")
            return raw

        def address_gate(request: Request) -> str:
            ip = getattr(request.state, "ip", "")
            if fail_limiter is not None and fail_limiter.blocked(ip):
                raise too_many(fail_limiter, ip, "auth_fail")
            return ip

        async def attempt(request: Request, fn):
            """Run an account operation on a worker thread; refusals count against the caller's address."""
            ip = address_gate(request)
            try:
                return await run_in_threadpool(fn)
            except AccountError as exc:
                if exc.status in (401, 422, 403, 404, 409) and fail_limiter is not None:
                    fail_limiter.take(ip)
                metrics.inc("council_auth_failures_total")
                raise HTTPException(status_code=exc.status, detail=str(exc))

        @app.post("/v1/register")
        async def register(request: Request) -> dict:
            body = await json_body(request)
            code, handle = text_field(body, "code"), text_field(body, "handle")
            public = PublicIdentity(hex_field(body, "sign_vk", 32), hex_field(body, "box_pk", 32))
            account = await attempt(request, lambda: accounts.redeem_registration_code(code, public, handle))
            return {"handle": account.handle, "can_host": account.can_host, "device_id": public.device_id}

        @app.post("/v1/login/challenge")
        async def login_challenge(request: Request) -> dict:
            device_id = text_field(await json_body(request), "device_id")
            return {"nonce": await attempt(request, lambda: accounts.login_challenge(device_id))}

        @app.post("/v1/login")
        async def login(request: Request) -> dict:
            body = await json_body(request)
            device_id, nonce = text_field(body, "device_id"), text_field(body, "nonce")
            signature = hex_field(body, "signature", 64)
            token, expires = await attempt(request, lambda: accounts.login(device_id, nonce, signature))
            handle = (await run_in_threadpool(accounts.session_participant, token) or ("", ""))[0]
            return {"token": token, "expires_at": expires, "participant": handle}

        @app.get("/v1/devices")
        async def my_devices(request: Request) -> dict:
            """This account's devices and the link requests waiting for one of them to approve. Session sign-in only."""
            me = await awho(request)
            current = getattr(request.state, "device_id", None)
            if not current:
                raise HTTPException(status_code=403, detail="sign in with a registered device to see your devices")

            def build() -> dict:
                devices = [{"device_id": d.device_id, "words": safety_words(PublicIdentity(d.sign_vk, d.box_pk).fingerprint),
                            "created_at": d.created_at, "revoked": d.revoked_at is not None, "current": d.device_id == current}
                           for d in accounts.list_devices(me)]
                pending = [{"request_id": r.request_id, "device_id": r.device_id, "words": r.words} for r in accounts.pending_links(me)]
                return {"handle": me, "devices": devices, "pending": pending}

            return await run_in_threadpool(build)

        @app.post("/v1/devices/{device_id}/revoke")
        async def revoke_my_device(device_id: str, request: Request) -> dict:
            """Revoke one of this account's other devices. The device in use cannot revoke itself."""
            me = await awho(request)
            current = getattr(request.state, "device_id", None)
            if not current:
                raise HTTPException(status_code=403, detail="sign in with a registered device to revoke one")
            if device_id == current:
                raise HTTPException(status_code=400, detail="this is the device you are using; revoke it from another one")

            def go() -> dict:
                device = accounts.get_device(device_id)
                if device is None or device.handle != me:
                    raise AccountError("no such device on your account", 404)
                accounts.revoke_device(device_id)
                return {"revoked": device_id}

            return await attempt(request, go)

        @app.post("/v1/devices/link-requests")
        async def new_link_request(request: Request) -> dict:
            body = await json_body(request)
            handle = text_field(body, "handle")
            public = PublicIdentity(hex_field(body, "sign_vk", 32), hex_field(body, "box_pk", 32))
            req = await attempt(request, lambda: accounts.create_link_request(handle, public))
            return {"request_id": req.request_id, "words": req.words}

        @app.get("/v1/devices/link-requests/{request_id}")
        async def link_status(request_id: str, request: Request) -> dict:
            address_gate(request)
            req = await run_in_threadpool(accounts.get_link_request, request_id)
            if req is None:
                raise HTTPException(status_code=404, detail="no such link request")
            return {"status": req.status, "words": req.words, "grants": req.grants.hex() if req.status == "approved" else ""}

        @app.post("/v1/devices/link-requests/{request_id}/approve")
        async def approve_link(request_id: str, request: Request) -> dict:
            await awho(request)  # signed in (a session, not a legacy token)
            device_id = getattr(request.state, "device_id", None)
            if not device_id:
                raise HTTPException(status_code=403, detail="sign in with a registered device to approve a link")
            body = await json_body(request, limit=2 * MAX_GRANTS + 1024)
            words = text_field(body, "words")
            grants = hex_field(body, "grants") if body.get("grants") else b""
            device = await attempt(request, lambda: accounts.approve_link_request(device_id, request_id, words, grants))
            return {"device_id": device.device_id, "handle": device.handle}

        # -- room key grants (sealed by the host; the server stores them without being able to read them) --------------
        @app.put("/v1/rooms/{room_id}/grants/{participant}/{epoch}")
        async def put_grant(room_id: str, participant: str, epoch: int, request: Request) -> dict:
            info = await run_in_threadpool(room_info_for, room_id, await awho(request), True)
            if participant not in info.members:
                raise HTTPException(status_code=404, detail="that person is not a member of this room")
            body = await json_body(request, limit=2 * MAX_GRANT + 1024)
            device_id, grant = text_field(body, "device_id"), hex_field(body, "grant", max_len=2 * MAX_GRANT)

            def put() -> dict:
                try:
                    accounts.put_grant(room_id, participant, epoch, device_id, grant)
                except AccountError as exc:
                    raise HTTPException(status_code=exc.status, detail=str(exc))
                return {"ok": True}

            return await run_in_threadpool(put)

        @app.get("/v1/rooms/{room_id}/grants/me")
        async def my_grants(room_id: str, request: Request) -> dict:
            me = await awho(request)
            await run_in_threadpool(room_for, room_id, me)
            device_id = getattr(request.state, "device_id", None)
            if not device_id:
                raise HTTPException(status_code=403, detail="sign in with a registered device to fetch your grants")
            found = await run_in_threadpool(accounts.my_grants, room_id, device_id)
            return {"grants": [{"epoch": e, "grant": g.hex()} for e, g in found]}

        # -- invitations -------------------------------------------------------------------------------------------
        def spend_guess(request: Request) -> None:
            ip = getattr(request.state, "ip", "")
            if fail_limiter is not None:
                fail_limiter.take(ip)  # a miss on a public lookup counts against the caller's address
            metrics.inc("council_auth_failures_total")

        @app.post("/v1/rooms/{room_id}/invites")
        async def create_invite(room_id: str, request: Request) -> dict:
            me = await awho(request)
            await run_in_threadpool(room_for, room_id, me, True)
            body = await json_body(request, limit=2 * MAX_WRAPPED + 1024)
            wrapped = hex_field(body, "wrapped_room_key", max_len=2 * MAX_WRAPPED)
            verifier = hex_field(body, "claim_verifier", 32) if "claim_verifier" in body else b""
            ttl = body.get("ttl_seconds", 86400)
            if isinstance(ttl, bool) or not isinstance(ttl, (int, float)):
                raise HTTPException(status_code=422, detail="ttl_seconds must be a number")
            device_id = getattr(request.state, "device_id", "") or ""

            def make() -> dict:
                try:
                    invite_id, expires = accounts.create_invite(room_id, device_id, wrapped, float(ttl), store.room_info(room_id).title, verifier)
                except AccountError as exc:
                    raise HTTPException(status_code=exc.status, detail=str(exc))
                return {"invite_id": invite_id, "expires_at": expires}

            return await run_in_threadpool(make)

        @app.get("/v1/invites/{invite_id}")
        async def read_invite(invite_id: str, request: Request) -> dict:
            address_gate(request)
            info = await run_in_threadpool(accounts.get_invite, invite_id)
            if info is None:
                spend_guess(request)
                raise HTTPException(status_code=404, detail="no such invite")
            if info.status != "open":
                raise HTTPException(status_code=410, detail=f"this invite is {info.status}; ask the host for a new one")
            host = None
            if info.host is not None:
                host = {"handle": info.host.handle, "device_id": info.host.device_id, "sign_vk": info.host.sign_vk.hex(),
                        "box_pk": info.host.box_pk.hex()}
            return {"room_id": info.room_id, "title": info.title, "wrapped_room_key": info.wrapped.hex(), "expires_at": info.expires_at,
                    "host": host}

        @app.post("/v1/invites/{invite_id}/requests")
        async def request_join(invite_id: str, request: Request) -> dict:
            body = await json_body(request)
            handle = text_field(body, "handle")
            public = PublicIdentity(hex_field(body, "sign_vk", 32), hex_field(body, "box_pk", 32))
            claim_key = hex_field(body, "claim_key", 32) if "claim_key" in body else b""
            req = await attempt(request, lambda: accounts.claim_invite(invite_id, handle, public, claim_key))
            return {"request_id": req.request_id, "words": req.words}

        @app.get("/v1/join-requests/{request_id}")
        async def join_status(request_id: str, request: Request) -> dict:
            address_gate(request)
            req = await run_in_threadpool(accounts.get_join_request, request_id)
            if req is None:
                spend_guess(request)
                raise HTTPException(status_code=404, detail="no such join request")
            return {"status": req.status, "words": req.words, "handle": req.handle,
                    "room_id": req.room_id if req.status == "approved" else ""}

        @app.get("/v1/rooms/{room_id}/join-requests")
        async def list_join_requests(room_id: str, request: Request) -> dict:
            await run_in_threadpool(room_for, room_id, await awho(request), True)
            pending = await run_in_threadpool(accounts.list_join_requests, room_id)
            return {"requests": [{"request_id": r.request_id, "handle": r.handle, "device_id": r.device_id, "words": r.words,
                                  "created_at": r.created_at} for r in pending]}

        async def decide(room_id: str, request_id: str, request: Request, approve: bool) -> dict:
            await run_in_threadpool(room_for, room_id, await awho(request), True)

            def go() -> dict:
                try:
                    req = accounts.approve_join(room_id, request_id) if approve else accounts.reject_join(room_id, request_id)
                except AccountError as exc:
                    raise HTTPException(status_code=exc.status, detail=str(exc))
                if approve:
                    store.add_member(room_id, req.handle)  # idempotent, so a retried approval completes a half-done one
                return {"handle": req.handle, "status": req.status, "members": store.members_of(room_id)}

            return await run_in_threadpool(go)

        @app.post("/v1/rooms/{room_id}/join-requests/{request_id}/approve")
        async def approve_join(room_id: str, request_id: str, request: Request) -> dict:
            return await decide(room_id, request_id, request, True)

        @app.post("/v1/rooms/{room_id}/join-requests/{request_id}/reject")
        async def reject_join(room_id: str, request_id: str, request: Request) -> dict:
            return await decide(room_id, request_id, request, False)

    @app.get("/v1/whoami")
    async def whoami(request: Request) -> dict:
        return {"participant": await awho(request)}

    def info_json(info) -> dict:
        return {"room_id": info.room_id, "host": info.host, "title": info.title, "mode": info.mode, "status": info.status,
                "members": info.members, "last_seq": info.last_seq, "last_activity": info.last_activity, "crypto": info.crypto}

    @app.post("/v1/rooms/{room_id}")
    async def create_room(room_id: str, request: Request) -> dict:
        me = await awho(request)
        via_session = bool(getattr(request.state, "via_session", False))
        if not ROOM_RE.match(room_id) or room_id in (".", ".."):
            raise HTTPException(status_code=422, detail="invalid room id")
        raw_body = await request.body()
        if len(raw_body) > 4096:
            raise HTTPException(status_code=413, detail="request too large")
        try:
            body = json.loads(raw_body)
        except (ValueError, RecursionError):
            raise HTTPException(status_code=422, detail="body must be JSON")
        host = body.get("host") if isinstance(body, dict) else None
        if not isinstance(host, str) or host != me:
            raise HTTPException(status_code=403, detail="only the named host can create a room")
        title, mode = body.get("title", ""), body.get("mode", "interactive")
        crypto_mode = body.get("crypto")  # None means "no preference": new rooms are plain, an existing room keeps its mode
        if crypto_mode is not None and crypto_mode not in CRYPTO_MODES:
            raise HTTPException(status_code=422, detail=f"crypto must be one of {', '.join(CRYPTO_MODES)}")
        if not isinstance(title, str) or len(title) > MAX_TITLE or any(ord(c) < 32 for c in title):
            raise HTTPException(status_code=422, detail=f"title must be plain text of at most {MAX_TITLE} characters")
        if mode not in ROOM_MODES:
            raise HTTPException(status_code=422, detail=f"mode must be one of {', '.join(ROOM_MODES)}")
        def create() -> dict:
            if accounts is not None and via_session and not accounts.can_host(me):
                raise HTTPException(status_code=403, detail="your account may join rooms but not host them; ask the server admin to enable hosting")
            with create_lock:  # check-then-create must be one step, or concurrent requests all pass the check
                try:
                    existing = store.host_of(room_id)
                    if existing != host:
                        raise HTTPException(status_code=409, detail="room exists with a different host")
                    if crypto_mode is not None and store.room_info(room_id).crypto != crypto_mode:
                        raise HTTPException(status_code=409, detail="the room's encryption mode is fixed when it is created")
                except KeyError:
                    if sum(1 for r in store.list_rooms(me) if r.host == me and r.status == "open") >= max_rooms_per_host:
                        raise HTTPException(status_code=422, detail=f"you already host {max_rooms_per_host} open rooms; close one first")
                    store.create_room(room_id, host, title=title, mode=mode, crypto=crypto_mode or "none")
            return {"room_id": room_id, "host": host}

        return await run_in_threadpool(create)

    @app.get("/v1/rooms")
    def list_rooms(request: Request) -> dict:
        return {"rooms": [info_json(r) for r in store.list_rooms(who(request))]}

    @app.get("/v1/rooms/{room_id}")
    def room_info(room_id: str, request: Request) -> dict:
        me = who(request)
        room_for(room_id, me)
        return info_json(store.room_info(room_id))

    @app.post("/v1/rooms/{room_id}/members")
    async def add_member(room_id: str, request: Request) -> dict:
        await run_in_threadpool(room_for, room_id, await awho(request), True)
        try:
            body = json.loads(await request.body())
        except (ValueError, RecursionError):
            raise HTTPException(status_code=422, detail="body must be JSON")
        who_to = body.get("participant") if isinstance(body, dict) else None
        known = isinstance(who_to, str) and (who_to in set(tokens.values())
                                             or (accounts is not None and await run_in_threadpool(accounts.handle_exists, who_to)))
        if not known:
            raise HTTPException(status_code=422, detail="that participant is not known to this server")
        def add() -> dict:
            current = store.members_of(room_id)
            if who_to not in current and len(current) >= settings.max_members_per_room:
                raise HTTPException(status_code=422, detail=f"this room already has {settings.max_members_per_room} members, the most this server allows")
            store.add_member(room_id, who_to)
            return {"members": store.members_of(room_id)}

        return await run_in_threadpool(add)

    @app.delete("/v1/rooms/{room_id}/members/{participant}")
    async def remove_member(room_id: str, participant: str, request: Request) -> dict:
        """Host only. The person loses access at once: every request checks membership, so their next one is refused."""
        info = await run_in_threadpool(room_info_for, room_id, await awho(request), True)
        if participant == info.host:
            raise HTTPException(status_code=422, detail="the host cannot be removed from their own room")
        if participant not in info.members:
            raise HTTPException(status_code=404, detail="that person is not a member of this room")

        def remove() -> dict:
            store.remove_member(room_id, participant)
            return {"members": store.members_of(room_id)}

        return await run_in_threadpool(remove)

    @app.post("/v1/rooms/{room_id}/close")
    def close_room(room_id: str, request: Request) -> dict:
        room_for(room_id, who(request), host_only=True)
        store.close_room(room_id)
        return {"status": "closed"}

    @app.get("/v1/rooms/{room_id}/host")
    def host_of(room_id: str, request: Request) -> dict:
        return {"host": room_for(room_id, who(request))}

    @app.get("/v1/rooms/{room_id}/last_seq")
    def last_seq(room_id: str, request: Request) -> dict:
        room_for(room_id, who(request))
        return {"last_seq": store.last_seq(room_id)}

    @app.post("/v1/rooms/{room_id}/events")
    async def post_event(room_id: str, request: Request) -> dict:
        me = await awho(request)
        if events_limiter is not None and not events_limiter.allow(me):
            raise too_many(events_limiter, me, "events")
        info = await run_in_threadpool(room_info_for, room_id, me)
        host, encrypted = info.host, info.crypto == "e2e-v1"
        limit = (MAX_ENVELOPE_BYTES if encrypted else MAX_BODY_BYTES) + 4096
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise HTTPException(status_code=413, detail="event too large")
        raw_bytes = await request.body()
        if len(raw_bytes) > limit:
            raise HTTPException(status_code=413, detail="event too large")
        try:
            raw = json.loads(raw_bytes)
        except (ValueError, RecursionError):
            raise HTTPException(status_code=422, detail="body must be JSON")
        if not isinstance(raw, dict):
            raise HTTPException(status_code=422, detail="event must be an object")
        if raw.get("sender_participant") != me:
            raise HTTPException(status_code=403, detail="sender does not match token")
        if encrypted or raw.get("type") == "envelope":
            if not encrypted:
                raise HTTPException(status_code=422, detail="this room is not end-to-end encrypted: envelopes are not accepted here")
            if raw.get("type") != "envelope":
                raise HTTPException(status_code=422, detail="this room is end-to-end encrypted: send sealed envelopes")
            cid = raw.get("client_event_id")
            if not isinstance(cid, str) or not cid.strip() or len(cid) > 200:
                raise HTTPException(status_code=422, detail="client_event_id is required")
            try:
                check_envelope(body=raw.get("body"), sender=me, host=host, thread_id=raw.get("thread_id"),
                               sender_agent=raw.get("sender_agent"), reply_to=raw.get("reply_to"))
            except HostOnlyError as exc:
                raise HTTPException(status_code=403, detail=str(exc))
            except ProtocolError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            if accounts is not None:  # the signing device must be an active device of the sender's account
                device = await run_in_threadpool(accounts.get_device, raw["body"]["dev"])
                if device is None or not device.active or device.handle != me:
                    raise HTTPException(status_code=403, detail="that device does not belong to your account")
            draft = EventDraft(room_id, cid, raw["thread_id"], EventType.ENVELOPE, me, None, None, raw["body"])
        else:
            try:
                draft = validate_draft(raw, room_id=room_id, sender_participant=me, sender_agent=raw.get("sender_agent"))
            except ProtocolError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            if is_host_only(draft.type, draft.body) and me != host:
                raise HTTPException(status_code=403, detail="only the room host can post this kind of event")
        def store_it():
            if store.room_info(room_id).status == "closed":
                raise HTTPException(status_code=409, detail="this room is closed")
            if store.last_seq(room_id) >= max_events_per_room:
                raise HTTPException(status_code=429, detail="this room has reached its event limit")
            return store.append(draft)

        stored = await run_in_threadpool(store_it)
        metrics.inc("council_events_appended_total")
        return event_to_json(stored)

    @app.get("/v1/host-proof")
    def host_proof(nonce: str) -> dict:
        """A server run by its host can sign a fresh nonce with the host's device key, so a guest can check it is talking to the host
        whose key is in the invitation and not to someone who now holds a recycled address."""
        from aletheore.council.tunnel_registry import host_proof_message

        if host_identity is None:
            raise HTTPException(status_code=404, detail="this server does not run on a host's device")
        try:
            raw = bytes.fromhex(nonce)
        except ValueError:
            raise HTTPException(status_code=400, detail="the nonce must be hex")
        if not 8 <= len(raw) <= 64:
            raise HTTPException(status_code=400, detail="the nonce must be 8 to 64 bytes")
        pub = host_identity.public()
        return {"sign_vk": pub.sign_vk.hex(), "box_pk": pub.box_pk.hex(), "signature": host_identity.sign(host_proof_message(raw)).hex()}

    @app.get("/v1/rooms/{room_id}/devices")
    def room_devices(room_id: str, request: Request) -> dict:
        """Each member's active device public keys: what a reader needs to check who signed an encrypted event."""
        info = room_info_for(room_id, who(request))
        out: dict[str, list[dict]] = {}
        if accounts is not None:
            for member in info.members:
                out[member] = [{"device_id": d.device_id, "sign_vk": d.sign_vk.hex(), "box_pk": d.box_pk.hex()}
                               for d in accounts.list_devices(member) if d.active]
        return {"devices": out}

    @app.get("/v1/rooms/{room_id}/archive")
    def read_archive(room_id: str, request: Request):
        """Every event that moved to cold storage, oldest first, one JSON object per line. Members only; for someone who wants the old text
        itself (a snapshot already gives the room's state)."""
        room_for(room_id, who(request))
        if not hasattr(store, "read_archive"):
            raise HTTPException(status_code=404, detail="this server does not archive")
        from fastapi.responses import StreamingResponse

        def lines():
            for e in store.read_archive(room_id):
                yield json.dumps(event_to_json(e), separators=(",", ":"), ensure_ascii=False) + "\n"

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    @app.get("/v1/rooms/{room_id}/events")
    def read_events(
        room_id: str, request: Request, after_seq: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=500)
    ) -> dict:
        room_for(room_id, who(request))
        return {"events": [event_to_json(e) for e in store.read_after(room_id, after_seq, limit)]}

    # JSON answers over 1 KB are gzip-compressed for clients that ask (httpx does). Bundles and images are already compressed: left alone.
    from starlette.middleware.gzip import GZipMiddleware

    class _JsonOnlyGzip:
        def __init__(self, inner_app):
            self.plain, self.zipped = inner_app, GZipMiddleware(inner_app, minimum_size=1024, compresslevel=5)

        async def __call__(self, scope, receive, send):
            path = scope.get("path", "") if scope["type"] == "http" else ""
            target = self.plain if ("/bundles/" in path or "/blobs/" in path) else self.zipped
            await target(scope, receive, send)

    app.add_middleware(_JsonOnlyGzip)

    @app.exception_handler(OSError)
    @app.exception_handler(sqlite3.OperationalError)
    async def storage_trouble(request: Request, exc: Exception):
        """A full disk is a plain, retryable answer (507), not a bare 500: clients hold what they were sending and try again later."""
        text = str(exc).lower()
        if getattr(exc, "errno", None) == errno.ENOSPC or "disk is full" in text or "no space left" in text:
            return JSONResponse(status_code=507, content={"detail": "this server's disk is full, so nothing new can be saved right now. "
                                                                    "Reading still works; sending will work again once the host frees some space."})
        if "database is locked" in text:
            return JSONResponse(status_code=503, content={"detail": "the database is busy; try again in a moment"})
        log.info(json.dumps({"event": "unhandled_storage_error", "error": type(exc).__name__}))
        return JSONResponse(status_code=500, content={"detail": "internal error"})

    def over_budget(n: int) -> HTTPException:
        """509 (bandwidth limit exceeded): a plain refusal the client does not retry in a loop. Messages are unaffected."""
        snap = budget.snapshot()
        hours = max(1, round(snap["resets_in_s"] / 3600))
        mb = lambda b: f"{b / 1048576:.1f}"  # noqa: E731
        return HTTPException(status_code=509, detail=(
            f"this file ({mb(n)} MB) would go over this host's file transfer allowance for the day ({mb(snap['used_bytes'])} of "
            f"{mb(snap['cap_bytes'])} MB used). Space frees up gradually, in about {hours} hour(s). Messages still work. "
            "For big files use a direct connection or Tailscale."))

    @app.get("/v1/transfer-budget")
    def transfer_budget(request: Request) -> dict:
        who(request)  # members only
        return budget.snapshot()

    @app.put("/v1/rooms/{room_id}/bundles/{sha}")
    async def put_bundle(room_id: str, sha: str, request: Request) -> dict:
        encrypted = (await run_in_threadpool(room_info_for, room_id, await awho(request))).crypto == "e2e-v1"
        path = bundle_path(room_id, sha)
        limit = MAX_BUNDLE_BYTES + (OBJECT_SLACK if encrypted else 0)  # an encrypted room's objects are sealed: size is all we can check
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise HTTPException(status_code=413, detail="bundle too large")
        chunks, total = [], 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > limit:
                raise HTTPException(status_code=413, detail="bundle too large")
            chunks.append(chunk)
        data = b"".join(chunks)
        if not budget.allows(len(data)):
            raise over_budget(len(data))

        def finish() -> dict:
            if encrypted:
                if len(data) < OBJECT_MIN or not data.startswith(OBJECT_MAGIC):
                    raise HTTPException(status_code=422, detail="an encrypted room stores sealed objects only")
            elif hashlib.sha256(data).hexdigest() != sha:
                raise HTTPException(status_code=422, detail="bundle does not match its sha-256")
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                existing = list(path.parent.glob("*.bundle"))
                if len(existing) >= max_bundles_per_room or sum(f.stat().st_size for f in existing) + len(data) > max_room_bundle_bytes:
                    raise HTTPException(status_code=429, detail="this room has reached its bundle storage limit")
            fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            purge()
            budget.add(len(data))
            return {"sha256": sha, "size": len(data)}

        return await run_in_threadpool(finish)

    @app.get("/v1/rooms/{room_id}/bundles/{sha}")
    def get_bundle(room_id: str, sha: str, request: Request) -> Response:
        room_for(room_id, who(request))
        path = bundle_path(room_id, sha)
        try:
            age_ok = path.stat().st_mtime >= clock() - retention_days * 86400
        except OSError:
            raise HTTPException(status_code=404, detail="no such bundle")
        if not age_ok:
            path.unlink(missing_ok=True)
            raise HTTPException(status_code=404, detail="no such bundle")
        size = path.stat().st_size
        if not budget.allows(size):
            raise over_budget(size)
        budget.add(size)
        return Response(content=path.read_bytes(), media_type="application/octet-stream")

    @app.put("/v1/rooms/{room_id}/blobs/{sha}")
    async def put_blob(room_id: str, sha: str, request: Request) -> dict:
        from aletheore.council.attachments import MAX_BLOBS_PER_ROOM, MAX_IMAGE_BYTES, MAX_ROOM_BLOB_BYTES, sniff_image

        encrypted = (await run_in_threadpool(room_info_for, room_id, await awho(request))).crypto == "e2e-v1"
        if not _SHA_RE.match(sha):
            raise HTTPException(status_code=422, detail="image id must be a lowercase sha-256")
        limit = MAX_IMAGE_BYTES + (OBJECT_SLACK if encrypted else 0)
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise HTTPException(status_code=413, detail="image too large")
        chunks, total = [], 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > limit:
                raise HTTPException(status_code=413, detail="image too large")
            chunks.append(chunk)
        data = b"".join(chunks)
        if not budget.allows(len(data)):
            raise over_budget(len(data))

        def finish() -> dict:
            if encrypted:
                if len(data) < OBJECT_MIN or not data.startswith(OBJECT_MAGIC):
                    raise HTTPException(status_code=422, detail="an encrypted room stores sealed objects only")
            else:
                if hashlib.sha256(data).hexdigest() != sha:
                    raise HTTPException(status_code=422, detail="image does not match its sha-256")
                if sniff_image(data) is None:
                    raise HTTPException(status_code=422, detail="not a supported image (png, jpg, gif or webp)")
            path = _stored_path(bundle_dir, room_id, sha, ".blob")
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                existing = list(path.parent.glob("*.blob"))
                if len(existing) >= MAX_BLOBS_PER_ROOM or sum(f.stat().st_size for f in existing) + len(data) > MAX_ROOM_BLOB_BYTES:
                    raise HTTPException(status_code=429, detail="this room has reached its image storage limit")
            fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            budget.add(len(data))
            return {"sha256": sha, "size": len(data)}

        return await run_in_threadpool(finish)

    @app.get("/v1/rooms/{room_id}/blobs/{sha}")
    def get_blob(room_id: str, sha: str, request: Request) -> Response:
        room_for(room_id, who(request))
        if not _SHA_RE.match(sha):
            raise HTTPException(status_code=422, detail="image id must be a lowercase sha-256")
        path = _stored_path(bundle_dir, room_id, sha, ".blob")
        try:
            fresh = path.stat().st_mtime >= clock() - retention_days * 86400
        except OSError:
            raise HTTPException(status_code=404, detail="no such image")
        if not fresh:
            path.unlink(missing_ok=True)
            raise HTTPException(status_code=404, detail="no such image")
        size = path.stat().st_size
        if not budget.allows(size):
            raise over_budget(size)
        budget.add(size)
        return Response(content=path.read_bytes(), media_type="application/octet-stream")

    return app


def build_store(settings: ServerSettings, db: Path | None = None):
    if settings.store == "postgres":
        from aletheore.council.pg_store import PostgresRoomStore

        return PostgresRoomStore(settings.dsn, pool_size=settings.pg_pool_size)
    from aletheore.council.sqlite_store import SqliteRoomStore

    return SqliteRoomStore(db or settings.db)


def build_accounts(settings: ServerSettings, store, db: Path | None, tokens: dict[str, str]) -> AccountService:
    """The account service over the same database as the rooms. Names in the token file stay reserved."""
    if settings.store == "postgres":
        from aletheore.council.accounts import PostgresAccountBackend

        return AccountService(PostgresAccountBackend(store), reserved=set(tokens.values()))
    from aletheore.council.accounts import SqliteAccountBackend

    return AccountService(SqliteAccountBackend(db or settings.db), reserved=set(tokens.values()))


def run_server(db: Path, bundle_dir: Path, tokens: dict[str, str], retention_days: int, host: str, port: int,
               settings: ServerSettings | None = None, host_identity=None) -> None:
    import uvicorn

    settings = settings or ServerSettings()
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))  # one JSON object per line
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    store = build_store(settings, db)
    accounts = build_accounts(settings, store, db, tokens) if settings.accounts else None
    app = create_app(store, tokens, bundle_dir, retention_days, settings=settings, accounts=accounts, host_identity=host_identity)
    from aletheore.council.housekeeping import run_sweep, sweep_loop

    try:
        log.info(json.dumps({"event": "sweep", **run_sweep(store, accounts, bundle_dir, settings)}))  # once at start, then on a timer
    except Exception as exc:  # noqa: BLE001
        log.info(json.dumps({"event": "sweep_failed", "error": type(exc).__name__}))
    threading.Thread(target=sweep_loop, args=(store, accounts, bundle_dir, settings, threading.Event()),
                     kwargs={"on_result": lambda r: log.info(json.dumps({"event": "sweep", **r})),
                             "on_error": lambda e: log.info(json.dumps({"event": "sweep_failed", "error": type(e).__name__}))},
                     daemon=True, name="council-housekeeping").start()
    # Client addresses come from our own X-Forwarded-For handling (trusted proxies only), not uvicorn's.
    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False, proxy_headers=False,
                server_header=False, timeout_graceful_shutdown=20, limit_concurrency=settings.max_connections,
                timeout_keep_alive=15)  # a connection that sits idle is closed; too many at once get a plain 503
