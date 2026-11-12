import hashlib
import logging
import os

import pytest
from fastapi.testclient import TestClient

from aletheore.council.protocol import MAX_BODY_BYTES, MAX_BUNDLE_BYTES
from aletheore.council.server import create_app, parse_tokens
from aletheore.council.sqlite_store import SqliteRoomStore

TOKENS = {"tok-alice": "alice", "tok-bob": "bob"}


@pytest.fixture
def env(tmp_path):
    store = SqliteRoomStore(tmp_path / "r.db")
    now = [1_000_000.0]
    app = create_app(store, TOKENS, tmp_path / "bundles", retention_days=7, clock=lambda: now[0])
    return TestClient(app), store, tmp_path, now


def auth(tok):
    return {"Authorization": f"Bearer {tok}"}


def draft(cid="c1", sender="alice", text="hi", thread="t1", etype="claim"):
    return {"type": etype, "client_event_id": cid, "thread_id": thread, "reply_to": None,
            "sender_participant": sender, "sender_agent": "claude", "body": {"text": text}}


def make_room(client, room="r1", tok="tok-alice", host="alice", invite=("bob",)):
    r = client.post(f"/v1/rooms/{room}", json={"host": host}, headers=auth(tok))
    if r.status_code == 200:
        for who in invite:
            client.post(f"/v1/rooms/{room}/members", json={"participant": who}, headers=auth(tok))
    return r


def test_missing_or_wrong_token_is_401(env):
    client, *_ = env
    assert client.get("/v1/rooms/r1/last_seq").status_code == 401
    assert client.get("/v1/rooms/r1/last_seq", headers=auth("nope")).status_code == 401
    assert client.post("/v1/rooms/r1", json={"host": "alice"}).status_code == 401


def test_event_roundtrip_and_seq_order(env):
    client, *_ = env
    assert make_room(client).status_code == 200
    for i in range(3):
        r = client.post("/v1/rooms/r1/events", json=draft(cid=f"c{i}"), headers=auth("tok-alice"))
        assert r.status_code == 200 and r.json()["seq"] == i + 1
    got = client.get("/v1/rooms/r1/events?after_seq=0", headers=auth("tok-bob")).json()["events"]
    assert [e["seq"] for e in got] == [1, 2, 3] and got[0]["type"] == "claim" and got[0]["body"] == {"text": "hi"}
    assert client.get("/v1/rooms/r1/last_seq", headers=auth("tok-bob")).json() == {"last_seq": 3}
    assert client.get("/v1/rooms/r1/host", headers=auth("tok-bob")).json() == {"host": "alice"}


def test_sender_must_match_token_participant(env):
    client, store, *_ = env
    make_room(client)
    r = client.post("/v1/rooms/r1/events", json=draft(sender="bob"), headers=auth("tok-alice"))
    assert r.status_code == 403
    assert store.last_seq("r1") == 0


def test_only_the_named_host_can_create_a_room(env):
    client, *_ = env
    assert make_room(client, tok="tok-bob", host="alice").status_code == 403


def test_duplicate_client_event_id_is_idempotent_over_http(env):
    client, store, *_ = env
    make_room(client)
    a = client.post("/v1/rooms/r1/events", json=draft(cid="same"), headers=auth("tok-alice")).json()
    b = client.post("/v1/rooms/r1/events", json=draft(cid="same", text="retry"), headers=auth("tok-alice")).json()
    assert a == b and store.last_seq("r1") == 1


def test_oversized_event_body_rejected_without_storing(env):
    client, store, *_ = env
    make_room(client)
    r = client.post("/v1/rooms/r1/events", json=draft(text="x" * (MAX_BODY_BYTES + 10)), headers=auth("tok-alice"))
    assert r.status_code in (413, 422) and store.last_seq("r1") == 0
    huge = client.post("/v1/rooms/r1/events", content=b"{" + b" " * (MAX_BODY_BYTES * 2), headers=auth("tok-alice"))
    assert huge.status_code == 413


def test_malformed_event_is_422(env):
    client, store, *_ = env
    make_room(client)
    bad = draft(etype="shout")
    assert client.post("/v1/rooms/r1/events", json=bad, headers=auth("tok-alice")).status_code == 422
    assert client.post("/v1/rooms/r1/events", json=draft(thread="a\nb"), headers=auth("tok-alice")).status_code == 422
    assert store.last_seq("r1") == 0


def test_unknown_room_is_404(env):
    client, *_ = env
    assert client.get("/v1/rooms/nope/events?after_seq=0", headers=auth("tok-alice")).status_code == 404
    assert client.post("/v1/rooms/nope/events", json=draft(), headers=auth("tok-alice")).status_code == 404
    assert client.get("/v1/rooms/nope/host", headers=auth("tok-alice")).status_code == 404


def test_read_after_resumes_exactly(env):
    client, *_ = env
    make_room(client)
    for i in range(5):
        client.post("/v1/rooms/r1/events", json=draft(cid=f"c{i}"), headers=auth("tok-alice"))
    got = client.get("/v1/rooms/r1/events?after_seq=3", headers=auth("tok-alice")).json()["events"]
    assert [e["seq"] for e in got] == [4, 5]
    assert client.get("/v1/rooms/r1/events?after_seq=0&limit=2", headers=auth("tok-alice")).json()["events"][-1]["seq"] == 2


def put_bundle(client, data, room="r1", tok="tok-alice", sha=None):
    sha = sha or hashlib.sha256(data).hexdigest()
    return client.put(f"/v1/rooms/{room}/bundles/{sha}", content=data, headers=auth(tok)), sha


def test_bundle_roundtrip(env):
    client, *_ = env
    make_room(client)
    r, sha = put_bundle(client, b"BUNDLEBYTES" * 100)
    assert r.status_code == 200
    got = client.get(f"/v1/rooms/r1/bundles/{sha}", headers=auth("tok-bob"))
    assert got.status_code == 200 and got.content == b"BUNDLEBYTES" * 100


def test_bundle_hash_mismatch_is_422(env):
    client, _, tmp, _ = env
    make_room(client)
    r, sha = put_bundle(client, b"abc", sha="0" * 64)
    assert r.status_code == 422
    assert client.get(f"/v1/rooms/r1/bundles/{'0' * 64}", headers=auth("tok-alice")).status_code == 404


def test_oversized_bundle_is_413_and_not_stored(env):
    client, _, tmp, _ = env
    make_room(client)
    data = b"x" * (MAX_BUNDLE_BYTES + 1)
    r, sha = put_bundle(client, data)
    assert r.status_code == 413
    assert client.get(f"/v1/rooms/r1/bundles/{sha}", headers=auth("tok-alice")).status_code == 404


def test_bundle_requires_token_and_room(env):
    client, *_ = env
    make_room(client)
    sha = hashlib.sha256(b"a").hexdigest()
    assert client.put(f"/v1/rooms/r1/bundles/{sha}", content=b"a").status_code == 401
    assert client.get(f"/v1/rooms/r1/bundles/{sha}").status_code == 401
    assert put_bundle(client, b"a", room="ghost")[0].status_code == 404


def test_path_traversal_in_room_or_sha_is_rejected(env):
    client, _, tmp, _ = env
    make_room(client)
    for url in ("/v1/rooms/..%2f..%2fetc/bundles/" + "a" * 64, "/v1/rooms/r1/bundles/..%2f..%2fpasswd", "/v1/rooms/r1/bundles/" + "a" * 63):
        assert client.get(url, headers=auth("tok-alice")).status_code in (404, 422)
        assert client.put(url, content=b"x", headers=auth("tok-alice")).status_code in (404, 422)
    outside = [p for p in tmp.rglob("*") if "bundles" not in p.parts and p.name.endswith(".bundle")]
    assert outside == []


def test_expired_bundle_is_deleted_and_404(env):
    client, _, tmp, now = env
    make_room(client)
    r, sha = put_bundle(client, b"old")
    path = tmp / "bundles" / "r1" / f"{sha}.bundle"
    os.utime(path, (now[0], now[0]))
    now[0] += 8 * 86400
    assert client.get(f"/v1/rooms/r1/bundles/{sha}", headers=auth("tok-alice")).status_code == 404
    assert not path.exists()


def test_server_refuses_zero_tokens(tmp_path):
    with pytest.raises(ValueError):
        create_app(SqliteRoomStore(tmp_path / "r.db"), {}, tmp_path / "b")


def test_parse_tokens_from_args_and_file(tmp_path):
    f = tmp_path / "t.json"
    f.write_text('{"carol": "tok-carol"}')
    assert parse_tokens(["alice=tok-a"], str(f)) == {"tok-a": "alice", "tok-carol": "carol"}
    with pytest.raises(ValueError):
        parse_tokens(["no-equals-sign"], None)
    with pytest.raises(ValueError):
        parse_tokens([], None)


def test_token_body_and_bundle_never_in_logs(env, caplog):
    client, *_ = env
    caplog.set_level(logging.DEBUG)
    make_room(client)
    client.post("/v1/rooms/r1/events", json=draft(text="SECRET-MESSAGE-TEXT"), headers=auth("tok-alice"))
    put_bundle(client, b"SECRET-BUNDLE-BYTES")
    client.get("/v1/rooms/r1/events?after_seq=0", headers=auth("tok-bob"))
    logged = caplog.text
    assert "tok-alice" not in logged and "tok-bob" not in logged
    assert "SECRET-MESSAGE-TEXT" not in logged and "SECRET-BUNDLE-BYTES" not in logged
