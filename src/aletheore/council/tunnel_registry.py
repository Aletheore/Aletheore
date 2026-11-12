"""The tunnel registry: a pool of free slots that hosts lease, and a signed answer to "where is this host now?".

A slot is a name (`slot-017`) plus a tunnel at the tunnel provider. A host holds a slot only while it uses it: its heartbeat
renews the lease, a lease with no heartbeat for the grace period (24 hours) is released, and `release` frees it at once. When a
slot is released its tunnel's secret is changed and the old connections dropped, so the previous holder's token stops working
the same moment the next host can claim it. The registry knows only which host (a device id) holds which slot. It never sees
room traffic: the tunnel carries ciphertext straight to the host's machine.

This module has no network code of its own: the provider (Cloudflare in production, `FakeProvider` in tests) is passed in."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from . import crypto
from .identity import PublicIdentity

GRACE_S = 24 * 3600  # a lease with no heartbeat for this long is released
QUEUE_WAIT_S = 15 * 60  # a queued host that has not asked again for this long loses its place
ANSWER_TTL_S = 300  # how long a signed "where is this host" answer is good for


class Provider(Protocol):
    def create_tunnel(self, slot: str) -> str: ...  # returns the provider's tunnel id; also points `slot.<domain>` at it
    def rotate(self, tunnel_id: str) -> str: ...  # new secret: returns the new run token and drops every existing connection


class RegistryError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Claim:
    status: str  # leased | queued | banned
    slot: str = ""
    hostname: str = ""
    tunnel_id: str = ""
    token: str = ""  # shown once, at the moment of the claim
    lease_expires: float = 0.0
    position: int = 0  # for a queued host: 1 is next
    message: str = ""


@dataclass
class FakeProvider:
    """A stand-in for Cloudflare that behaves the way the docs say it does, so the registry logic is tested without a network."""
    tunnels: dict[str, int] = field(default_factory=dict)  # tunnel id -> current secret generation
    connections: dict[str, set[int]] = field(default_factory=dict)  # tunnel id -> generations currently connected
    calls: list[tuple] = field(default_factory=list)
    _n: int = 0

    def create_tunnel(self, slot: str) -> str:
        self._n += 1
        tid = f"tun-{self._n}"
        self.tunnels[tid], self.connections[tid] = 0, set()
        self.calls.append(("create", slot, tid))
        return tid

    def rotate(self, tunnel_id: str) -> str:
        self.tunnels[tunnel_id] += 1
        self.connections[tunnel_id].clear()  # the real sequence: change the secret, then drop connections
        self.calls.append(("rotate", tunnel_id))
        return f"{tunnel_id}:{self.tunnels[tunnel_id]}"

    # what a host's cloudflared would do, for tests
    def connect(self, tunnel_id: str, token: str) -> bool:
        tid, _, gen = token.partition(":")
        if tid != tunnel_id or not gen.isdigit() or int(gen) != self.tunnels.get(tunnel_id):
            return False
        self.connections[tunnel_id].add(int(gen))
        return True


SCHEMA = """
CREATE TABLE IF NOT EXISTS slots (name TEXT PRIMARY KEY, tunnel_id TEXT NOT NULL, holder TEXT, lease_expires REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS queue (host TEXT PRIMARY KEY, queued_at REAL NOT NULL, last_seen REAL NOT NULL);
CREATE TABLE IF NOT EXISTS hosts (host TEXT PRIMARY KEY, sign_vk BLOB NOT NULL, box_pk BLOB NOT NULL, banned INTEGER NOT NULL DEFAULT 0);
"""


class Registry:
    def __init__(self, path: str | Path, provider: Provider, domain: str, signing_key: tuple[bytes, bytes], slots: int = 150,
                 clock: Callable[[], float] = time.time) -> None:
        """`signing_key` is the registry's (secret key, public key); the public half is what clients pin."""
        self.path, self.provider, self.domain, self.clock = str(path), provider, domain, clock
        self._sk, self.vk = signing_key
        self.total = slots
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as c:
            c.executescript(SCHEMA)
        self.ensure_slots(slots)

    # -- storage helpers ---------------------------------------------------------------------------------------------
    def _db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def ensure_slots(self, n: int) -> None:
        """Create slots up to `n`. Existing ones are kept; the pool never shrinks under a host that holds one."""
        with closing(self._db()) as c:
            have = {r[0] for r in c.execute("SELECT name FROM slots")}
        for i in range(1, n + 1):
            name = f"slot-{i:03d}"
            if name not in have:
                tid = self.provider.create_tunnel(name)
                with closing(self._db()) as c:
                    c.execute("INSERT INTO slots (name, tunnel_id) VALUES (?,?)", (name, tid))

    # -- leasing -----------------------------------------------------------------------------------------------------
    def register_host(self, public: PublicIdentity) -> None:
        with closing(self._db()) as c:
            c.execute("INSERT INTO hosts (host, sign_vk, box_pk) VALUES (?,?,?) ON CONFLICT(host) DO UPDATE SET sign_vk=excluded.sign_vk, "
                      "box_pk=excluded.box_pk", (public.device_id, public.sign_vk, public.box_pk))

    def _banned(self, c, host: str) -> bool:
        row = c.execute("SELECT banned FROM hosts WHERE host=?", (host,)).fetchone()
        return bool(row and row[0])

    def sweep(self) -> int:
        """Release leases whose grace has run out, and forget queued hosts that stopped asking. Returns how many slots were freed."""
        now, freed = self.clock(), 0
        with closing(self._db()) as c:
            expired = [r[0] for r in c.execute("SELECT name FROM slots WHERE holder IS NOT NULL AND lease_expires < ?", (now,))]
            c.execute("DELETE FROM queue WHERE last_seen < ?", (now - QUEUE_WAIT_S,))
        for name in expired:
            self._free(name)
            freed += 1
        return freed

    def _free(self, name: str) -> None:
        with closing(self._db()) as c:
            row = c.execute("SELECT tunnel_id FROM slots WHERE name=?", (name,)).fetchone()
            c.execute("UPDATE slots SET holder=NULL, lease_expires=0 WHERE name=?", (name,))
        if row:
            self.provider.rotate(row[0])  # the old holder's token dies now; the new token is made at the next claim

    def claim(self, public: PublicIdentity) -> Claim:
        """Give this host a slot, or put it in the queue. A host that already holds one gets the same slot (and a fresh token)."""
        self.register_host(public)
        self.sweep()
        host, now = public.device_id, self.clock()
        with closing(self._db()) as c:
            if self._banned(c, host):
                return Claim("banned", message="this host has been blocked from the free tunnels")
            mine = c.execute("SELECT name, tunnel_id FROM slots WHERE holder=?", (host,)).fetchone()
            if mine is None:
                head = c.execute("SELECT host FROM queue ORDER BY queued_at, rowid LIMIT 1").fetchone()
                if head and head[0] != host:
                    return self._enqueue(c, host, now)  # someone has been waiting longer
                free = c.execute("SELECT name, tunnel_id FROM slots WHERE holder IS NULL ORDER BY name LIMIT 1").fetchone()
                if free is None:
                    return self._enqueue(c, host, now)
                c.execute("DELETE FROM queue WHERE host=?", (host,))
                mine = free
            expires = now + GRACE_S
            c.execute("UPDATE slots SET holder=?, lease_expires=? WHERE name=?", (host, expires, mine[0]))
        token = self.provider.rotate(mine[1])
        return Claim("leased", mine[0], f"{mine[0]}.{self.domain}", mine[1], token, expires)

    def _enqueue(self, c, host: str, now: float) -> Claim:
        c.execute("INSERT INTO queue (host, queued_at, last_seen) VALUES (?,?,?) ON CONFLICT(host) DO UPDATE SET last_seen=excluded.last_seen",
                  (host, now, now))
        mine = c.execute("SELECT queued_at, rowid FROM queue WHERE host=?", (host,)).fetchone()
        position = c.execute("SELECT COUNT(*) FROM queue WHERE queued_at < ? OR (queued_at = ? AND rowid < ?)", (mine[0], mine[0], mine[1])).fetchone()[0] + 1
        return Claim("queued", position=position, message=f"all {self.total} free slots are in use; you are number {position} in the queue. "
                                                          "Ask again in a few minutes, or use --local or Tailscale.")

    def heartbeat(self, host: str) -> float:
        """The host is alive: lease runs another grace period from now. Returns the new expiry."""
        now = self.clock()
        with closing(self._db()) as c:
            row = c.execute("SELECT name, lease_expires FROM slots WHERE holder=?", (host,)).fetchone()
            if row is None or row[1] < now:
                raise RegistryError("you do not hold a slot (it may have been released); claim again", 404)
            expires = now + GRACE_S
            c.execute("UPDATE slots SET lease_expires=? WHERE name=?", (expires, row[0]))
        return expires

    def release(self, host: str) -> bool:
        """Free the slot now (the host ended its room). True when the host held one."""
        with closing(self._db()) as c:
            row = c.execute("SELECT name FROM slots WHERE holder=?", (host,)).fetchone()
        if row is None:
            return False
        self._free(row[0])
        return True

    def ban(self, host: str, reason: str = "") -> None:
        """Revoke a host: its slot is released and it cannot claim again."""
        with closing(self._db()) as c:
            c.execute("INSERT INTO hosts (host, sign_vk, box_pk, banned) VALUES (?, x'', x'', 1) ON CONFLICT(host) DO UPDATE SET banned=1", (host,))
        self.release(host)

    # -- the honest counter ---------------------------------------------------------------------------------------------
    def counter(self) -> dict:
        self.sweep()
        with closing(self._db()) as c:
            used = c.execute("SELECT COUNT(*) FROM slots WHERE holder IS NOT NULL").fetchone()[0]
            total = c.execute("SELECT COUNT(*) FROM slots").fetchone()[0]
            queued = c.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
        return {"in_use": used, "total": total, "queued": queued}

    # -- where is this host now (signed) -------------------------------------------------------------------------------
    def lookup(self, host: str) -> dict:
        now = self.clock()
        with closing(self._db()) as c:
            slot = c.execute("SELECT name FROM slots WHERE holder=? AND lease_expires >= ?", (host, now)).fetchone()
            keys = c.execute("SELECT sign_vk, box_pk FROM hosts WHERE host=? AND banned=0", (host,)).fetchone()
        if slot is None or keys is None:
            raise RegistryError("this host has no public address right now", 404)
        payload = {"host": host, "sign_vk": bytes(keys[0]).hex(), "box_pk": bytes(keys[1]).hex(),
                   "address": f"https://{slot[0]}.{self.domain}", "issued": now, "expires": now + ANSWER_TTL_S}
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return {"payload": payload, "signature": crypto.sign(self._sk, b"council-registry-answer-v1" + body).hex()}


def verify_answer(answer: dict, registry_vk: bytes, expected_host: str, now: float) -> str:
    """Check a lookup answer: signed by the pinned registry, not expired, for the host the link names and carrying that host's keys.
    Returns the address. Raises ValueError with a plain reason otherwise."""
    try:
        payload, signature = answer["payload"], bytes.fromhex(answer["signature"])
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        public = PublicIdentity(bytes.fromhex(payload["sign_vk"]), bytes.fromhex(payload["box_pk"]))
        expires, host, address = float(payload["expires"]), payload["host"], str(payload["address"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("the registry's answer is malformed") from None
    if not crypto.verify(registry_vk, b"council-registry-answer-v1" + body, signature):
        raise ValueError("the registry's answer is not signed by the registry this app trusts")
    if expires < now:
        raise ValueError("the registry's answer has expired; ask again, and if this keeps happening check this computer's clock")
    if host != expected_host:
        raise ValueError("the registry answered for a different host")
    if public.device_id != expected_host:
        raise ValueError("the keys in the registry's answer do not belong to the host in your link")
    return address


PROOF_PREFIX = b"council-host-proof-v1"


def host_proof_message(nonce: bytes) -> bytes:
    return PROOF_PREFIX + nonce


def verify_host_proof(public: PublicIdentity, nonce: bytes, signature: bytes) -> bool:
    """A server that is the host's can sign a fresh nonce with the host's device key; nobody else can."""
    return crypto.verify(public.sign_vk, host_proof_message(nonce), signature)
