"""SQLite RoomStore. One short-lived connection per call, so it is safe across threads."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from aletheore.council.protocol import Event, EventDraft, EventType, ProtocolError, check_draft, event_from_json, event_to_json
from aletheore.council.store import CRYPTO_MODES, ROOM_MODES, RoomInfo

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
    room_id TEXT PRIMARY KEY,
    host_participant TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'interactive',
    status TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS archive (
    room_id TEXT NOT NULL,
    first_seq INTEGER NOT NULL,
    last_seq INTEGER NOT NULL,
    data BLOB NOT NULL,
    PRIMARY KEY (room_id, first_seq)
);
CREATE TABLE IF NOT EXISTS members (
    room_id TEXT NOT NULL,
    participant TEXT NOT NULL,
    PRIMARY KEY (room_id, participant)
);
CREATE TABLE IF NOT EXISTS events (
    room_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    event_id TEXT NOT NULL UNIQUE,
    client_event_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    type TEXT NOT NULL,
    sender_participant TEXT NOT NULL,
    sender_agent TEXT,
    reply_to TEXT,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (room_id, seq),
    UNIQUE (room_id, sender_participant, client_event_id)
);
"""

_COLUMNS = (
    "room_id, seq, event_id, client_event_id, thread_id, type, "
    "sender_participant, sender_agent, reply_to, body, created_at"
)


def _row_to_event(row: tuple) -> Event:
    return Event(
        room_id=row[0],
        seq=row[1],
        event_id=row[2],
        client_event_id=row[3],
        thread_id=row[4],
        type=EventType(row[5]),
        sender_participant=row[6],
        sender_agent=row[7],
        reply_to=row[8],
        body=json.loads(row[9]),
        created_at=row[10],
    )


class SqliteRoomStore:
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            self._upgrade_rooms(conn)
            self._migrate(conn)

    @staticmethod
    def _upgrade_rooms(conn: sqlite3.Connection) -> None:
        """Databases from before rooms had titles, modes, status and members: add the columns, make hosts members."""
        have = {r[1] for r in conn.execute("PRAGMA table_info(rooms)")}
        for col, ddl in (("title", "TEXT NOT NULL DEFAULT ''"), ("mode", "TEXT NOT NULL DEFAULT 'interactive'"),
                         ("status", "TEXT NOT NULL DEFAULT 'open'"), ("crypto", "TEXT NOT NULL DEFAULT 'none'"), ("archived_through", "INTEGER NOT NULL DEFAULT 0")):
            if col not in have:
                conn.execute(f"ALTER TABLE rooms ADD COLUMN {col} {ddl}")
        conn.execute("INSERT OR IGNORE INTO members (room_id, participant) SELECT room_id, host_participant FROM rooms")

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Databases from before stage 2 made idempotency unique per room; rebuild the table so it is per sender."""
        for _, name, unique, *_rest in conn.execute("PRAGMA index_list(events)").fetchall():
            if unique and [r[2] for r in conn.execute(f"PRAGMA index_info('{name}')")] == ["room_id", "client_event_id"]:
                break
        else:
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("ALTER TABLE events RENAME TO events_old")
            conn.execute("CREATE TABLE events (" + _SCHEMA.split("CREATE TABLE IF NOT EXISTS events (", 1)[1].split(");", 1)[0] + ")")
            conn.execute(f"INSERT INTO events ({_COLUMNS}) SELECT {_COLUMNS} FROM events_old")
            conn.execute("DROP TABLE events_old")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=30, isolation_level=None)
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def create_room(self, room_id: str, host_participant: str, title: str = "", mode: str = "interactive", crypto: str = "none") -> None:
        if mode not in ROOM_MODES:
            raise ProtocolError(f"mode must be one of {', '.join(ROOM_MODES)}")
        if crypto not in CRYPTO_MODES:
            raise ProtocolError(f"crypto must be one of {', '.join(CRYPTO_MODES)}")
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO rooms (room_id, host_participant, title, mode, crypto) VALUES (?, ?, ?, ?, ?)",
                (room_id, host_participant, title, mode, crypto),
            )
            conn.execute("INSERT OR IGNORE INTO members (room_id, participant) VALUES (?, ?)", (room_id, host_participant))

    def members_of(self, room_id: str) -> list[str]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT participant FROM members WHERE room_id=? ORDER BY rowid", (room_id,)).fetchall()
        return [r[0] for r in rows]

    def add_member(self, room_id: str, participant: str) -> None:
        self.host_of(room_id)  # KeyError for an unknown room
        with closing(self._connect()) as conn:
            conn.execute("INSERT OR IGNORE INTO members (room_id, participant) VALUES (?, ?)", (room_id, participant))

    def remove_member(self, room_id: str, participant: str) -> None:
        """KeyError for an unknown room or someone who is not a member; the host cannot be removed."""
        if self.host_of(room_id) == participant:
            raise ProtocolError("the host cannot be removed from their own room")
        with closing(self._connect()) as conn:
            if conn.execute("DELETE FROM members WHERE room_id=? AND participant=?", (room_id, participant)).rowcount == 0:
                raise KeyError(f"{participant} is not a member of {room_id}")

    def close_room(self, room_id: str) -> None:
        self.host_of(room_id)
        with closing(self._connect()) as conn:
            conn.execute("UPDATE rooms SET status='closed' WHERE room_id=?", (room_id,))

    def _info(self, conn: sqlite3.Connection, row: tuple) -> RoomInfo:
        room_id = row[0]
        last = conn.execute("SELECT seq, created_at FROM events WHERE room_id=? ORDER BY seq DESC LIMIT 1", (room_id,)).fetchone()
        members = [r[0] for r in conn.execute("SELECT participant FROM members WHERE room_id=? ORDER BY rowid", (room_id,))]
        return RoomInfo(room_id, row[1], row[2], row[3], row[4], members, last[0] if last else 0, last[1] if last else "", row[5])

    def room_info(self, room_id: str) -> RoomInfo:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT room_id, host_participant, title, mode, status, crypto FROM rooms WHERE room_id=?", (room_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown room: {room_id}")
            return self._info(conn, row)

    def list_rooms(self, participant: str) -> list[RoomInfo]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT r.room_id, r.host_participant, r.title, r.mode, r.status, r.crypto FROM rooms r JOIN members m ON m.room_id = r.room_id "
                "WHERE m.participant=? ORDER BY r.rowid", (participant,)).fetchall()
            return [self._info(conn, r) for r in rows]

    def all_rooms(self) -> list[RoomInfo]:
        """Every room in this database (for the host-run server shutting down; not part of the shared store interface)."""
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT room_id, host_participant, title, mode, status, crypto FROM rooms ORDER BY rowid").fetchall()
            return [self._info(conn, r) for r in rows]

    def delete_room(self, room_id: str) -> bool:
        """Remove a room with its events and members. True when it existed. (Files and account records are removed by the caller.)"""
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            gone = conn.execute("DELETE FROM rooms WHERE room_id=?", (room_id,)).rowcount > 0
            conn.execute("DELETE FROM events WHERE room_id=?", (room_id,))
            conn.execute("DELETE FROM members WHERE room_id=?", (room_id,))
            conn.execute("DELETE FROM archive WHERE room_id=?", (room_id,))
            conn.execute("COMMIT")
        return gone

    def room_events_and_bytes(self, room_id: str) -> tuple[int, int]:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(body)), 0) FROM events WHERE room_id=?", (room_id,)).fetchone()
        return int(row[0]), int(row[1])

    def host_of(self, room_id: str) -> str:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT host_participant FROM rooms WHERE room_id=?", (room_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown room: {room_id}")
        return row[0]

    def append(self, draft: EventDraft) -> Event:
        check_draft(draft)  # defense in depth: every writer, not just the MCP path
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    f"SELECT {_COLUMNS} FROM events WHERE room_id=? AND sender_participant=? AND client_event_id=?",
                    (draft.room_id, draft.sender_participant, draft.client_event_id),
                ).fetchone()
                if existing is not None:
                    conn.execute("COMMIT")
                    return _row_to_event(existing)
                state = conn.execute("SELECT status FROM rooms WHERE room_id=?", (draft.room_id,)).fetchone()
                if state is None:
                    raise KeyError(f"unknown room: {draft.room_id}")
                if state[0] == "closed":
                    raise ProtocolError("this room is closed")
                seq = conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE room_id=?", (draft.room_id,)
                ).fetchone()[0]
                event_id = f"evt_{uuid.uuid4().hex}"
                created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                conn.execute(
                    f"INSERT INTO events ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        draft.room_id, seq, event_id, draft.client_event_id, draft.thread_id,
                        draft.type.value, draft.sender_participant, draft.sender_agent,
                        draft.reply_to, json.dumps(draft.body, ensure_ascii=False), created_at,
                    ),
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return Event(**{**draft.__dict__, "event_id": event_id, "seq": seq, "created_at": created_at})

    def horizon(self, room_id: str) -> int:
        """Events with seq up to this have been archived (0: nothing archived)."""
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT archived_through FROM rooms WHERE room_id=?", (room_id,)).fetchone()
        return int(row[0]) if row else 0

    def read_after(self, room_id: str, after_seq: int, limit: int = 500) -> list[Event]:
        from aletheore.council.protocol import horizon_marker

        horizon = self.horizon(room_id)
        marker = [horizon_marker(room_id, horizon)] if 0 < horizon and after_seq < horizon else []
        start = max(after_seq, horizon)
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM events WHERE room_id=? AND seq>? ORDER BY seq ASC LIMIT ?",
                (room_id, start, max(0, limit - len(marker))),
            ).fetchall()
        return marker + [_row_to_event(r) for r in rows]

    def archive_before(self, room_id: str, seq: int, chunk: int = 1000) -> int:
        """Move events with seq <= `seq` into compressed chunks (one row per `chunk` events) and remove them from the live table.
        Returns how many moved. Reading below the new horizon returns a horizon marker instead of them."""
        import gzip

        moved = 0
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            done = conn.execute("SELECT archived_through FROM rooms WHERE room_id=?", (room_id,)).fetchone()
            if done is None:
                conn.execute("ROLLBACK")
                raise KeyError(f"unknown room: {room_id}")
            after = int(done[0])
            while after < seq:
                rows = conn.execute(f"SELECT {_COLUMNS} FROM events WHERE room_id=? AND seq>? AND seq<=? ORDER BY seq LIMIT ?",
                                    (room_id, after, seq, chunk)).fetchall()
                if not rows:
                    break
                events = [_row_to_event(r) for r in rows]
                lines = "\n".join(json.dumps(event_to_json(e), separators=(",", ":"), ensure_ascii=False) for e in events)
                conn.execute("INSERT OR REPLACE INTO archive (room_id, first_seq, last_seq, data) VALUES (?,?,?,?)",
                             (room_id, events[0].seq, events[-1].seq, gzip.compress(lines.encode("utf-8"), 6)))
                conn.execute("DELETE FROM events WHERE room_id=? AND seq>=? AND seq<=?", (room_id, events[0].seq, events[-1].seq))
                after = events[-1].seq
                moved += len(events)
            if moved:
                conn.execute("UPDATE rooms SET archived_through=? WHERE room_id=?", (after, room_id))
            conn.execute("COMMIT")
        return moved

    def read_archive(self, room_id: str):
        """Every archived event, oldest first, as Event objects (the full old history for a member who wants it)."""
        import gzip

        with closing(self._connect()) as conn:
            chunks = [bytes(r[0]) for r in conn.execute("SELECT data FROM archive WHERE room_id=? ORDER BY first_seq", (room_id,)).fetchall()]
        for blob in chunks:
            for line in gzip.decompress(blob).decode("utf-8").splitlines():
                yield event_from_json(json.loads(line))

    def last_seq(self, room_id: str) -> int:
        with closing(self._connect()) as conn:
            return conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM events WHERE room_id=?", (room_id,)
            ).fetchone()[0]
