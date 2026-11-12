"""RemoteRoomStore: a RoomStore (and BundleStore) that talks to a Council room server."""

from __future__ import annotations

import random
import time
from pathlib import Path

import httpx

from aletheore.council.bundle_store import check_ids
from aletheore.council.protocol import Event, EventDraft, ProtocolError, event_from_json
from aletheore.council.store import RoomInfo

ATTEMPTS = 3
MAX_BACKOFF_S = 5.0  # no single pause between tries is longer than this
MAX_RETRY_AFTER_S = 10.0  # never sleep longer than this for one rate-limit answer


class TransferBudgetError(ConnectionError):
    """The host's daily file allowance is used up. A ConnectionError on purpose: the work is held and sent again later, nothing is lost."""


class RemoteRoomStore:
    # An agent's tool process cannot open this store, and must not hold the server token: Room reaches it for agents through a
    # localhost broker that holds the store and hands the agent a short-lived token of its own (broker.py).
    supports_agent_tools = False

    def __init__(self, base_url: str, token: str = "", client: httpx.Client | None = None, backoff_s: float = 0.2, timeout_s: float = 30.0) -> None:  # nosec B107 - empty means no token configured
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout_s)
        # No token (a device that has not signed in yet: registering, or asking to join) means NO Authorization header. A bare "Bearer " is an
        # illegal header value on a real connection, though a test client lets it through, so this broke every first-time register and join.
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._backoff_s = backoff_s
        self._identity = None  # set by login(): lets a request that finds the session expired sign in again once

    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            return self._send(method, path, **kw)
        except PermissionError:
            if self._identity is None or path.startswith("/v1/login"):
                raise
            self.login(self._identity)  # the session ended (or the server restarted): sign in again, once
            return self._send(method, path, **kw)

    def register(self, code: str, handle: str, identity) -> dict:
        """Use a one-time registration code to make this device an account on the server (no session needed)."""
        pub = identity.public()
        return self._send("POST", "/v1/register", json={"code": code, "handle": handle, "sign_vk": pub.sign_vk.hex(),
                                                        "box_pk": pub.box_pk.hex()}).json()

    def login(self, identity) -> str:
        """Challenge and response with this device's key. Afterwards every request carries the new session. Returns the handle."""
        from aletheore.council.accounts import AccountService

        nonce = self._send("POST", "/v1/login/challenge", json={"device_id": identity.device_id}).json()["nonce"]
        signature = identity.sign(AccountService.login_message(identity.device_id, nonce))
        done = self._send("POST", "/v1/login", json={"device_id": identity.device_id, "nonce": nonce, "signature": signature.hex()}).json()
        self._headers = {"Authorization": f"Bearer {done['token']}"}
        self._identity = identity
        return done["participant"]

    # -- invitations ----------------------------------------------------------------
    def create_invite(self, room_id: str, wrapped: bytes, ttl_seconds: int, claim_verifier: bytes = b"") -> dict:
        body = {"wrapped_room_key": wrapped.hex(), "ttl_seconds": ttl_seconds}
        if claim_verifier:
            body["claim_verifier"] = claim_verifier.hex()
        return self._request("POST", f"/v1/rooms/{room_id}/invites", json=body).json()

    def get_invite(self, invite_id: str) -> dict:
        return self._request("GET", f"/v1/invites/{invite_id}").json()

    def request_join(self, invite_id: str, handle: str, identity, claim_key: bytes = b"") -> dict:
        pub = identity.public()
        body = {"handle": handle, "sign_vk": pub.sign_vk.hex(), "box_pk": pub.box_pk.hex()}
        if claim_key:
            body["claim_key"] = claim_key.hex()
        return self._request("POST", f"/v1/invites/{invite_id}/requests", json=body).json()

    def join_status(self, request_id: str) -> dict:
        return self._request("GET", f"/v1/join-requests/{request_id}").json()

    def my_devices(self) -> dict:
        return self._request("GET", "/v1/devices").json()

    def approve_link(self, request_id: str, words: str, grants: bytes = b"") -> dict:
        return self._request("POST", f"/v1/devices/link-requests/{request_id}/approve", json={"words": words, "grants": grants.hex()}).json()

    def revoke_device(self, device_id: str) -> dict:
        return self._request("POST", f"/v1/devices/{device_id}/revoke").json()

    def list_join_requests(self, room_id: str) -> list[dict]:
        return self._request("GET", f"/v1/rooms/{room_id}/join-requests").json()["requests"]

    def approve_join(self, room_id: str, request_id: str) -> dict:
        return self._request("POST", f"/v1/rooms/{room_id}/join-requests/{request_id}/approve").json()

    def reject_join(self, room_id: str, request_id: str) -> dict:
        return self._request("POST", f"/v1/rooms/{room_id}/join-requests/{request_id}/reject").json()

    def _send(self, method: str, path: str, **kw) -> httpx.Response:
        last: Exception | None = None
        for attempt in range(ATTEMPTS):
            try:
                resp = self._client.request(method, path, headers=self._headers, **kw)
            except httpx.TransportError as exc:
                last = exc
            else:
                if resp.status_code == 429:  # the server is rate-limiting us: wait as long as it asks (within reason), then retry
                    last = ConnectionError("rate limited by the server")
                    try:
                        wait = min(float(resp.headers.get("retry-after", self._backoff_s)), MAX_RETRY_AFTER_S)
                    except ValueError:
                        wait = self._backoff_s
                    if attempt < ATTEMPTS - 1:
                        time.sleep(wait)
                    continue
                if resp.status_code < 500 or resp.status_code in (507, 509):  # 509: the daily file allowance, a plain answer, not an outage
                    return self._guard_json(self._check(resp))
                last = ConnectionError(f"server error {resp.status_code}")
            if attempt < ATTEMPTS - 1:
                # full jitter: anywhere from nothing to the (capped) exponential step, so clients that failed together do not retry together
                time.sleep(random.uniform(0, min(MAX_BACKOFF_S, self._backoff_s * (2 ** attempt))))  # nosec B311 - retry jitter, not a secret
        if isinstance(last, ConnectionError) and "rate limited" in str(last):
            raise ConnectionError("the room server's rate limit was reached for this client; slow down and try again shortly") from last
        raise ConnectionError(f"room server unreachable: {last}") from last

    @staticmethod
    def _guard_json(resp: httpx.Response) -> httpx.Response:
        """A server (or something pretending to be one) may send JSON that is broken or nested past the parser's limit. Every caller reads
        `.json()`; make that one failure a ConnectionError instead of an exception nobody expects. Parsed once, then remembered."""
        original, parsed = resp.json, []

        def safe_json(**kw):
            if not parsed:
                try:
                    parsed.append(original(**kw))
                except (ValueError, RecursionError):
                    raise ConnectionError("the server sent an answer that is not valid JSON") from None
            return parsed[0]

        resp.json = safe_json  # type: ignore[method-assign]
        return resp

    @staticmethod
    def _check(resp: httpx.Response) -> httpx.Response:
        code = resp.status_code
        if code < 400:
            return resp
        detail = ""
        try:
            detail = str(resp.json().get("detail", ""))
        except Exception:  # noqa: BLE001  # nosec B110 - best effort: a failure here is expected and must not stop this path
            pass
        if code in (401, 403):
            raise PermissionError(detail or "not permitted")
        if code == 404:
            raise KeyError(detail or "not found")
        if code == 413:
            raise ValueError(detail or "too large")
        if code == 507:
            raise ConnectionError(detail or "the server's disk is full")  # held by the outbox and sent again once there is space
        if code == 509:
            raise TransferBudgetError(detail or "the host's daily file transfer allowance is used up")
        if code in (409, 410, 422):
            raise ProtocolError(detail or "rejected")
        raise ConnectionError(f"unexpected status {code}")

    # -- RoomStore -----------------------------------------------------------------
    def create_room(self, room_id: str, host_participant: str, title: str = "", mode: str = "interactive", crypto: str = "none") -> None:
        self._request("POST", f"/v1/rooms/{room_id}", json={"host": host_participant, "title": title, "mode": mode, "crypto": crypto})

    @staticmethod
    def _info(d: dict) -> RoomInfo:
        return RoomInfo(d["room_id"], d["host"], d.get("title", ""), d.get("mode", "interactive"), d.get("status", "open"),
                        list(d.get("members", [])), int(d.get("last_seq", 0)), d.get("last_activity", ""), d.get("crypto", "none"))

    def room_info(self, room_id: str) -> RoomInfo:
        return self._info(self._request("GET", f"/v1/rooms/{room_id}").json())

    def members_of(self, room_id: str) -> list[str]:
        return self.room_info(room_id).members

    def add_member(self, room_id: str, participant: str) -> None:
        self._request("POST", f"/v1/rooms/{room_id}/members", json={"participant": participant})

    def remove_member(self, room_id: str, participant: str) -> None:
        self._request("DELETE", f"/v1/rooms/{room_id}/members/{participant}")

    def put_grant(self, room_id: str, participant: str, epoch: int, device_id: str, grant: bytes) -> None:
        self._request("PUT", f"/v1/rooms/{room_id}/grants/{participant}/{epoch}", json={"device_id": device_id, "grant": grant.hex()})

    def my_grants(self, room_id: str) -> list[tuple[int, bytes]]:
        """The grants the server holds for this signed-in device in this room."""
        return [(g["epoch"], bytes.fromhex(g["grant"])) for g in self._request("GET", f"/v1/rooms/{room_id}/grants/me").json()["grants"]]

    def close_room(self, room_id: str) -> None:
        self._request("POST", f"/v1/rooms/{room_id}/close")

    def list_rooms(self, participant: str = "") -> list[RoomInfo]:
        return [self._info(d) for d in self._request("GET", "/v1/rooms").json()["rooms"]]

    def room_devices(self, room_id: str) -> dict:
        """The room members' device public keys, as the server knows them: {participant: [PublicIdentity, ...]}."""
        from aletheore.council.identity import PublicIdentity

        raw = self._request("GET", f"/v1/rooms/{room_id}/devices").json()["devices"]
        return {who: [PublicIdentity(bytes.fromhex(d["sign_vk"]), bytes.fromhex(d["box_pk"])) for d in devs] for who, devs in raw.items()}

    def whoami(self) -> str:
        return self._request("GET", "/v1/whoami").json()["participant"]

    def host_of(self, room_id: str) -> str:
        return self._request("GET", f"/v1/rooms/{room_id}/host").json()["host"]

    def append(self, draft: EventDraft) -> Event:
        payload = {
            "type": draft.type.value, "client_event_id": draft.client_event_id, "thread_id": draft.thread_id,
            "reply_to": draft.reply_to, "sender_participant": draft.sender_participant,
            "sender_agent": draft.sender_agent, "body": draft.body,
        }
        return event_from_json(self._request("POST", f"/v1/rooms/{draft.room_id}/events", json=payload).json())

    def read_after(self, room_id: str, after_seq: int, limit: int = 500) -> list[Event]:
        resp = self._request("GET", f"/v1/rooms/{room_id}/events", params={"after_seq": after_seq, "limit": min(limit, 500)})
        return [event_from_json(d) for d in resp.json()["events"]]

    def read_archive(self, room_id: str):
        """The room's archived (older than the horizon) events, oldest first."""
        import json as _json

        resp = self._request("GET", f"/v1/rooms/{room_id}/archive")
        for line in resp.text.splitlines():
            if line.strip():
                try:
                    yield event_from_json(_json.loads(line))
                except (ValueError, RecursionError):  # a hostile or damaged archive line: a plain connection error, not a crash
                    raise ConnectionError("the server sent an archive line that is not valid JSON") from None

    def last_seq(self, room_id: str) -> int:
        return self._request("GET", f"/v1/rooms/{room_id}/last_seq").json()["last_seq"]

    # -- BundleStore ---------------------------------------------------------------
    def transfer_budget(self) -> dict:
        return self._request("GET", "/v1/transfer-budget").json()

    def put_bundle(self, room_id: str, sha256: str, path: Path) -> None:
        check_ids(room_id, sha256)
        self._request("PUT", f"/v1/rooms/{room_id}/bundles/{sha256}", content=Path(path).read_bytes())

    def get_bundle(self, room_id: str, sha256: str, dest: Path) -> None:
        check_ids(room_id, sha256)
        data = self._request("GET", f"/v1/rooms/{room_id}/bundles/{sha256}").content
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(data)

    def put_blob(self, room_id: str, sha256: str, path: Path) -> None:
        check_ids(room_id, sha256)
        self._request("PUT", f"/v1/rooms/{room_id}/blobs/{sha256}", content=Path(path).read_bytes())

    def get_blob(self, room_id: str, sha256: str, dest: Path) -> None:
        check_ids(room_id, sha256)
        data = self._request("GET", f"/v1/rooms/{room_id}/blobs/{sha256}").content
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(data)
