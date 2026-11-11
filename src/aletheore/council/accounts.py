"""Accounts, devices and signed login for a Council room server.

* An **account** is a handle (the `sender_participant` in events) plus `can_host`. It has one or more **devices**; each device
  is one libsodium identity (Task 4) and is never shared between accounts.
* A person **registers** with a one-time code the server's admin made for their handle (`council admin add-host`). The code is
  shown once and only its SHA-256 is stored; it works once, only for that handle, and expires.
* **Login** is challenge and response: the server hands out a fresh single-use nonce, the device signs it, the server checks the
  signature against the device's registered key and returns a session token (32 random bytes, shown once, only its SHA-256 is
  stored, with an expiry). No password exists anywhere.
* A **second device** of the same person is added by approval from a device that is already in the account, after both screens show
  the same four safety words. The server only stores the opaque key grants the first device wraps for the new one; it cannot read them.
* A **revoked** device cannot log in and its sessions stop working at once on this server process (a different process notices
  within `SESSION_CACHE_S`).

All the rules live in `AccountService`. Storage is a small backend (SQLite here, Postgres below) so both behave the same way.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Protocol

from aletheore.council import crypto
from aletheore.council.identity import PublicIdentity, safety_words

HANDLE_RE = re.compile(r"[a-z0-9][a-z0-9_-]{2,31}", re.ASCII)
CODE_TTL_S = 15 * 60
CHALLENGE_TTL_S = 60
LINK_TTL_S = 15 * 60
SESSION_TTL_S = 12 * 3600
MAX_PENDING_LINKS = 5
MAX_GRANTS_BYTES = 64 * 1024
SESSION_CACHE_S = 5.0
MAX_INVITE_TTL_S = 7 * 86400
MIN_INVITE_TTL_S = 30
MAX_WRAPPED_BYTES = 8192
MAX_GRANT_BYTES = 8192
MAX_OPEN_INVITES_PER_ROOM = 20


class AccountError(Exception):
    """A refused account operation. `status` is the HTTP status the server answers with."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Account:
    handle: str
    can_host: bool


@dataclass(frozen=True)
class Device:
    device_id: str
    handle: str
    sign_vk: bytes
    box_pk: bytes
    created_at: float
    revoked_at: float | None = None

    @property
    def active(self) -> bool:
        return self.revoked_at is None


@dataclass(frozen=True)
class LinkRequest:
    request_id: str
    handle: str
    device_id: str
    words: str
    status: str  # pending | approved | expired
    grants: bytes = b""


@dataclass(frozen=True)
class InviteInfo:
    invite_id: str
    room_id: str
    title: str
    wrapped: bytes
    expires_at: float
    status: str  # open | claimed | expired
    host: Device | None  # the device that made the invite (None for a stage 2 token host)


@dataclass(frozen=True)
class JoinRequest:
    request_id: str
    invite_id: str
    room_id: str
    handle: str
    device_id: str
    words: str
    status: str  # pending | approved | rejected
    created_at: float = 0.0


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _device_row(p: PublicIdentity, handle: str, now: float) -> dict:
    return {"device_id": p.device_id, "handle": handle, "sign_vk": p.sign_vk.hex(), "box_pk": p.box_pk.hex(), "created_at": now}


def _device_from(row: tuple) -> Device:
    return Device(device_id=row[0], handle=row[1], sign_vk=bytes.fromhex(row[2]), box_pk=bytes.fromhex(row[3]),
                  created_at=row[4], revoked_at=row[5])


class AccountBackend(Protocol):
    """Storage. Each method that must be all-or-nothing (redeeming a code, approving a link, using a nonce) is one atomic call."""

    def put_code(self, code_hash: str, handle: str, can_host: bool, expires_at: float) -> None: ...
    def redeem_code(self, code_hash: str, handle: str, device: dict, now: float) -> bool: ...  # returns can_host
    def handle_exists(self, handle: str) -> bool: ...
    def account_can_host(self, handle: str) -> bool | None: ...
    def get_device(self, device_id: str) -> Device | None: ...
    def list_devices(self, handle: str) -> list[Device]: ...
    def revoke_device(self, device_id: str, now: float) -> bool: ...
    def put_nonce(self, nonce: str, device_id: str, expires_at: float, now: float) -> None: ...
    def take_nonce(self, nonce: str, device_id: str, now: float) -> bool: ...
    def put_session(self, token_hash: str, device_id: str, expires_at: float) -> None: ...
    def get_session(self, token_hash: str, now: float) -> tuple[str, str, float] | None: ...  # handle, device_id, expires_at
    def put_link(self, request_id: str, handle: str, device: dict, words: str, expires_at: float) -> None: ...
    def count_pending_links(self, handle: str, now: float) -> int: ...
    def list_pending_links(self, handle: str, now: float) -> list[LinkRequest]: ...
    def get_link(self, request_id: str, now: float) -> LinkRequest | None: ...
    def approve_link(self, request_id: str, approver_handle: str, now: float, grants: bytes) -> Device: ...
    def put_invite(self, invite: dict, max_open: int, now: float) -> None: ...
    def get_invite(self, invite_id: str, now: float) -> InviteInfo | None: ...
    def claim_invite(self, invite_id: str, request: dict, now: float) -> None: ...
    def get_join_request(self, request_id: str) -> JoinRequest | None: ...
    def list_join_requests(self, room_id: str) -> list[JoinRequest]: ...
    def decide_join(self, room_id: str, request_id: str, approve: bool, now: float) -> JoinRequest: ...
    def put_grant(self, room_id: str, epoch: int, device_id: str, grant: bytes, now: float) -> None: ...
    def list_grants(self, room_id: str, device_id: str) -> list[tuple[int, bytes]]: ...
    def delete_room_data(self, room_id: str) -> dict: ...
    def purge_expired(self, now: float, invite_grace: float, link_grace: float) -> dict: ...


class AccountService:
    def __init__(self, backend: AccountBackend, clock: Callable[[], float] = time.time, session_ttl: float = SESSION_TTL_S,
                 challenge_ttl: float = CHALLENGE_TTL_S, reserved: Iterable[str] = ()) -> None:
        self.backend, self.clock = backend, clock
        self.session_ttl, self.challenge_ttl = session_ttl, challenge_ttl
        self.reserved = frozenset(reserved)  # participants that exist outside the account system (the stage 2 token file)
        self._cache: dict[str, tuple[tuple[str, str], float]] = {}

    # -- registration ------------------------------------------------------------------------------------------------
    def _check_handle(self, handle: str) -> None:
        if not isinstance(handle, str) or not HANDLE_RE.fullmatch(handle):
            raise AccountError("a handle is 3 to 32 lowercase letters, digits, '-' or '_', starting with a letter or digit")
        if handle in self.reserved or handle == "council-server" or self.backend.handle_exists(handle):  # "council-server" signs the server's own notices
            raise AccountError("that handle is taken", 409)

    def create_registration_code(self, handle: str, can_host: bool = False, ttl: float = CODE_TTL_S) -> str:
        self._check_handle(handle)
        code = "cr_" + secrets.token_hex(20)
        self.backend.put_code(_sha(code), handle, bool(can_host), self.clock() + ttl)
        return code

    def redeem_registration_code(self, code: str, public: PublicIdentity, handle: str) -> Account:
        if not isinstance(code, str) or not isinstance(handle, str):
            raise AccountError("code and handle are required")
        if handle in self.reserved:
            raise AccountError("that handle is taken", 409)
        can_host = self.backend.redeem_code(_sha(code), handle, _device_row(public, handle, self.clock()), self.clock())
        return Account(handle, can_host)

    # -- login -------------------------------------------------------------------------------------------------------
    @staticmethod
    def login_message(device_id: str, nonce: str) -> bytes:
        return f"council/login/v1\n{device_id}\n{nonce}".encode("utf-8")

    def _active_device(self, device_id: str) -> Device:
        device = self.backend.get_device(device_id) if isinstance(device_id, str) else None
        if device is None or not device.active:
            raise AccountError("unknown or revoked device", 401)
        return device

    def login_challenge(self, device_id: str) -> str:
        self._active_device(device_id)
        nonce = secrets.token_hex(32)
        now = self.clock()
        self.backend.put_nonce(nonce, device_id, now + self.challenge_ttl, now)
        return nonce

    def login(self, device_id: str, nonce: str, signature: bytes) -> tuple[str, float]:
        device = self._active_device(device_id)
        # The nonce is spent before the signature is looked at: one challenge allows one attempt.
        if not isinstance(nonce, str) or not self.backend.take_nonce(nonce, device_id, self.clock()):
            raise AccountError("the challenge expired or was already used; ask for a new one", 401)
        if not crypto.verify(device.sign_vk, self.login_message(device_id, nonce), signature):
            raise AccountError("bad signature", 401)
        token = secrets.token_urlsafe(32)
        expires = self.clock() + self.session_ttl
        self.backend.put_session(_sha(token), device_id, expires)
        return token, expires

    def session_participant(self, token: str) -> tuple[str, str] | None:
        if not token or not isinstance(token, str):
            return None
        key, now = _sha(token), self.clock()
        hit = self._cache.get(key)
        if hit is not None and hit[1] > now:
            return hit[0]
        found = self.backend.get_session(key, now)
        if found is None:
            self._cache.pop(key, None)
            return None
        handle, device_id, expires = found
        if len(self._cache) > 10_000:
            self._cache.clear()
        self._cache[key] = ((handle, device_id), min(now + SESSION_CACHE_S, expires))
        return handle, device_id

    def revoke_device(self, device_id: str) -> None:
        if not self.backend.revoke_device(device_id, self.clock()):
            raise AccountError("no such device", 404)
        self._cache.clear()  # this process stops honouring the device's sessions immediately

    # -- lookups -----------------------------------------------------------------------------------------------------
    def get_device(self, device_id: str) -> Device | None:
        return self.backend.get_device(device_id)

    def list_devices(self, handle: str) -> list[Device]:
        return self.backend.list_devices(handle)

    def can_host(self, handle: str) -> bool:
        return bool(self.backend.account_can_host(handle))

    def handle_exists(self, handle: str) -> bool:
        return self.backend.handle_exists(handle)

    # -- linking a second device -------------------------------------------------------------------------------------
    def create_link_request(self, handle: str, public: PublicIdentity) -> LinkRequest:
        now = self.clock()
        if self.backend.account_can_host(handle) is None:
            raise AccountError("no such account", 404)
        if self.backend.get_device(public.device_id) is not None:
            raise AccountError("this device already belongs to an account", 409)
        if self.backend.count_pending_links(handle, now) >= MAX_PENDING_LINKS:
            raise AccountError("too many pending link requests for this account; try again later", 429)
        request_id, words = "lr_" + secrets.token_hex(16), safety_words(public.fingerprint)
        self.backend.put_link(request_id, handle, _device_row(public, handle, now), words, now + LINK_TTL_S)
        return LinkRequest(request_id, handle, public.device_id, words, "pending")

    def pending_links(self, handle: str) -> list[LinkRequest]:
        return self.backend.list_pending_links(handle, self.clock())

    def get_link_request(self, request_id: str) -> LinkRequest | None:
        return self.backend.get_link(request_id, self.clock()) if isinstance(request_id, str) else None

    def approve_link_request(self, approver_device_id: str, request_id: str, words: str, grants: bytes = b"") -> Device:
        approver = self.backend.get_device(approver_device_id)
        if approver is None or not approver.active:
            raise AccountError("only an active device of the account can approve", 403)
        req = self.get_link_request(request_id)
        if req is None:
            raise AccountError("no such link request", 404)
        if req.handle != approver.handle:
            raise AccountError("that request is for a different account", 403)
        if req.status != "pending":
            raise AccountError(f"this link request is {req.status}", 409)
        if not isinstance(words, str) or not hmac.compare_digest(words.strip().lower(), req.words):
            raise AccountError("the safety words do not match: check the other screen", 422)
        if len(grants) > MAX_GRANTS_BYTES:
            raise AccountError("grants too large", 413)
        return self.backend.approve_link(request_id, approver.handle, self.clock(), bytes(grants))

    # -- invitations -------------------------------------------------------------------------------------------------
    def create_invite(self, room_id: str, host_device_id: str, wrapped: bytes, ttl: float, title: str = "",
                      claim_verifier: bytes = b"") -> tuple[str, float]:
        if not MIN_INVITE_TTL_S <= ttl <= MAX_INVITE_TTL_S:
            raise AccountError(f"an invite lasts between {MIN_INVITE_TTL_S} seconds and {MAX_INVITE_TTL_S // 86400} days")
        if not 1 <= len(wrapped) <= MAX_WRAPPED_BYTES:
            raise AccountError("the wrapped key is the wrong size", 413)
        if len(claim_verifier) not in (0, 32):
            raise AccountError("the claim verifier is the wrong size", 422)
        now = self.clock()
        invite = {"invite_id": "inv_" + secrets.token_hex(16), "room_id": room_id, "host_device_id": host_device_id,
                  "wrapped": bytes(wrapped), "title": title, "expires_at": now + ttl, "created_at": now, "claim_verifier": bytes(claim_verifier)}
        self.backend.put_invite(invite, MAX_OPEN_INVITES_PER_ROOM, now)
        return invite["invite_id"], invite["expires_at"]

    def get_invite(self, invite_id: str) -> InviteInfo | None:
        return self.backend.get_invite(invite_id, self.clock()) if isinstance(invite_id, str) and len(invite_id) <= 64 else None

    def claim_invite(self, invite_id: str, handle: str, public: PublicIdentity, claim_key: bytes = b"") -> JoinRequest:
        """A guest asks to join. Single use: the invite is spent by the claim, whatever the host later decides. An invitation made with a claim
        verifier (every one Council makes) is claimed only by someone holding the link's secret: `claim_key` is checked inside the same
        transaction that spends the invite, and a wrong or missing key spends nothing."""
        invite = self.get_invite(invite_id)
        if invite is None:
            raise AccountError("no such invite", 404)
        if invite.status != "open":
            raise AccountError(f"this invite is {invite.status}; ask the host for a new one", 410)
        existing = self.backend.get_device(public.device_id)
        if existing is not None:
            if existing.handle != handle or not existing.active:
                raise AccountError("this device already belongs to a different account", 409)
        else:
            self._check_handle(handle)  # a new device needs a free handle (an existing person adds a device with a link request instead)
        now = self.clock()
        request = {"request_id": "jr_" + secrets.token_hex(16), "invite_id": invite_id, "room_id": invite.room_id, "handle": handle,
                   "device": _device_row(public, handle, now), "words": safety_words(public.fingerprint), "created_at": now,
                   "claim_key": bytes(claim_key)}
        self.backend.claim_invite(invite_id, request, now)
        return JoinRequest(request["request_id"], invite_id, invite.room_id, handle, public.device_id, request["words"], "pending", now)

    def get_join_request(self, request_id: str) -> JoinRequest | None:
        return self.backend.get_join_request(request_id) if isinstance(request_id, str) and len(request_id) <= 64 else None

    def list_join_requests(self, room_id: str) -> list[JoinRequest]:
        return self.backend.list_join_requests(room_id)

    def approve_join(self, room_id: str, request_id: str) -> JoinRequest:
        """Registers the guest (a new account that may join but not host, unless they already have one) and marks the request
        approved. The caller then adds the member to the room. Approving twice is harmless."""
        return self.backend.decide_join(room_id, request_id, True, self.clock())

    def reject_join(self, room_id: str, request_id: str) -> JoinRequest:
        return self.backend.decide_join(room_id, request_id, False, self.clock())

    # -- room key grants (opaque to the server) ----------------------------------------------------------------------
    def put_grant(self, room_id: str, participant: str, epoch: int, device_id: str, grant: bytes) -> None:
        """Store a grant the host made for one device of a member. The server cannot read it; it only checks who it is for."""
        if isinstance(epoch, bool) or not isinstance(epoch, int) or not 1 <= epoch <= 1_000_000:
            raise AccountError("epoch must be a whole number from 1")
        if not 1 <= len(grant) <= MAX_GRANT_BYTES:
            raise AccountError("the grant is the wrong size", 413)
        device = self.backend.get_device(device_id) if isinstance(device_id, str) else None
        if device is None or not device.active or device.handle != participant:
            raise AccountError("that device does not belong to that member", 403)
        self.backend.put_grant(room_id, epoch, device_id, bytes(grant), self.clock())

    def my_grants(self, room_id: str, device_id: str) -> list[tuple[int, bytes]]:
        return self.backend.list_grants(room_id, device_id)

    # -- housekeeping -------------------------------------------------------------------------------------------------
    INVITE_GRACE_S = 24 * 3600  # an expired invite stays a day, so "this link expired" can be said instead of "no such link"
    LINK_GRACE_S = 3600

    def purge_expired(self) -> dict:
        """Remove what has expired: old login challenges and sessions, link requests and unclaimed invites (with their join requests)."""
        return self.backend.purge_expired(self.clock(), self.INVITE_GRACE_S, self.LINK_GRACE_S)

    def delete_room_data(self, room_id: str) -> dict:
        """Everything the accounts side holds about one room: invites, join requests and key grants."""
        return self.backend.delete_room_data(room_id)


# -- SQLite --------------------------------------------------------------------------------------------------------
_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS reg_codes (code_hash TEXT PRIMARY KEY, handle TEXT NOT NULL, can_host INTEGER NOT NULL,
    expires_at REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS accounts (handle TEXT PRIMARY KEY, can_host INTEGER NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS devices (device_id TEXT PRIMARY KEY, handle TEXT NOT NULL, sign_vk TEXT NOT NULL, box_pk TEXT NOT NULL,
    created_at REAL NOT NULL, revoked_at REAL);
CREATE INDEX IF NOT EXISTS devices_by_handle ON devices (handle);
CREATE TABLE IF NOT EXISTS nonces (nonce TEXT PRIMARY KEY, device_id TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, device_id TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS sessions_by_device ON sessions (device_id);
CREATE TABLE IF NOT EXISTS link_requests (request_id TEXT PRIMARY KEY, handle TEXT NOT NULL, device_id TEXT NOT NULL,
    sign_vk TEXT NOT NULL, box_pk TEXT NOT NULL, words TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
    grants BLOB, expires_at REAL NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS invites (invite_id TEXT PRIMARY KEY, room_id TEXT NOT NULL, host_device_id TEXT NOT NULL,
    wrapped BLOB NOT NULL, title TEXT NOT NULL, expires_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'open', created_at REAL NOT NULL,
    claim_verifier BLOB NOT NULL DEFAULT x'');
CREATE INDEX IF NOT EXISTS invites_by_room ON invites (room_id);
CREATE TABLE IF NOT EXISTS join_requests (request_id TEXT PRIMARY KEY, invite_id TEXT NOT NULL, room_id TEXT NOT NULL,
    handle TEXT NOT NULL, device_id TEXT NOT NULL, sign_vk TEXT NOT NULL, box_pk TEXT NOT NULL, words TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS join_requests_by_room ON join_requests (room_id, status);
CREATE TABLE IF NOT EXISTS grants (room_id TEXT NOT NULL, epoch INTEGER NOT NULL, device_id TEXT NOT NULL, grant_blob BLOB NOT NULL,
    created_at REAL NOT NULL, PRIMARY KEY (room_id, epoch, device_id));
"""
_DEVICE_COLS = "device_id, handle, sign_vk, box_pk, created_at, revoked_at"


_JOIN_COLS = "request_id, invite_id, room_id, handle, device_id, words, status, created_at"


def _join_from(row: tuple) -> JoinRequest:
    return JoinRequest(request_id=row[0], invite_id=row[1], room_id=row[2], handle=row[3], device_id=row[4], words=row[5],
                       status=row[6], created_at=row[7])


def _check_claim(verifier, claim_key: bytes) -> None:
    """An invitation made with a verifier is claimed only with the key that hashes to it. An empty verifier is an older invitation."""
    verifier = bytes(verifier or b"")
    if verifier and not hmac.compare_digest(hashlib.sha256(bytes(claim_key or b"")).digest(), verifier):
        raise AccountError("this invitation link is incomplete or has been altered; copy the whole link, including the part after #", 403)


def _invite_from(row: tuple, now: float, host: Device | None) -> InviteInfo:
    status = "expired" if row[6] == "open" and row[5] < now else row[6]
    return InviteInfo(invite_id=row[0], room_id=row[1], title=row[4], wrapped=bytes(row[3]), expires_at=row[5], status=status, host=host)


class SqliteAccountBackend:
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self._path, timeout=30)) as conn:
            conn.executescript(_SQLITE_SCHEMA)
            if "claim_verifier" not in {r[1] for r in conn.execute("PRAGMA table_info(invites)")}:  # a database made before invitations were claim-proofed
                conn.execute("ALTER TABLE invites ADD COLUMN claim_verifier BLOB NOT NULL DEFAULT x''")
                conn.commit()

    @contextmanager
    def _tx(self):
        conn = sqlite3.connect(self._path, timeout=30, isolation_level=None)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    def _one(self, sql: str, args: tuple = ()):
        with closing(sqlite3.connect(self._path, timeout=30)) as conn:
            return conn.execute(sql, args).fetchone()

    def put_code(self, code_hash, handle, can_host, expires_at):
        with self._tx() as c:
            c.execute("INSERT INTO reg_codes (code_hash, handle, can_host, expires_at) VALUES (?,?,?,?)",
                      (code_hash, handle, int(can_host), expires_at))

    def redeem_code(self, code_hash, handle, device, now):
        with self._tx() as c:
            row = c.execute("SELECT handle, can_host, expires_at, used FROM reg_codes WHERE code_hash=?", (code_hash,)).fetchone()
            if row is None or row[3] or row[2] < now:
                raise AccountError("that registration code is not valid (unknown, used or expired)")
            if row[0] != handle:
                raise AccountError("that registration code is for a different handle")
            if c.execute("SELECT 1 FROM accounts WHERE handle=?", (handle,)).fetchone():
                raise AccountError("that handle is taken", 409)
            if c.execute("SELECT 1 FROM devices WHERE device_id=?", (device["device_id"],)).fetchone():
                raise AccountError("this device already belongs to an account", 409)
            c.execute("UPDATE reg_codes SET used=1 WHERE code_hash=?", (code_hash,))
            c.execute("INSERT INTO accounts (handle, can_host, created_at) VALUES (?,?,?)", (handle, row[1], now))
            c.execute(f"INSERT INTO devices ({_DEVICE_COLS}) VALUES (?,?,?,?,?,NULL)",
                      (device["device_id"], handle, device["sign_vk"], device["box_pk"], device["created_at"]))
            return bool(row[1])

    def handle_exists(self, handle):
        return self._one("SELECT 1 FROM accounts WHERE handle=?", (handle,)) is not None

    def account_can_host(self, handle):
        row = self._one("SELECT can_host FROM accounts WHERE handle=?", (handle,))
        return None if row is None else bool(row[0])

    def get_device(self, device_id):
        row = self._one(f"SELECT {_DEVICE_COLS} FROM devices WHERE device_id=?", (device_id,))
        return _device_from(row) if row else None

    def list_devices(self, handle):
        with closing(sqlite3.connect(self._path, timeout=30)) as conn:
            return [_device_from(r) for r in conn.execute(f"SELECT {_DEVICE_COLS} FROM devices WHERE handle=? ORDER BY created_at, device_id", (handle,))]

    def revoke_device(self, device_id, now):
        with self._tx() as c:
            n = c.execute("UPDATE devices SET revoked_at=COALESCE(revoked_at, ?) WHERE device_id=?", (now, device_id)).rowcount
            c.execute("DELETE FROM sessions WHERE device_id=?", (device_id,))
            return n > 0

    def put_nonce(self, nonce, device_id, expires_at, now):
        with self._tx() as c:
            c.execute("DELETE FROM nonces WHERE expires_at < ?", (now,))
            c.execute("INSERT INTO nonces (nonce, device_id, expires_at) VALUES (?,?,?)", (nonce, device_id, expires_at))

    def take_nonce(self, nonce, device_id, now):
        with self._tx() as c:
            return c.execute("DELETE FROM nonces WHERE nonce=? AND device_id=? AND expires_at >= ?", (nonce, device_id, now)).rowcount == 1

    def put_session(self, token_hash, device_id, expires_at):
        with self._tx() as c:
            c.execute("DELETE FROM sessions WHERE expires_at < ?", (expires_at - 10 * 86400,))
            c.execute("INSERT INTO sessions (token_hash, device_id, expires_at) VALUES (?,?,?)", (token_hash, device_id, expires_at))

    def get_session(self, token_hash, now):
        row = self._one("SELECT d.handle, s.device_id, s.expires_at FROM sessions s JOIN devices d ON d.device_id = s.device_id "
                        "WHERE s.token_hash=? AND s.expires_at > ? AND d.revoked_at IS NULL", (token_hash, now))
        return tuple(row) if row else None

    def delete_room_data(self, room_id):
        with self._tx() as c:
            counts = {"invites": c.execute("DELETE FROM invites WHERE room_id=?", (room_id,)).rowcount,
                      "join_requests": c.execute("DELETE FROM join_requests WHERE room_id=?", (room_id,)).rowcount,
                      "grants": c.execute("DELETE FROM grants WHERE room_id=?", (room_id,)).rowcount}
        return counts

    def purge_expired(self, now, invite_grace, link_grace):
        with self._tx() as c:
            counts = {"nonces": c.execute("DELETE FROM nonces WHERE expires_at < ?", (now,)).rowcount,
                      "sessions": c.execute("DELETE FROM sessions WHERE expires_at < ?", (now,)).rowcount,
                      "link_requests": c.execute("DELETE FROM link_requests WHERE status != 'approved' AND expires_at < ?", (now - link_grace,)).rowcount}
            old = [r[0] for r in c.execute("SELECT invite_id FROM invites WHERE status='open' AND expires_at < ?", (now - invite_grace,))]
            for iid in old:
                c.execute("DELETE FROM join_requests WHERE invite_id=? AND status='pending'", (iid,))
                c.execute("DELETE FROM invites WHERE invite_id=?", (iid,))
            counts["invites"] = len(old)
        return counts

    def put_link(self, request_id, handle, device, words, expires_at):
        with self._tx() as c:
            c.execute("INSERT INTO link_requests (request_id, handle, device_id, sign_vk, box_pk, words, expires_at, created_at) "
                      "VALUES (?,?,?,?,?,?,?,?)", (request_id, handle, device["device_id"], device["sign_vk"], device["box_pk"], words,
                                                    expires_at, device["created_at"]))

    def count_pending_links(self, handle, now):
        return self._one("SELECT COUNT(*) FROM link_requests WHERE handle=? AND status='pending' AND expires_at >= ?", (handle, now))[0]

    def list_pending_links(self, handle, now):
        with closing(sqlite3.connect(self._path, timeout=30)) as conn:
            rows = conn.execute("SELECT request_id, handle, device_id, words FROM link_requests WHERE handle=? AND status='pending' "
                                "AND expires_at >= ? ORDER BY created_at", (handle, now)).fetchall()
        return [LinkRequest(r[0], r[1], r[2], r[3], "pending") for r in rows]

    def get_link(self, request_id, now):
        row = self._one("SELECT request_id, handle, device_id, words, status, grants, expires_at FROM link_requests WHERE request_id=?",
                        (request_id,))
        if row is None:
            return None
        status = "expired" if row[4] == "pending" and row[6] < now else row[4]
        return LinkRequest(row[0], row[1], row[2], row[3], status, bytes(row[5] or b""))

    def approve_link(self, request_id, approver_handle, now, grants):
        with self._tx() as c:
            row = c.execute("SELECT handle, device_id, sign_vk, box_pk, status, expires_at, created_at FROM link_requests "
                            "WHERE request_id=?", (request_id,)).fetchone()
            if row is None or row[4] != "pending" or row[5] < now or row[0] != approver_handle:
                raise AccountError("this link request can no longer be approved", 409)
            if c.execute("SELECT 1 FROM devices WHERE device_id=?", (row[1],)).fetchone():
                raise AccountError("this device already belongs to an account", 409)
            c.execute("UPDATE link_requests SET status='approved', grants=? WHERE request_id=?", (grants, request_id))
            c.execute(f"INSERT INTO devices ({_DEVICE_COLS}) VALUES (?,?,?,?,?,NULL)", (row[1], row[0], row[2], row[3], now))
        return Device(row[1], row[0], bytes.fromhex(row[2]), bytes.fromhex(row[3]), now)

    def put_invite(self, invite, max_open, now):
        with self._tx() as c:
            n = c.execute("SELECT COUNT(*) FROM invites WHERE room_id=? AND status='open' AND expires_at >= ?", (invite["room_id"], now)).fetchone()[0]
            if n >= max_open:
                raise AccountError("this room already has too many open invitations; wait for some to expire", 429)
            c.execute("INSERT INTO invites (invite_id, room_id, host_device_id, wrapped, title, expires_at, created_at, claim_verifier) VALUES (?,?,?,?,?,?,?,?)",
                      (invite["invite_id"], invite["room_id"], invite["host_device_id"], invite["wrapped"], invite["title"],
                       invite["expires_at"], invite["created_at"], invite["claim_verifier"]))

    def get_invite(self, invite_id, now):
        row = self._one("SELECT invite_id, room_id, host_device_id, wrapped, title, expires_at, status FROM invites WHERE invite_id=?", (invite_id,))
        return _invite_from(row, now, self.get_device(row[2]) if row and row[2] else None) if row else None

    def claim_invite(self, invite_id, request, now):
        with self._tx() as c:
            row = c.execute("SELECT status, expires_at, claim_verifier FROM invites WHERE invite_id=?", (invite_id,)).fetchone()
            if row is None:
                raise AccountError("no such invite", 404)
            if row[0] != "open" or row[1] < now:
                raise AccountError("this invite is no longer open; ask the host for a new one", 410)
            _check_claim(row[2], request.get("claim_key", b""))
            d = request["device"]
            c.execute("UPDATE invites SET status='claimed' WHERE invite_id=?", (invite_id,))
            c.execute("INSERT INTO join_requests (request_id, invite_id, room_id, handle, device_id, sign_vk, box_pk, words, created_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?)", (request["request_id"], invite_id, request["room_id"], request["handle"], d["device_id"],
                                                      d["sign_vk"], d["box_pk"], request["words"], request["created_at"]))

    def get_join_request(self, request_id):
        row = self._one(f"SELECT {_JOIN_COLS} FROM join_requests WHERE request_id=?", (request_id,))
        return _join_from(row) if row else None

    def list_join_requests(self, room_id):
        with closing(sqlite3.connect(self._path, timeout=30)) as conn:
            return [_join_from(r) for r in conn.execute(
                f"SELECT {_JOIN_COLS} FROM join_requests WHERE room_id=? AND status='pending' ORDER BY created_at, request_id", (room_id,))]

    def decide_join(self, room_id, request_id, approve, now):
        with self._tx() as c:
            row = c.execute(f"SELECT {_JOIN_COLS}, sign_vk, box_pk FROM join_requests WHERE request_id=?", (request_id,)).fetchone()
            if row is None or row[2] != room_id:
                raise AccountError("no such join request for this room", 404)
            req = _join_from(row[:8])
            if req.status == ("approved" if approve else "rejected"):
                return req  # deciding again the same way is harmless
            if req.status != "pending":
                raise AccountError(f"this request was already {req.status}", 409)
            if approve:
                have = c.execute("SELECT handle FROM devices WHERE device_id=?", (req.device_id,)).fetchone()
                if have is not None:
                    if have[0] != req.handle:
                        raise AccountError("that device now belongs to a different account", 409)
                else:
                    if c.execute("SELECT 1 FROM accounts WHERE handle=?", (req.handle,)).fetchone():
                        raise AccountError("that handle was taken in the meantime; ask the guest to pick another", 409)
                    c.execute("INSERT INTO accounts (handle, can_host, created_at) VALUES (?,0,?)", (req.handle, now))
                    c.execute(f"INSERT INTO devices ({_DEVICE_COLS}) VALUES (?,?,?,?,?,NULL)", (req.device_id, req.handle, row[8], row[9], now))
            c.execute("UPDATE join_requests SET status=? WHERE request_id=?", ("approved" if approve else "rejected", request_id))
            return JoinRequest(req.request_id, req.invite_id, req.room_id, req.handle, req.device_id, req.words,
                               "approved" if approve else "rejected", req.created_at)

    def put_grant(self, room_id, epoch, device_id, grant, now):
        with self._tx() as c:
            c.execute("INSERT INTO grants (room_id, epoch, device_id, grant_blob, created_at) VALUES (?,?,?,?,?) "
                      "ON CONFLICT (room_id, epoch, device_id) DO UPDATE SET grant_blob=excluded.grant_blob, created_at=excluded.created_at",
                      (room_id, epoch, device_id, grant, now))

    def list_grants(self, room_id, device_id):
        with closing(sqlite3.connect(self._path, timeout=30)) as conn:
            return [(r[0], bytes(r[1])) for r in conn.execute(
                "SELECT epoch, grant_blob FROM grants WHERE room_id=? AND device_id=? ORDER BY epoch", (room_id, device_id))]


# -- Postgres (the tables are created by migration 2 in pg_store.py) -------------------------------------------------
class PostgresAccountBackend:
    """Uses the connection pool and schema of an existing PostgresRoomStore, so one database holds rooms and accounts."""

    def __init__(self, store) -> None:
        self._store = store

    def _q(self, sql: str, args: tuple = (), tx: bool = False, many: bool = False):
        with self._store._conn(tx=tx) as conn:  # noqa: SLF001 (same package; shares the pool)
            cur = conn.execute(sql, args)
            if cur.description is None:  # a statement that returns no rows (INSERT, UPDATE)
                return [] if many else None
            return cur.fetchall() if many else cur.fetchone()

    def put_code(self, code_hash, handle, can_host, expires_at):
        self._q("INSERT INTO reg_codes (code_hash, handle, can_host, expires_at) VALUES (%s,%s,%s,%s)", (code_hash, handle, can_host, expires_at))

    def redeem_code(self, code_hash, handle, device, now):
        with self._store._conn(tx=True) as c:  # noqa: SLF001
            row = c.execute("SELECT handle, can_host, expires_at, used FROM reg_codes WHERE code_hash=%s FOR UPDATE", (code_hash,)).fetchone()
            if row is None or row[3] or row[2] < now:
                raise AccountError("that registration code is not valid (unknown, used or expired)")
            if row[0] != handle:
                raise AccountError("that registration code is for a different handle")
            if c.execute("SELECT 1 FROM accounts WHERE handle=%s", (handle,)).fetchone():
                raise AccountError("that handle is taken", 409)
            if c.execute("SELECT 1 FROM devices WHERE device_id=%s", (device["device_id"],)).fetchone():
                raise AccountError("this device already belongs to an account", 409)
            c.execute("UPDATE reg_codes SET used=true WHERE code_hash=%s", (code_hash,))
            c.execute("INSERT INTO accounts (handle, can_host, created_at) VALUES (%s,%s,%s)", (handle, row[1], now))
            c.execute(f"INSERT INTO devices ({_DEVICE_COLS}) VALUES (%s,%s,%s,%s,%s,NULL)",
                      (device["device_id"], handle, device["sign_vk"], device["box_pk"], device["created_at"]))
            return bool(row[1])

    def handle_exists(self, handle):
        return self._q("SELECT 1 FROM accounts WHERE handle=%s", (handle,)) is not None

    def account_can_host(self, handle):
        row = self._q("SELECT can_host FROM accounts WHERE handle=%s", (handle,))
        return None if row is None else bool(row[0])

    def get_device(self, device_id):
        row = self._q(f"SELECT {_DEVICE_COLS} FROM devices WHERE device_id=%s", (device_id,))
        return _device_from(row) if row else None

    def list_devices(self, handle):
        rows = self._q(f"SELECT {_DEVICE_COLS} FROM devices WHERE handle=%s ORDER BY created_at, device_id", (handle,), many=True)
        return [_device_from(r) for r in rows]

    def revoke_device(self, device_id, now):
        with self._store._conn(tx=True) as c:  # noqa: SLF001
            n = c.execute("UPDATE devices SET revoked_at=COALESCE(revoked_at, %s) WHERE device_id=%s", (now, device_id)).rowcount
            c.execute("DELETE FROM sessions WHERE device_id=%s", (device_id,))
            return n > 0

    def put_nonce(self, nonce, device_id, expires_at, now):
        with self._store._conn(tx=True) as c:  # noqa: SLF001
            c.execute("DELETE FROM nonces WHERE expires_at < %s", (now,))
            c.execute("INSERT INTO nonces (nonce, device_id, expires_at) VALUES (%s,%s,%s)", (nonce, device_id, expires_at))

    def take_nonce(self, nonce, device_id, now):
        with self._store._conn() as c:  # noqa: SLF001
            return c.execute("DELETE FROM nonces WHERE nonce=%s AND device_id=%s AND expires_at >= %s", (nonce, device_id, now)).rowcount == 1

    def put_session(self, token_hash, device_id, expires_at):
        self._q("INSERT INTO sessions (token_hash, device_id, expires_at) VALUES (%s,%s,%s)", (token_hash, device_id, expires_at))

    def get_session(self, token_hash, now):
        row = self._q("SELECT d.handle, s.device_id, s.expires_at FROM sessions s JOIN devices d ON d.device_id = s.device_id "
                      "WHERE s.token_hash=%s AND s.expires_at > %s AND d.revoked_at IS NULL", (token_hash, now))
        return tuple(row) if row else None

    def delete_room_data(self, room_id):
        with self._store._conn(tx=True) as c:  # noqa: SLF001
            return {"invites": c.execute("DELETE FROM invites WHERE room_id=%s", (room_id,)).rowcount,
                    "join_requests": c.execute("DELETE FROM join_requests WHERE room_id=%s", (room_id,)).rowcount,
                    "grants": c.execute("DELETE FROM grants WHERE room_id=%s", (room_id,)).rowcount}

    def purge_expired(self, now, invite_grace, link_grace):
        with self._store._conn(tx=True) as c:  # noqa: SLF001
            counts = {"nonces": c.execute("DELETE FROM nonces WHERE expires_at < %s", (now,)).rowcount,
                      "sessions": c.execute("DELETE FROM sessions WHERE expires_at < %s", (now,)).rowcount,
                      "link_requests": c.execute("DELETE FROM link_requests WHERE status != 'approved' AND expires_at < %s", (now - link_grace,)).rowcount}
            old = [r[0] for r in c.execute("SELECT invite_id FROM invites WHERE status='open' AND expires_at < %s", (now - invite_grace,)).fetchall()]
            for iid in old:
                c.execute("DELETE FROM join_requests WHERE invite_id=%s AND status='pending'", (iid,))
                c.execute("DELETE FROM invites WHERE invite_id=%s", (iid,))
            counts["invites"] = len(old)
        return counts

    def put_link(self, request_id, handle, device, words, expires_at):
        self._q("INSERT INTO link_requests (request_id, handle, device_id, sign_vk, box_pk, words, expires_at, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", (request_id, handle, device["device_id"], device["sign_vk"], device["box_pk"], words,
                                                      expires_at, device["created_at"]))

    def count_pending_links(self, handle, now):
        return self._q("SELECT COUNT(*) FROM link_requests WHERE handle=%s AND status='pending' AND expires_at >= %s", (handle, now))[0]

    def list_pending_links(self, handle, now):
        rows = self._q("SELECT request_id, handle, device_id, words FROM link_requests WHERE handle=%s AND status='pending' "
                       "AND expires_at >= %s ORDER BY created_at", (handle, now), many=True)
        return [LinkRequest(r[0], r[1], r[2], r[3], "pending") for r in rows]

    def get_link(self, request_id, now):
        row = self._q("SELECT request_id, handle, device_id, words, status, grants, expires_at FROM link_requests WHERE request_id=%s",
                      (request_id,))
        if row is None:
            return None
        status = "expired" if row[4] == "pending" and row[6] < now else row[4]
        return LinkRequest(row[0], row[1], row[2], row[3], status, bytes(row[5] or b""))

    def approve_link(self, request_id, approver_handle, now, grants):
        with self._store._conn(tx=True) as c:  # noqa: SLF001
            row = c.execute("SELECT handle, device_id, sign_vk, box_pk, status, expires_at FROM link_requests "
                            "WHERE request_id=%s FOR UPDATE", (request_id,)).fetchone()
            if row is None or row[4] != "pending" or row[5] < now or row[0] != approver_handle:
                raise AccountError("this link request can no longer be approved", 409)
            if c.execute("SELECT 1 FROM devices WHERE device_id=%s", (row[1],)).fetchone():
                raise AccountError("this device already belongs to an account", 409)
            c.execute("UPDATE link_requests SET status='approved', grants=%s WHERE request_id=%s", (grants, request_id))
            c.execute(f"INSERT INTO devices ({_DEVICE_COLS}) VALUES (%s,%s,%s,%s,%s,NULL)", (row[1], row[0], row[2], row[3], now))
        return Device(row[1], row[0], bytes.fromhex(row[2]), bytes.fromhex(row[3]), now)

    def put_invite(self, invite, max_open, now):
        with self._store._conn(tx=True) as c:  # noqa: SLF001
            n = c.execute("SELECT COUNT(*) FROM invites WHERE room_id=%s AND status='open' AND expires_at >= %s", (invite["room_id"], now)).fetchone()[0]
            if n >= max_open:
                raise AccountError("this room already has too many open invitations; wait for some to expire", 429)
            c.execute("INSERT INTO invites (invite_id, room_id, host_device_id, wrapped, title, expires_at, created_at, claim_verifier) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                      (invite["invite_id"], invite["room_id"], invite["host_device_id"], invite["wrapped"], invite["title"],
                       invite["expires_at"], invite["created_at"], invite["claim_verifier"]))

    def get_invite(self, invite_id, now):
        row = self._q("SELECT invite_id, room_id, host_device_id, wrapped, title, expires_at, status FROM invites WHERE invite_id=%s", (invite_id,))
        return _invite_from(row, now, self.get_device(row[2]) if row and row[2] else None) if row else None

    def claim_invite(self, invite_id, request, now):
        with self._store._conn(tx=True) as c:  # noqa: SLF001
            row = c.execute("SELECT status, expires_at, claim_verifier FROM invites WHERE invite_id=%s FOR UPDATE", (invite_id,)).fetchone()
            if row is None:
                raise AccountError("no such invite", 404)
            if row[0] != "open" or row[1] < now:
                raise AccountError("this invite is no longer open; ask the host for a new one", 410)
            _check_claim(row[2], request.get("claim_key", b""))
            d = request["device"]
            c.execute("UPDATE invites SET status='claimed' WHERE invite_id=%s", (invite_id,))
            c.execute("INSERT INTO join_requests (request_id, invite_id, room_id, handle, device_id, sign_vk, box_pk, words, created_at) "
                      "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", (request["request_id"], invite_id, request["room_id"], request["handle"], d["device_id"],
                                                      d["sign_vk"], d["box_pk"], request["words"], request["created_at"]))

    def get_join_request(self, request_id):
        row = self._q(f"SELECT {_JOIN_COLS} FROM join_requests WHERE request_id=%s", (request_id,))
        return _join_from(row) if row else None

    def list_join_requests(self, room_id):
        rows = self._q(f"SELECT {_JOIN_COLS} FROM join_requests WHERE room_id=%s AND status='pending' ORDER BY created_at, request_id",
                       (room_id,), many=True)
        return [_join_from(r) for r in rows]

    def decide_join(self, room_id, request_id, approve, now):
        with self._store._conn(tx=True) as c:  # noqa: SLF001
            row = c.execute(f"SELECT {_JOIN_COLS}, sign_vk, box_pk FROM join_requests WHERE request_id=%s FOR UPDATE", (request_id,)).fetchone()
            if row is None or row[2] != room_id:
                raise AccountError("no such join request for this room", 404)
            req = _join_from(row[:8])
            if req.status == ("approved" if approve else "rejected"):
                return req  # deciding again the same way is harmless
            if req.status != "pending":
                raise AccountError(f"this request was already {req.status}", 409)
            if approve:
                have = c.execute("SELECT handle FROM devices WHERE device_id=%s", (req.device_id,)).fetchone()
                if have is not None:
                    if have[0] != req.handle:
                        raise AccountError("that device now belongs to a different account", 409)
                else:
                    if c.execute("SELECT 1 FROM accounts WHERE handle=%s", (req.handle,)).fetchone():
                        raise AccountError("that handle was taken in the meantime; ask the guest to pick another", 409)
                    c.execute("INSERT INTO accounts (handle, can_host, created_at) VALUES (%s,false,%s)", (req.handle, now))
                    c.execute(f"INSERT INTO devices ({_DEVICE_COLS}) VALUES (%s,%s,%s,%s,%s,NULL)", (req.device_id, req.handle, row[8], row[9], now))
            c.execute("UPDATE join_requests SET status=%s WHERE request_id=%s", ("approved" if approve else "rejected", request_id))
            return JoinRequest(req.request_id, req.invite_id, req.room_id, req.handle, req.device_id, req.words,
                               "approved" if approve else "rejected", req.created_at)

    def put_grant(self, room_id, epoch, device_id, grant, now):
        self._q("INSERT INTO grants (room_id, epoch, device_id, grant_blob, created_at) VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT (room_id, epoch, device_id) DO UPDATE SET grant_blob=excluded.grant_blob, created_at=excluded.created_at",
                (room_id, epoch, device_id, grant, now))

    def list_grants(self, room_id, device_id):
        rows = self._q("SELECT epoch, grant_blob FROM grants WHERE room_id=%s AND device_id=%s ORDER BY epoch", (room_id, device_id), many=True)
        return [(r[0], bytes(r[1])) for r in rows]
