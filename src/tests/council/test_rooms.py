import sqlite3

import pytest
from fastapi.testclient import TestClient

from aletheore.council.protocol import EventDraft, EventType, ProtocolError
from aletheore.council.remote_store import RemoteRoomStore
from aletheore.council.server import create_app
from aletheore.council.sqlite_store import SqliteRoomStore


def q(room, cid, sender="alice", text="hi"):
    return EventDraft(room, cid, "room", EventType.QUESTION, sender, None, None, {"text": text, "kind": "chat"})


# ---- the store ---------------------------------------------------------------------------------------------------
def test_old_database_without_the_new_columns_is_upgraded(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE rooms (room_id TEXT PRIMARY KEY, host_participant TEXT NOT NULL);
    INSERT INTO rooms VALUES ('old1', 'alice');
    """)
    conn.commit()
    conn.close()
    s = SqliteRoomStore(path)
    assert s.room_info("old1").members == ["alice"] and s.room_info("old1").status == "open"


# ---- the server --------------------------------------------------------------------------------------------------
@pytest.fixture
def api(tmp_path):
    app = create_app(SqliteRoomStore(tmp_path / "s.db"), {"ta": "alice", "tb": "bob", "tc": "carol"}, tmp_path / "b")
    client = TestClient(app)
    mk = lambda tok: RemoteRoomStore("http://t", tok, client=client, backoff_s=0)
    return mk("ta"), mk("tb"), mk("tc"), client


def test_an_uninvited_participant_cannot_read_post_or_fetch_anything(api, tmp_path):
    alice, bob, carol, client = api
    alice.create_room("r1", "alice", title="private")
    alice.append(q("r1", "c1"))
    for call in (lambda: carol.read_after("r1", 0), lambda: carol.append(q("r1", "x", "carol")),
                 lambda: carol.host_of("r1"), lambda: carol.last_seq("r1"), lambda: carol.room_info("r1")):
        with pytest.raises(PermissionError, match="not a member"):
            call()
    img = tmp_path / "i.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 20)
    import hashlib
    sha = hashlib.sha256(img.read_bytes()).hexdigest()
    with pytest.raises(PermissionError):
        carol.put_blob("r1", sha, img)
    assert carol.list_rooms() == []


def test_only_the_host_can_invite_and_the_invited_can_then_join(api):
    alice, bob, carol, _ = api
    alice.create_room("r1", "alice", title="team")
    with pytest.raises(PermissionError):
        bob.add_member("r1", "bob")  # cannot invite yourself
    with pytest.raises(ProtocolError, match="not known"):
        alice.add_member("r1", "mallory")  # not someone this server knows
    alice.add_member("r1", "bob")
    assert bob.read_after("r1", 0) == [] and [r.room_id for r in bob.list_rooms()] == ["r1"]
    bob.append(q("r1", "b1", "bob"))
    with pytest.raises(PermissionError):
        bob.add_member("r1", "carol")  # members cannot invite others, only the host


def test_only_the_host_can_close_and_a_closed_room_refuses_new_events(api):
    alice, bob, _, _ = api
    alice.create_room("r1", "alice")
    alice.add_member("r1", "bob")
    with pytest.raises(PermissionError):
        bob.close_room("r1")
    alice.close_room("r1")
    with pytest.raises(ProtocolError, match="closed"):
        alice.append(q("r1", "c1"))
    assert alice.room_info("r1").status == "closed"


def test_room_creation_is_bounded_per_host_and_titles_are_checked(tmp_path):
    app = create_app(SqliteRoomStore(tmp_path / "s.db"), {"ta": "alice"}, tmp_path / "b", max_rooms_per_host=2)
    alice = RemoteRoomStore("http://t", "ta", client=TestClient(app), backoff_s=0)
    alice.create_room("r1", "alice")
    alice.create_room("r2", "alice", title="ok")
    with pytest.raises(ProtocolError):
        alice.create_room("r3", "alice")
    with pytest.raises(ProtocolError):
        alice.create_room("r4", "alice", title="x" * 200)
    with pytest.raises(ProtocolError):
        alice.create_room("r5", "alice", mode="chaos")
