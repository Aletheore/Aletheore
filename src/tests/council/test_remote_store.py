import hashlib

import httpx
import pytest
from fastapi.testclient import TestClient

from aletheore.council.protocol import EventDraft, EventType, ProtocolError
from aletheore.council.remote_store import RemoteRoomStore
from aletheore.council.server import create_app
from aletheore.council.sqlite_store import SqliteRoomStore
from tests.council.store_contract import StoreContract, draft


def make(tmp_path, tokens=None, name="r.db"):
    backing = SqliteRoomStore(tmp_path / name)
    app = create_app(backing, tokens or {"tok-alice": "alice", "tok-bob": "bob"}, tmp_path / "bundles")
    return backing, TestClient(app)


class TestRemoteStore(StoreContract):
    @pytest.fixture
    def store(self, tmp_path):
        _, client = make(tmp_path)
        return RemoteRoomStore("http://testserver", "tok-alice", client=client, backoff_s=0)


def test_resumes_after_server_restart(tmp_path):
    backing, client = make(tmp_path)
    s = RemoteRoomStore("http://testserver", "tok-alice", client=client, backoff_s=0)
    s.create_room("r1", "alice")
    s.append(draft(cid="c1"))
    _, client2 = make(tmp_path)  # a new server process over the same database file
    s2 = RemoteRoomStore("http://testserver", "tok-alice", client=client2, backoff_s=0)
    assert [e.seq for e in s2.read_after("r1", 0)] == [1]
    assert s2.append(draft(cid="c2")).seq == 2


def test_403_raises_permission_error(tmp_path):
    _, client = make(tmp_path)
    s = RemoteRoomStore("http://testserver", "tok-alice", client=client, backoff_s=0)
    s.create_room("r1", "alice")
    with pytest.raises(PermissionError):
        s.append(draft(sender="bob"))


def test_wrong_token_raises_permission_error(tmp_path):
    _, client = make(tmp_path)
    with pytest.raises(PermissionError):
        RemoteRoomStore("http://testserver", "bad", client=client, backoff_s=0).last_seq("r1")


def test_unknown_room_raises_key_error(tmp_path):
    _, client = make(tmp_path)
    with pytest.raises(KeyError):
        RemoteRoomStore("http://testserver", "tok-alice", client=client, backoff_s=0).host_of("ghost")


def test_malformed_draft_raises_protocol_error(tmp_path):
    _, client = make(tmp_path)
    s = RemoteRoomStore("http://testserver", "tok-alice", client=client, backoff_s=0)
    s.create_room("r1", "alice")
    bad = EventDraft("r1", "c1", "bad thread", EventType.CLAIM, "alice", None, None, {"text": "x"})
    with pytest.raises(ProtocolError):
        s.append(bad)


def test_connection_error_retried_then_raises():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("down")

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x")
    s = RemoteRoomStore("http://x", "t", client=client, backoff_s=0)
    with pytest.raises(ConnectionError):
        s.last_seq("r1")
    assert len(calls) == 3


def test_server_error_retried_but_client_error_is_not():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(500 if "last_seq" in request.url.path else 404)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x")
    s = RemoteRoomStore("http://x", "t", client=client, backoff_s=0)
    with pytest.raises(ConnectionError):
        s.last_seq("r1")
    assert len(seen) == 3
    seen.clear()
    with pytest.raises(KeyError):
        s.host_of("r1")
    assert len(seen) == 1


def test_remote_bundle_roundtrip_matches_bytes(tmp_path):
    _, client = make(tmp_path)
    s = RemoteRoomStore("http://testserver", "tok-alice", client=client, backoff_s=0)
    s.create_room("r1", "alice")
    s.add_member("r1", "bob")
    src = tmp_path / "a.bundle"
    src.write_bytes(b"\x00\x01bundle\xff" * 50)
    sha = hashlib.sha256(src.read_bytes()).hexdigest()
    s.put_bundle("r1", sha, src)
    dest = tmp_path / "out" / "b.bundle"
    RemoteRoomStore("http://testserver", "tok-bob", client=client, backoff_s=0).get_bundle("r1", sha, dest)
    assert dest.read_bytes() == src.read_bytes()
    with pytest.raises(KeyError):
        s.get_bundle("r1", "0" * 64, tmp_path / "nope")


def test_a_429_is_waited_out_using_retry_after_and_then_succeeds(monkeypatch):
    import httpx
    from aletheore.council import remote_store as rs

    calls = {"n": 0}
    slept = []

    def handler(request):
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"detail": "too many requests; slow down"})
        return httpx.Response(200, json={"last_seq": 7})

    monkeypatch.setattr(rs.time, "sleep", lambda s: slept.append(s))
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://t")
    store = rs.RemoteRoomStore("http://t", "tok", client=client, backoff_s=0)
    assert store.last_seq("r1") == 7
    assert calls["n"] == 3 and slept == [2.0, 2.0]


def test_a_429_that_never_clears_ends_as_a_connection_error_with_a_clear_message(monkeypatch):
    import httpx
    from aletheore.council import remote_store as rs

    monkeypatch.setattr(rs.time, "sleep", lambda s: None)
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429, headers={"Retry-After": "1"}, json={"detail": "x"})), base_url="http://t")
    store = rs.RemoteRoomStore("http://t", "tok", client=client, backoff_s=0)
    with pytest.raises(ConnectionError, match="rate limit"):
        store.last_seq("r1")


def test_a_huge_retry_after_is_capped_so_a_client_never_sleeps_for_minutes(monkeypatch):
    import httpx
    from aletheore.council import remote_store as rs

    slept = []
    monkeypatch.setattr(rs.time, "sleep", lambda s: slept.append(s))
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429, headers={"Retry-After": "3600"}, json={"detail": "x"})), base_url="http://t")
    store = rs.RemoteRoomStore("http://t", "tok", client=client, backoff_s=0)
    with pytest.raises(ConnectionError):
        store.last_seq("r1")
    assert slept and max(slept) <= 10


def test_retries_back_off_with_full_jitter_capped_and_give_up_cleanly(monkeypatch):
    import httpx

    from aletheore.council import remote_store as rs

    pauses = []
    monkeypatch.setattr(rs.time, "sleep", pauses.append)
    attempts = []

    def handler(req):
        attempts.append(1)
        raise httpx.ConnectError("no route")

    client = httpx.Client(base_url="http://x.test", transport=httpx.MockTransport(handler))
    store = rs.RemoteRoomStore("http://x.test", "t", client=client, backoff_s=100.0)  # a huge base: the cap must hold
    runs = []
    for _ in range(40):
        pauses.clear()
        with pytest.raises(ConnectionError, match="unreachable"):
            store.last_seq("r1")
        runs.append(tuple(pauses))
    assert all(len(p) == rs.ATTEMPTS - 1 for p in runs)  # one pause between each pair of tries
    assert all(0 <= x <= rs.MAX_BACKOFF_S for p in runs for x in p)  # never longer than the cap
    assert len({round(x, 6) for p in runs for x in p}) > 20  # and genuinely random, not one fixed step
    assert len(attempts) == 40 * rs.ATTEMPTS


def test_a_store_with_no_token_sends_no_authorization_header_so_a_real_server_accepts_it(tmp_path):
    """Found by the real acceptance rehearsal: register and join-link build a store with an empty token, and the header 'Bearer ' with
    nothing after it is rejected by a real HTTP stack (a test client does not notice). Run it against a real server on a real socket."""
    from aletheore.council.accounts import AccountService, SqliteAccountBackend
    from aletheore.council.identity import Keystore
    from aletheore.council.remote_store import RemoteRoomStore
    from aletheore.council.server import create_app
    from aletheore.council.sqlite_store import SqliteRoomStore
    from tests.council.chaos.live import LiveServer

    service = AccountService(SqliteAccountBackend(tmp_path / "a.db"))
    live = LiveServer(tmp_path, tokens={}, accounts=service).start()
    try:
        ident = Keystore(tmp_path / "k").load_or_create("a")
        code = service.create_registration_code("alice", can_host=True)
        fresh = RemoteRoomStore(live.url, "")  # not signed in yet
        assert "Authorization" not in fresh._headers
        done = fresh.register(code, "alice", ident)  # a real request over a real socket
        assert done["handle"] == "alice"
        assert fresh.login(ident) == "alice" and "Authorization" in fresh._headers  # signing in adds it
    finally:
        live.stop()
