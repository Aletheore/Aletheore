import pytest

from aletheore.council.sqlite_store import SqliteRoomStore
from tests.council.store_contract import RoomFeaturesContract, StoreContract, draft


class TestSqliteStore(StoreContract, RoomFeaturesContract):
    @pytest.fixture
    def store(self, tmp_path):
        return SqliteRoomStore(tmp_path / "rooms.db")

    def test_survives_reopen(self, tmp_path):
        path = tmp_path / "rooms.db"
        s1 = SqliteRoomStore(path)
        s1.create_room("r1", "alice")
        s1.append(draft(cid="c1"))
        s2 = SqliteRoomStore(path)
        assert [e.seq for e in s2.read_after("r1", 0)] == [1]
        assert s2.append(draft(cid="c2")).seq == 2


def test_store_rejects_oversized_body_even_if_caller_skipped_validation(tmp_path):
    from aletheore.council.protocol import MAX_BODY_BYTES, ProtocolError
    s = SqliteRoomStore(tmp_path / "r.db")
    s.create_room("r1", "alice")
    with pytest.raises(ProtocolError):
        s.append(draft(text="x" * (MAX_BODY_BYTES + 10)))
    assert s.last_seq("r1") == 0


def test_old_database_is_migrated_to_per_sender_idempotency(tmp_path):
    import sqlite3
    from aletheore.council.protocol import EventDraft, EventType
    from aletheore.council.sqlite_store import SqliteRoomStore

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE rooms (room_id TEXT PRIMARY KEY, host_participant TEXT NOT NULL);
    CREATE TABLE events (room_id TEXT NOT NULL, seq INTEGER NOT NULL, event_id TEXT NOT NULL UNIQUE,
      client_event_id TEXT NOT NULL, thread_id TEXT NOT NULL, type TEXT NOT NULL, sender_participant TEXT NOT NULL,
      sender_agent TEXT, reply_to TEXT, body TEXT NOT NULL, created_at TEXT NOT NULL,
      PRIMARY KEY (room_id, seq), UNIQUE (room_id, client_event_id));
    INSERT INTO rooms VALUES ('r1', 'alice');
    INSERT INTO events VALUES ('r1', 1, 'e1', 'same', 't', 'question', 'alice', NULL, NULL, '{"text": "hi"}', '2026-01-01T00:00:00+00:00');
    """)
    conn.commit()
    conn.close()
    store = SqliteRoomStore(path)
    store.append(EventDraft("r1", "same", "t", EventType.QUESTION, "bob", None, None, {"text": "yo"}))
    events = store.read_after("r1", 0)
    assert [(e.sender_participant, e.seq) for e in events] == [("alice", 1), ("bob", 2)]
