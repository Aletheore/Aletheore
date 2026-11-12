"""Restarts, full disks, expired sessions, skewed clocks, members coming and going. Each test states the invariant."""

import errno
import sqlite3
import threading
import time

import pytest
from fastapi.testclient import TestClient

from aletheore.council.accounts import AccountService, SqliteAccountBackend
from aletheore.council.identity import Keystore
from aletheore.council.outbox import OutboxStore
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.remote_store import RemoteRoomStore
from aletheore.council.server import create_app
from aletheore.council.sqlite_store import SqliteRoomStore
from tests.council.chaos.live import LiveServer


def draft(cid, text="x", room="r1", who="bob"):
    return EventDraft(room, cid, "t", EventType.CLAIM, who, None, None, {"text": text})


def test_server_restart_under_load_loses_no_acknowledged_event_and_clients_reconnect(tmp_path):
    server = LiveServer(tmp_path).start()
    admin = RemoteRoomStore(server.url, "tok-a", timeout_s=2, backoff_s=0.01)
    admin.create_room("r1", "alice")
    for who in ("bob", "carol"):
        admin.add_member("r1", who)
    acked: dict[str, set[str]] = {"bob": set(), "carol": set()}
    stop = threading.Event()

    def worker(name, token):
        store = OutboxStore(RemoteRoomStore(server.url, token, timeout_s=1, backoff_s=0.01), tmp_path / f"{name}.json")
        i = 0
        while not stop.is_set():
            cid = f"{name}-{i}"
            ev = store.append(draft(cid, who=name))
            if ev.seq > 0:
                acked[name].add(cid)  # the server said yes
            i += 1
            time.sleep(0.005)
        for _ in range(100):  # deliver whatever was queued while the server was down
            store.flush()
            if not store.queued():
                break
            time.sleep(0.1)
        results[name] = store

    results: dict = {}
    threads = [threading.Thread(target=worker, args=(n, t), daemon=True) for n, t in (("bob", "tok-b"), ("carol", "tok-c"))]
    for t in threads:
        t.start()
    time.sleep(0.6)
    server.restart()  # killed and started again over the same database, mid-traffic
    time.sleep(0.6)
    stop.set()
    for t in threads:
        t.join(timeout=20)
    events = RemoteRoomStore(server.url, "tok-a", backoff_s=0.01).read_after("r1", 0)
    ids = [e.client_event_id for e in events if e.type is EventType.CLAIM]
    assert len(ids) == len(set(ids))  # nothing twice
    for name in acked:
        assert acked[name] <= set(ids)  # every event the server acknowledged is still there
        assert all(not results[name].queued() for _ in [0])  # and what was queued while it was down arrived after
    assert [e.seq for e in events] == list(range(1, len(events) + 1))  # in order, no gaps
    server.stop()


def test_disk_full_gives_a_clear_error_and_no_corrupt_rows_then_recovers(tmp_path, monkeypatch):
    store = SqliteRoomStore(tmp_path / "s.db")
    app = create_app(store, {"ta": "alice"}, tmp_path / "b")
    c = TestClient(app, base_url="http://x.test", raise_server_exceptions=False)
    A = {"Authorization": "Bearer ta"}
    c.post("/v1/rooms/r1", json={"host": "alice"}, headers=A)
    body = {"client_event_id": "e1", "thread_id": "t", "type": "claim", "sender_participant": "alice", "sender_agent": None, "reply_to": None, "body": {"text": "x"}}
    real = store.append

    def full(d):
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(store, "append", full)
    r = c.post("/v1/rooms/r1/events", json=body, headers=A)
    assert r.status_code == 507 and "disk" in r.json()["detail"].lower()  # a clear answer, not a bare 500
    monkeypatch.setattr(store, "append", real)
    assert c.post("/v1/rooms/r1/events", json=body, headers=A).status_code == 200  # recovers, nothing corrupt
    assert [e.client_event_id for e in store.read_after("r1", 0)] == ["e1"]
    # a full disk while storing a bundle: a clear error and no leftover temp file
    import hashlib
    import tempfile

    data = b"q" * 5000
    sha = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(tempfile, "mkstemp", lambda *a, **k: (_ for _ in ()).throw(OSError(errno.ENOSPC, "No space left on device")))
    r2 = c.put(f"/v1/rooms/r1/bundles/{sha}", content=data, headers=A)
    assert r2.status_code == 507 and "disk" in r2.json()["detail"].lower()
    monkeypatch.undo()
    assert not list((tmp_path / "b" / "r1").glob("*.tmp")) and not list((tmp_path / "b" / "r1").glob("*.bundle"))


def test_token_expiry_mid_session_relogs_in_transparently_without_losing_a_message(tmp_path):
    class Clock:
        t = 1_800_000_000.0

        def __call__(self):
            return self.t

    clock = Clock()
    service = AccountService(SqliteAccountBackend(tmp_path / "a.db"), clock=clock, session_ttl=600)
    store = SqliteRoomStore(tmp_path / "s.db")
    app = create_app(store, {}, tmp_path / "b", clock=clock, accounts=service)
    http = TestClient(app, base_url="http://x.test")
    ident = Keystore(tmp_path / "k").load_or_create("a")
    service.redeem_registration_code(service.create_registration_code("alice", can_host=True), ident.public(), "alice")
    remote = RemoteRoomStore("http://x.test", client=http, backoff_s=0.01)
    remote.login(ident)
    remote.create_room("r1", "alice")
    remote.append(draft("before", who="alice"))
    clock.t += 3600  # the session ran out long ago
    sent = remote.append(draft("after", "sent after the session expired", who="alice"))  # signs in again by itself
    assert sent.seq == 2 and [e.client_event_id for e in store.read_after("r1", 0)] == ["before", "after"]


def test_login_and_invites_do_not_depend_on_the_clients_clock(tmp_path, monkeypatch):
    import time as real_time

    service = AccountService(SqliteAccountBackend(tmp_path / "a.db"))
    store = SqliteRoomStore(tmp_path / "s.db")
    http = TestClient(create_app(store, {}, tmp_path / "b", accounts=service), base_url="http://x.test")
    ident = Keystore(tmp_path / "k").load_or_create("a")
    service.redeem_registration_code(service.create_registration_code("alice", can_host=True), ident.public(), "alice")
    monkeypatch.setattr(real_time, "time", lambda: real_time.monotonic() + 3600 * 1e6)  # a client whose clock is wildly wrong
    remote = RemoteRoomStore("http://x.test", client=http, backoff_s=0.01)
    assert remote.login(ident) == "alice"  # the server's clock decides; the client's never mattered


def test_a_registry_client_with_a_skewed_clock_learns_the_offset_and_succeeds(tmp_path):
    from aletheore.council import crypto
    from aletheore.council.registry_client import RegistryClient
    from aletheore.council.registry_server import create_registry_app
    from aletheore.council.tunnel_registry import FakeProvider, Registry

    class Clock:
        t = 1_800_000_000.0

        def __call__(self):
            return self.t

    server_clock = Clock()
    sk, vk = crypto.generate_signing_keypair()
    reg = Registry(tmp_path / "r.db", FakeProvider(), "example.test", (sk, vk), slots=1, clock=server_clock)
    http = TestClient(create_registry_app(reg, server_clock))
    ident = Keystore(tmp_path / "k").load_or_create("h")
    skewed = RegistryClient(http, ident, vk, clock=lambda: server_clock.t + 3600)  # an hour fast
    got = skewed.claim()
    assert got["status"] == "leased"  # the first try was refused for its time; the client learned the offset and tried again
    assert skewed.heartbeat() > server_clock.t  # and remembers it


def test_removed_member_is_refused_but_a_legitimately_offline_member_rejoins_without_a_new_invite(tmp_path):
    server = LiveServer(tmp_path).start()
    admin = RemoteRoomStore(server.url, "tok-a", backoff_s=0.01)
    admin.create_room("r1", "alice")
    for who in ("bob", "carol"):
        admin.add_member("r1", who)
    carol_store = RemoteRoomStore(server.url, "tok-c", backoff_s=0.01)
    carol_store.append(draft("c-before", who="carol"))
    for i in range(20):
        admin.append(draft(f"a{i}", who="alice"))  # carol is away while this happens (she makes no calls)
    admin.remove_member("r1", "bob")
    with pytest.raises(PermissionError):
        RemoteRoomStore(server.url, "tok-b", backoff_s=0.01).append(draft("b-after"))  # removed: refused at once
    back = carol_store.append(draft("c-after", who="carol"))  # still a member: back with the same token, no new invite
    assert back.seq == 22
    assert len(carol_store.read_after("r1", 0)) == 22
    server.stop()
