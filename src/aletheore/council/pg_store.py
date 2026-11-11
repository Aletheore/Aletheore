"""Postgres RoomStore: the production store behind the room server.

Same contract as the SQLite store (see tests/store_contract.py), built for many rooms at once:

* Each room's events get gap-free sequence numbers from a counter on the room's own row. An append locks that one row
  (`SELECT ... FOR UPDATE`), so appends to the same room line up behind each other and appends to different rooms never wait
  for each other.
* Retries are safe: the idempotency check runs under the same lock, so the same (room, sender, client_event_id) sent from
  several places at once is stored once and every caller gets the same event back.
* A connection pool bounds how many database connections one server uses. A database that is down, or a pool that stays
  exhausted, shows up as `ConnectionError` (the server answers 503), never as a crash or a hang.
* Bodies are kept as JSON text, exactly as written, so unusual but legal content (control characters, odd unicode) survives.
* Schema changes are numbered migrations applied under an advisory lock, so several server instances can start together.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import timezone

import psycopg
from psycopg_pool import ConnectionPool, PoolClosed, PoolTimeout

from aletheore.council.protocol import Event, EventDraft, EventType, ProtocolError, check_draft, event_from_json, event_to_json, horizon_marker
from aletheore.council.store import CRYPTO_MODES, ROOM_MODES, RoomInfo

MIGRATIONS: list[tuple[int, str]] = [
    (1, """
    CREATE TABLE rooms (
        room_id          text PRIMARY KEY,
        host_participant text NOT NULL,
        title            text NOT NULL DEFAULT '',
        mode             text NOT NULL DEFAULT 'interactive',
        status           text NOT NULL DEFAULT 'open',
        last_seq         bigint NOT NULL DEFAULT 0,
        created_n        bigserial
    );
    CREATE TABLE members (
        room_id     text NOT NULL REFERENCES rooms (room_id) ON DELETE CASCADE,
        participant text NOT NULL,
        n           bigserial,
        PRIMARY KEY (room_id, participant)
    );
    CREATE TABLE events (
        room_id            text NOT NULL REFERENCES rooms (room_id) ON DELETE CASCADE,
        seq                bigint NOT NULL,
        event_id           text NOT NULL UNIQUE,
        client_event_id    text NOT NULL,
        thread_id          text NOT NULL,
        type               text NOT NULL,
        sender_participant text NOT NULL,
        sender_agent       text,
        reply_to           text,
        body               text NOT NULL,
        created_at         timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (room_id, seq),
        UNIQUE (room_id, sender_participant, client_event_id)
    );
    """),
    (2, """
    CREATE TABLE reg_codes (code_hash text PRIMARY KEY, handle text NOT NULL, can_host boolean NOT NULL,
        expires_at double precision NOT NULL, used boolean NOT NULL DEFAULT false);
    CREATE TABLE accounts (handle text PRIMARY KEY, can_host boolean NOT NULL, created_at double precision NOT NULL);
    CREATE TABLE devices (device_id text PRIMARY KEY, handle text NOT NULL, sign_vk text NOT NULL, box_pk text NOT NULL,
        created_at double precision NOT NULL, revoked_at double precision);
    CREATE INDEX devices_by_handle ON devices (handle);
    CREATE TABLE nonces (nonce text PRIMARY KEY, device_id text NOT NULL, expires_at double precision NOT NULL);
    CREATE TABLE sessions (token_hash text PRIMARY KEY, device_id text NOT NULL, expires_at double precision NOT NULL);
    CREATE INDEX sessions_by_device ON sessions (device_id);
    CREATE TABLE link_requests (request_id text PRIMARY KEY, handle text NOT NULL, device_id text NOT NULL, sign_vk text NOT NULL,
        box_pk text NOT NULL, words text NOT NULL, status text NOT NULL DEFAULT 'pending', grants bytea,
        expires_at double precision NOT NULL, created_at double precision NOT NULL);
    """),
    (3, """
    CREATE TABLE invites (invite_id text PRIMARY KEY, room_id text NOT NULL, host_device_id text NOT NULL, wrapped bytea NOT NULL,
        title text NOT NULL, expires_at double precision NOT NULL, status text NOT NULL DEFAULT 'open', created_at double precision NOT NULL);
    CREATE INDEX invites_by_room ON invites (room_id);
    CREATE TABLE join_requests (request_id text PRIMARY KEY, invite_id text NOT NULL, room_id text NOT NULL, handle text NOT NULL,
        device_id text NOT NULL, sign_vk text NOT NULL, box_pk text NOT NULL, words text NOT NULL, status text NOT NULL DEFAULT 'pending',
        created_at double precision NOT NULL);
    CREATE INDEX join_requests_by_room ON join_requests (room_id, status);
    """),
    (4, "ALTER TABLE rooms ADD COLUMN crypto text NOT NULL DEFAULT 'none';"),
    (5, """
    CREATE TABLE grants (room_id text NOT NULL, epoch integer NOT NULL, device_id text NOT NULL, grant_blob bytea NOT NULL,
        created_at double precision NOT NULL, PRIMARY KEY (room_id, epoch, device_id));
    """),
    (6, """
    ALTER TABLE rooms ADD COLUMN archived_through bigint NOT NULL DEFAULT 0;
    CREATE TABLE archive (
        room_id   text NOT NULL REFERENCES rooms (room_id) ON DELETE CASCADE,
        first_seq bigint NOT NULL,
        last_seq  bigint NOT NULL,
        data      bytea NOT NULL,
        PRIMARY KEY (room_id, first_seq)
    );
    """),
    (7, "ALTER TABLE invites ADD COLUMN claim_verifier bytea NOT NULL DEFAULT ''::bytea;"),
]

_EVENT_COLUMNS = ("room_id, seq, event_id, client_event_id, thread_id, type, sender_participant, sender_agent, "
                  "reply_to, body, created_at")


def _iso(ts) -> str:
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _row_to_event(row: tuple) -> Event:
    return Event(room_id=row[0], seq=row[1], event_id=row[2], client_event_id=row[3], thread_id=row[4], type=EventType(row[5]),
                 sender_participant=row[6], sender_agent=row[7], reply_to=row[8], body=json.loads(row[9]), created_at=_iso(row[10]))


def _options(schema: str | None) -> str | None:
    if schema is None:
        return None
    if not schema.replace("_", "").isalnum():
        raise ValueError("schema name must be letters, digits and underscores")
    return f"-c search_path={schema}"


def migrate(dsn: str, schema: str | None = None) -> int:
    """Apply any migrations not yet applied, in order, under an advisory lock. Returns the highest applied version."""
    kwargs = {"options": _options(schema)} if schema else {}
    with psycopg.connect(dsn, **kwargs) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"council-migrate:{schema or 'public'}",))
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                     "(version int PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
        for version, sql in sorted(MIGRATIONS):
            if version not in done:
                conn.execute(sql)
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
                done.add(version)
        return max(done) if done else 0


class PostgresRoomStore:
    def __init__(self, dsn: str, pool_size: int = 10, schema: str | None = None, pool_timeout_s: float = 30.0) -> None:
        self._schema = schema
        migrate(dsn, schema)
        options = _options(schema)
        # Autocommit: a single statement is one round trip (no BEGIN/COMMIT around it). Operations that need several statements
        # to succeed or fail together open an explicit transaction with `_tx`.
        self._pool = ConnectionPool(dsn, min_size=1, max_size=pool_size, timeout=pool_timeout_s, open=False,
                                    kwargs={"autocommit": True, **({"options": options} if options else {})})
        self._pool.open(wait=True, timeout=pool_timeout_s)

    def close(self) -> None:
        self._pool.close()

    @contextmanager
    def _conn(self, tx: bool = False):
        """A pooled connection (autocommit), or with `tx=True` inside one transaction: committed on success, rolled back on an
        error. Database trouble becomes ConnectionError (so callers can retry or answer 503); our own errors (KeyError,
        ProtocolError) pass through."""
        try:
            with self._pool.connection() as conn:
                if tx:
                    with conn.transaction():
                        yield conn
                else:
                    yield conn
        except PoolTimeout as exc:
            raise ConnectionError("database busy: no free connection") from exc
        except (PoolClosed, psycopg.OperationalError, psycopg.InterfaceError) as exc:
            raise ConnectionError(f"database unavailable: {exc}") from exc

    # -- rooms -----------------------------------------------------------------------------------------------------
    def create_room(self, room_id: str, host_participant: str, title: str = "", mode: str = "interactive", crypto: str = "none") -> None:
        if mode not in ROOM_MODES:
            raise ProtocolError(f"mode must be one of {', '.join(ROOM_MODES)}")
        if crypto not in CRYPTO_MODES:
            raise ProtocolError(f"crypto must be one of {', '.join(CRYPTO_MODES)}")
        with self._conn(tx=True) as conn:
            conn.execute("INSERT INTO rooms (room_id, host_participant, title, mode, crypto) VALUES (%s, %s, %s, %s, %s) "
                         "ON CONFLICT (room_id) DO NOTHING", (room_id, host_participant, title, mode, crypto))
            conn.execute("INSERT INTO members (room_id, participant) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                         (room_id, host_participant))

    def host_of(self, room_id: str) -> str:
        with self._conn() as conn:
            row = conn.execute("SELECT host_participant FROM rooms WHERE room_id=%s", (room_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown room: {room_id}")
        return row[0]

    def members_of(self, room_id: str) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute("SELECT participant FROM members WHERE room_id=%s ORDER BY n", (room_id,)).fetchall()
        return [r[0] for r in rows]

    def add_member(self, room_id: str, participant: str) -> None:
        with self._conn(tx=True) as conn:
            if conn.execute("SELECT 1 FROM rooms WHERE room_id=%s", (room_id,)).fetchone() is None:
                raise KeyError(f"unknown room: {room_id}")
            conn.execute("INSERT INTO members (room_id, participant) VALUES (%s, %s) ON CONFLICT DO NOTHING", (room_id, participant))

    def remove_member(self, room_id: str, participant: str) -> None:
        """KeyError for an unknown room or someone who is not a member; the host cannot be removed."""
        if self.host_of(room_id) == participant:
            raise ProtocolError("the host cannot be removed from their own room")
        with self._conn() as conn:
            if conn.execute("DELETE FROM members WHERE room_id=%s AND participant=%s", (room_id, participant)).rowcount == 0:
                raise KeyError(f"{participant} is not a member of {room_id}")

    def all_rooms(self) -> list[RoomInfo]:
        with self._conn() as conn:
            rows = conn.execute(self._INFO_SQL + " ORDER BY r.created_n").fetchall()
        return [self._info(r) for r in rows]

    def delete_room(self, room_id: str) -> bool:
        """Remove a room; its events and members go with it (foreign keys cascade). True when it existed."""
        with self._conn() as conn:
            return conn.execute("DELETE FROM rooms WHERE room_id=%s", (room_id,)).rowcount > 0

    def room_events_and_bytes(self, room_id: str) -> tuple[int, int]:
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(body::text)), 0) FROM events WHERE room_id=%s", (room_id,)).fetchone()
        return int(row[0]), int(row[1])

    def close_room(self, room_id: str) -> None:
        with self._conn() as conn:
            if conn.execute("UPDATE rooms SET status='closed' WHERE room_id=%s", (room_id,)).rowcount == 0:
                raise KeyError(f"unknown room: {room_id}")

    _INFO_SQL = (
        "SELECT r.room_id, r.host_participant, r.title, r.mode, r.status, r.last_seq, r.crypto, "
        "ARRAY(SELECT participant FROM members m WHERE m.room_id = r.room_id ORDER BY m.n), "
        "(SELECT created_at FROM events e WHERE e.room_id = r.room_id AND e.seq = r.last_seq) FROM rooms r"
    )

    @staticmethod
    def _info(row: tuple) -> RoomInfo:
        return RoomInfo(row[0], row[1], row[2], row[3], row[4], list(row[7]), row[5], _iso(row[8]) if row[8] else "", row[6])

    def room_info(self, room_id: str) -> RoomInfo:
        with self._conn() as conn:
            row = conn.execute(self._INFO_SQL + " WHERE r.room_id=%s", (room_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown room: {room_id}")
        return self._info(row)

    def list_rooms(self, participant: str) -> list[RoomInfo]:
        with self._conn() as conn:
            rows = conn.execute(
                self._INFO_SQL + " WHERE EXISTS (SELECT 1 FROM members m WHERE m.room_id = r.room_id AND m.participant=%s) "
                "ORDER BY r.created_n", (participant,)).fetchall()
        return [self._info(r) for r in rows]

    # -- events ----------------------------------------------------------------------------------------------------
    def append(self, draft: EventDraft) -> Event:
        check_draft(draft)  # every writer, not only the MCP path
        with self._conn(tx=True) as conn:
            # One lock per room: sequence numbers are allocated and the idempotency check is made while holding it.
            room = conn.execute("SELECT status, last_seq FROM rooms WHERE room_id=%s FOR UPDATE", (draft.room_id,)).fetchone()
            existing = conn.execute(
                f"SELECT {_EVENT_COLUMNS} FROM events WHERE room_id=%s AND sender_participant=%s AND client_event_id=%s",
                (draft.room_id, draft.sender_participant, draft.client_event_id)).fetchone()
            if existing is not None:
                return _row_to_event(existing)
            if room is None:
                raise KeyError(f"unknown room: {draft.room_id}")
            if room[0] == "closed":
                raise ProtocolError("this room is closed")
            seq = room[1] + 1
            event_id = f"evt_{uuid.uuid4().hex}"
            created = conn.execute(
                "INSERT INTO events (room_id, seq, event_id, client_event_id, thread_id, type, sender_participant, sender_agent, "
                "reply_to, body) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING created_at",
                (draft.room_id, seq, event_id, draft.client_event_id, draft.thread_id, draft.type.value, draft.sender_participant,
                 draft.sender_agent, draft.reply_to, json.dumps(draft.body, ensure_ascii=False))).fetchone()[0]
            conn.execute("UPDATE rooms SET last_seq=%s WHERE room_id=%s", (seq, draft.room_id))
        return Event(**{**draft.__dict__, "event_id": event_id, "seq": seq, "created_at": _iso(created)})

    def horizon(self, room_id: str) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT archived_through FROM rooms WHERE room_id=%s", (room_id,)).fetchone()
        return int(row[0]) if row else 0

    def read_after(self, room_id: str, after_seq: int, limit: int = 500) -> list[Event]:
        horizon = self.horizon(room_id)
        marker = [horizon_marker(room_id, horizon)] if 0 < horizon and after_seq < horizon else []
        with self._conn() as conn:
            rows = conn.execute(f"SELECT {_EVENT_COLUMNS} FROM events WHERE room_id=%s AND seq>%s ORDER BY seq LIMIT %s",
                                (room_id, max(after_seq, horizon), max(0, limit - len(marker)))).fetchall()
        return marker + [_row_to_event(r) for r in rows]

    def archive_before(self, room_id: str, seq: int, chunk: int = 1000) -> int:
        """Move events with seq <= `seq` into compressed chunks and remove them from the live table. Returns how many moved."""
        import gzip

        moved = 0
        with self._conn(tx=True) as conn:
            row = conn.execute("SELECT archived_through FROM rooms WHERE room_id=%s FOR UPDATE", (room_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown room: {room_id}")
            after = int(row[0])
            while after < seq:
                rows = conn.execute(f"SELECT {_EVENT_COLUMNS} FROM events WHERE room_id=%s AND seq>%s AND seq<=%s ORDER BY seq LIMIT %s",
                                    (room_id, after, seq, chunk)).fetchall()
                if not rows:
                    break
                events = [_row_to_event(r) for r in rows]
                lines = "\n".join(json.dumps(event_to_json(e), separators=(",", ":"), ensure_ascii=False) for e in events)
                conn.execute("INSERT INTO archive (room_id, first_seq, last_seq, data) VALUES (%s,%s,%s,%s) "
                             "ON CONFLICT (room_id, first_seq) DO UPDATE SET last_seq=EXCLUDED.last_seq, data=EXCLUDED.data",
                             (room_id, events[0].seq, events[-1].seq, gzip.compress(lines.encode("utf-8"), 6)))
                conn.execute("DELETE FROM events WHERE room_id=%s AND seq>=%s AND seq<=%s", (room_id, events[0].seq, events[-1].seq))
                after = events[-1].seq
                moved += len(events)
            if moved:
                conn.execute("UPDATE rooms SET archived_through=%s WHERE room_id=%s", (after, room_id))
        return moved

    def read_archive(self, room_id: str):
        import gzip

        with self._conn() as conn:
            chunks = [bytes(r[0]) for r in conn.execute("SELECT data FROM archive WHERE room_id=%s ORDER BY first_seq", (room_id,)).fetchall()]
        for blob in chunks:
            for line in gzip.decompress(blob).decode("utf-8").splitlines():
                yield event_from_json(json.loads(line))

    def last_seq(self, room_id: str) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT last_seq FROM rooms WHERE room_id=%s", (room_id,)).fetchone()
        return row[0] if row else 0
