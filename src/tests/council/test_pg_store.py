"""PostgresRoomStore: the same contract as SQLite plus what only a real database server can get wrong.

Needs a Postgres: set COUNCIL_TEST_PG_URL, for example
  docker run -d --rm --name council-pg-test -p 5433:5432 -e POSTGRES_PASSWORD=t -e POSTGRES_HOST_AUTH_METHOD=trust postgres:16
  COUNCIL_TEST_PG_URL=postgresql://postgres:t@localhost:5433/postgres pytest tests/test_pg_store.py
"""

import os
import threading
import time
import uuid

import pytest

from aletheore.council.protocol import EventDraft, EventType, ProtocolError
from tests.council.store_contract import RoomFeaturesContract, StoreContract, draft, q

URL = os.environ.get("COUNCIL_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set COUNCIL_TEST_PG_URL to a Postgres to run these tests")


@pytest.fixture
def schema():
    import psycopg

    name = f"t_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(f'CREATE SCHEMA "{name}"')
    yield name
    with psycopg.connect(URL, autocommit=True) as c:
        c.execute(f'DROP SCHEMA "{name}" CASCADE')


@pytest.fixture
def store(schema):
    from aletheore.council.pg_store import PostgresRoomStore

    s = PostgresRoomStore(URL, pool_size=40, schema=schema)
    yield s
    s.close()


class TestPostgresStore(StoreContract, RoomFeaturesContract):
    pass


def test_concurrent_appends_to_one_room_give_gap_free_unique_seq(store):
    store.create_room("r1", "alice")
    out, errors = [], []

    def worker(n):
        try:
            for i in range(40):
                out.append(store.append(draft(cid=f"w{n}-{i}", sender=f"p{n}")).seq)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert sorted(out) == list(range(1, 24 * 40 + 1))  # every seq exactly once, no gaps
    assert [e.seq for e in store.read_after("r1", 0, limit=2000)] == list(range(1, 24 * 40 + 1))


def test_idempotent_append_under_concurrent_retries(store):
    store.create_room("r1", "alice")
    got = []
    threads = [threading.Thread(target=lambda: got.append(store.append(draft(cid="same", text="x")))) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({e.event_id for e in got}) == 1 and len({e.seq for e in got}) == 1
    assert store.last_seq("r1") == 1


def test_different_rooms_do_not_block_each_other(store):
    store.create_room("r1", "alice")
    store.create_room("r2", "alice")
    started = threading.Event()
    release = threading.Event()
    import psycopg

    def hold_room_one():
        with psycopg.connect(URL, options=f"-c search_path={store._schema}") as c:
            c.execute("SELECT 1 FROM rooms WHERE room_id='r1' FOR UPDATE")
            started.set()
            release.wait(10)

    t = threading.Thread(target=hold_room_one)
    t.start()
    assert started.wait(5)
    t0 = time.monotonic()
    store.append(draft(room="r2", cid="x"))  # r1 is locked, r2 must not wait for it
    assert time.monotonic() - t0 < 1.0
    release.set()
    t.join()


def test_migrations_are_idempotent_ordered_and_safe_to_race(schema):
    from aletheore.council.pg_store import MIGRATIONS, PostgresRoomStore, migrate

    results = []
    threads = [threading.Thread(target=lambda: results.append(migrate(URL, schema=schema))) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results)[-1] == len(MIGRATIONS)
    assert migrate(URL, schema=schema) == len(MIGRATIONS)  # nothing left to apply, still fine
    s = PostgresRoomStore(URL, schema=schema)
    s.create_room("r1", "alice")
    s.close()


def test_pool_exhaustion_waits_then_fails_cleanly_as_connection_error(schema):
    from aletheore.council.pg_store import PostgresRoomStore

    s = PostgresRoomStore(URL, pool_size=1, schema=schema, pool_timeout_s=0.5)
    s.create_room("r1", "alice")
    started, release = threading.Event(), threading.Event()

    def hold():
        with s._pool.connection():
            started.set()
            release.wait(5)

    t = threading.Thread(target=hold)
    t.start()
    assert started.wait(3)
    with pytest.raises(ConnectionError, match="busy"):
        s.append(draft(cid="c"))
    release.set()
    t.join()
    assert s.append(draft(cid="c")).seq == 1  # and it works again once the connection is free
    s.close()


def test_read_after_pages_do_not_skip_or_repeat_while_writes_continue(store):
    store.create_room("r1", "alice")
    for i in range(50):
        store.append(draft(cid=f"a{i}"))
    stop = threading.Event()

    def writer():
        i = 0
        while not stop.is_set():
            store.append(draft(cid=f"w{i}"))
            i += 1

    t = threading.Thread(target=writer)
    t.start()
    seen, after = [], 0
    while True:
        page = store.read_after("r1", after, limit=7)
        if not page and len(seen) >= 50:
            break
        seen += [e.seq for e in page]
        after = page[-1].seq if page else after
        if len(seen) > 120:
            break
    stop.set()
    t.join()
    assert seen == list(range(1, len(seen) + 1))  # contiguous, no repeats


def test_bodies_survive_exactly_including_unicode_and_control_characters(store):
    store.create_room("r1", "alice")
    body = {"text": "café ☃ \U0001f600 nul:\x00 tab:\t line\nbreak", "nested": {"n": [1, 2.5, None, True]}}
    e = store.append(EventDraft("r1", "u1", "t", EventType.CLAIM, "alice", None, None, body))
    assert store.read_after("r1", 0)[0].body == body == e.body


def test_closed_room_and_unknown_room_errors_match_sqlite(store):
    with pytest.raises(KeyError):
        store.append(draft(room="nope", cid="x"))
    store.create_room("r1", "alice")
    store.close_room("r1")
    with pytest.raises(ProtocolError, match="closed"):
        store.append(draft(cid="x"))
    with pytest.raises(ProtocolError):
        store.create_room("r9", "alice", mode="chaos")


def test_an_unreachable_database_is_a_connection_error_not_a_crash(schema):
    from aletheore.council.pg_store import PostgresRoomStore

    s = PostgresRoomStore(URL, schema=schema, pool_timeout_s=1.0)
    s.create_room("r1", "alice")
    s._pool.close()  # simulate the connection source going away
    with pytest.raises(ConnectionError):
        s.append(draft(cid="c"))


def test_housekeeping_works_on_postgres_too(store, tmp_path):
    from aletheore.council.accounts import AccountService, PostgresAccountBackend
    from aletheore.council.admin import AdminTool
    from aletheore.council.identity import Keystore
    from aletheore.council.protocol import EventDraft, EventType

    class Clock:
        t = 1_800_000_000.0

        def __call__(self):
            return self.t

    clock = Clock()
    accounts = AccountService(PostgresAccountBackend(store), clock=clock)
    ident = Keystore(tmp_path / "k").load_or_create("a")
    accounts.redeem_registration_code(accounts.create_registration_code("alice", can_host=True), ident.public(), "alice")
    store.create_room("r1", "alice", title="T")
    store.append(EventDraft("r1", "c0", "room", EventType.SYSTEM, "alice", None, None, {"subtype": "room_created", "host": "alice"}))
    store.add_member("r1", "bob")
    invite_id, _ = accounts.create_invite("r1", ident.device_id, b"w", ttl=60, title="T")
    accounts.put_grant("r1", "alice", 1, ident.device_id, b"g")
    accounts.login_challenge(ident.device_id)
    clock.t += 3 * 86400
    removed = accounts.purge_expired()
    assert removed["invites"] == 1 and removed["nonces"] >= 1 and accounts.get_invite(invite_id) is None
    (tmp_path / "bundles" / "r1").mkdir(parents=True)
    (tmp_path / "bundles" / "r1" / "a.bundle").write_bytes(b"1")
    tool = AdminTool(store, accounts, tmp_path / "bundles", tmp_path / "audit.log")
    assert tool.rooms()[0]["events"] == 1 and tool.rooms()[0]["members"] == 2
    done = tool.delete_room("r1")
    assert done["files"] == 1 and done["grants"] == 1
    assert store.all_rooms() == [] and store.read_after("r1", 0) == [] and accounts.my_grants("r1", ident.device_id) == []


def test_archive_and_horizon_work_on_postgres_too(store):
    import random
    import time

    from aletheore.council.housekeeping import archive_old_events
    from aletheore.council.protocol import is_horizon
    from aletheore.council.server_config import ServerSettings
    from aletheore.council.snapshots import load_state_fast
    from aletheore.council.state import project
    from aletheore.council.store import read_all
    from tests.council.test_snapshots import script

    script(store, "r1", random.Random(3), 2200, snapshot_every=500)
    original = read_all(store, "r1")
    moved = archive_old_events(store, ServerSettings(archive_keep_events=200, archive_min_age_days=30), lambda: time.time() + 90 * 86400)
    assert moved.get("r1", 0) > 500
    live = store.read_after("r1", 0, 100_000)
    assert is_horizon(live[0]) and len(live) < len(original)
    assert load_state_fast(store, "r1") == project(original, host="alice")
    restored = list(store.read_archive("r1")) + [e for e in live if not is_horizon(e)]
    assert [(e.seq, e.client_event_id) for e in restored] == [(e.seq, e.client_event_id) for e in original]
    assert store.delete_room("r1") and store.all_rooms() == []
