"""The HTTP face of the tunnel registry. Hosts prove who they are by signing each request with their device key."""

from __future__ import annotations

import hashlib
import hmac
import html
import time
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import crypto
from .identity import PublicIdentity
from .server_config import RateLimiter, client_ip
from .sponsor import SPONSOR
from .tunnel_registry import Registry, RegistryError

SIGNATURE_WINDOW_S = 60


def signing_message(method: str, path: str, stamp: str, nonce: str, body: bytes) -> bytes:
    return f"council-registry-request-v1\n{method}\n{path}\n{stamp}\n{nonce}\n{hashlib.sha256(body).hexdigest()}".encode()


def create_registry_app(registry: Registry, clock: Callable[[], float] = time.time, admin_token: str = "",
                        trusted_proxies: list[str] | None = None) -> FastAPI:
    app = FastAPI(title="Aletheore Council tunnel registry", docs_url=None, redoc_url=None, openapi_url=None)
    claims = RateLimiter(6, 6, clock)  # per host device: a host asking for a slot more than this is looping
    looks = RateLimiter(120, 60, clock)  # per address, for the public lookup
    trusted = trusted_proxies or []

    def address(request: Request) -> str:
        """The caller's address. Behind our own tunnel sidecar the peer is always that sidecar, so its client-address header is
        believed, but only when the direct peer is in the trusted list."""
        peer = request.client.host if request.client else "unknown"
        return client_ip(peer, request.headers.get("cf-connecting-ip"), trusted)

    seen: dict[str, float] = {}  # signatures already used, so a captured request cannot be replayed inside the window

    def authenticate(request: Request, body: bytes) -> PublicIdentity:
        try:
            sign_hex, box_hex = request.headers["x-council-key"].split(".")
            public = PublicIdentity(bytes.fromhex(sign_hex), bytes.fromhex(box_hex))
            stamp, nonce, signature = request.headers["x-council-time"], request.headers["x-council-nonce"], request.headers["x-council-sig"]
            when = float(stamp)
            sig_bytes = bytes.fromhex(signature)
        except (KeyError, ValueError):
            raise HTTPException(status_code=401, detail="sign the request with this device's key")
        if abs(clock() - when) > SIGNATURE_WINDOW_S:
            raise HTTPException(status_code=401, detail="the request's time is too far from the registry's clock",
                                headers={"X-Registry-Time": f"{clock():.3f}"})  # lets a client with a skewed clock correct itself
        path = request.url.path
        if not crypto.verify(public.sign_vk, signing_message(request.method, path, stamp, nonce, body), sig_bytes):
            raise HTTPException(status_code=401, detail="the request's signature does not match its device key")
        now = clock()
        for k in [k for k, t in seen.items() if t < now - 2 * SIGNATURE_WINDOW_S]:
            seen.pop(k, None)
        if signature in seen:
            raise HTTPException(status_code=401, detail="this request was already used")
        seen[signature] = now
        return public

    def refuse(exc: RegistryError) -> HTTPException:
        return HTTPException(status_code=exc.status, detail=str(exc))

    @app.post("/v1/claim")
    async def claim(request: Request) -> dict:
        public = authenticate(request, await request.body())
        if not claims.allow(public.device_id):
            raise HTTPException(status_code=429, detail="too many requests from this device; wait a minute")
        c = registry.claim(public)
        out = {"status": c.status, "message": c.message}
        if c.status == "leased":
            out.update(slot=c.slot, hostname=c.hostname, tunnel_id=c.tunnel_id, token=c.token, lease_expires=c.lease_expires)
        if c.status == "queued":
            out["position"] = c.position
        out["counter"] = registry.counter()
        return out

    @app.post("/v1/heartbeat")
    async def heartbeat(request: Request) -> dict:
        public = authenticate(request, await request.body())
        try:
            return {"lease_expires": registry.heartbeat(public.device_id)}
        except RegistryError as exc:
            raise refuse(exc)

    @app.post("/v1/release")
    async def release(request: Request) -> dict:
        public = authenticate(request, await request.body())
        return {"released": registry.release(public.device_id)}

    @app.get("/v1/lookup/{host}")
    def lookup(host: str, request: Request) -> dict:
        if not looks.allow(address(request)):
            raise HTTPException(status_code=429, detail="slow down")
        try:
            return registry.lookup(host)
        except RegistryError as exc:
            raise refuse(exc)

    @app.get("/v1/status")
    def status() -> dict:
        return {**registry.counter(), "sponsor": SPONSOR}

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        c = registry.counter()
        return (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                f"<title>Aletheore Council tunnels</title><body style='font:16px system-ui;max-width:34rem;margin:3rem auto;padding:0 1rem'>"
                f"<h1>Free tunnels</h1><p><strong>free slots in use: {c['in_use']} / {c['total']}</strong>"
                f"{'' if not c['queued'] else f' ({c[chr(113)+chr(117)+chr(101)+chr(117)+chr(101)+chr(100)]} waiting)'}</p>"
                f"<p>{html.escape(SPONSOR)}</p></body>")

    @app.post("/v1/admin/ban/{host}")
    async def ban(host: str, request: Request) -> dict:
        given = request.headers.get("authorization", "").removeprefix("Bearer ").encode()
        if not admin_token or not hmac.compare_digest(given, admin_token.encode()):
            raise HTTPException(status_code=403, detail="admin only")
        registry.ban(host)
        return {"banned": host}

    return app
