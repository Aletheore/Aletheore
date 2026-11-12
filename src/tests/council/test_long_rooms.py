"""Long rooms: bounded reads for agents, and (below) the terminal opening a very large room quickly."""

from aletheore.council.agent_posts import MAX_EVENT_CHARS, MAX_READ_CHARS, read_text
from aletheore.council.protocol import Event, EventType


def ev(seq, text):
    return Event("r1", f"c{seq}", "t", EventType.CLAIM, "bob", None, None, {"text": text}, f"id{seq}", seq, "2026-11-10T00:00:00+00:00")


def test_agent_reads_are_windowed_and_never_exceed_the_size_cap():
    huge = [ev(i, "x" * 60_000) for i in range(1, 201)]  # 200 messages of 60,000 characters: 12 million characters in one page
    out = read_text(huge)
    assert len(out) <= MAX_READ_CHARS + MAX_EVENT_CHARS + 2000  # bounded, whatever the room holds
    assert "more message(s) not shown" in out and "after_seq=" in out  # and it says where to carry on
    assert "was cut here" in out  # a single enormous message is cut, not passed whole
    shown = out.count("message #")
    resumed = read_text(huge[shown:])  # following the note's advice returns the next window
    assert "message #" in resumed and len(resumed) <= MAX_READ_CHARS + MAX_EVENT_CHARS + 2000
    assert read_text([]) == "no new messages" and "hello" in read_text([ev(1, "hello")])


import asyncio
import os
import sqlite3
import time

import pytest

from aletheore.council import tui_nav
from aletheore.council.protocol import EventDraft
from aletheore.council.snapshots import maybe_snapshot, load_state_fast
from aletheore.council.sqlite_store import SqliteRoomStore
from aletheore.council.state import project
from aletheore.council.store import read_all
from aletheore.council.tui import TAIL_WINDOW, CouncilApp, MessageView


def bulk_room(path, n, room="r1", host="alice"):
    """A room with `n` events, made by inserting rows directly (appending a hundred thousand one by one would take minutes)."""
    store = SqliteRoomStore(path)
    store.create_room(room, host, title="Very long room")
    store.add_member(room, "bob")
    rows = []
    for i in range(1, n + 1):
        if i == 1:
            etype, thread, who, body = "system", "room", host, '{"subtype": "room_created", "host": "alice"}'
        elif i == 2:
            etype, thread, who, body = "system", "room", host, '{"subtype": "area_defined", "name": "core", "globs": ["core/**"], "owner": "alice", "agent_id": "alice-codex"}'
        else:
            etype, thread, who, body = "claim", "t", ("bob" if i % 3 else "alice"), '{"text": "message number %d about the cache"}' % i
        rows.append((room, i, f"evt_{i}", f"c{i}", thread, etype, who, None, None, body, "2026-11-10T09:00:00+00:00"))
    with sqlite3.connect(path) as c:
        c.executemany("INSERT INTO events (room_id, seq, event_id, client_event_id, thread_id, type, sender_participant, sender_agent, reply_to, body, created_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    return store


async def settle(pilot, n=10):
    for _ in range(n):
        await pilot.pause(0.1)


def test_tui_opens_a_100000_event_room_within_the_time_budget_and_renders_only_the_recent_window(tmp_path):
    store = bulk_room(tmp_path / "big.db", 100_000)
    assert maybe_snapshot(store, "r1", "alice", every=1000) == 100_000  # the host's worker has written a snapshot
    for i in range(30):  # and a few events came after it
        store.append(EventDraft("r1", f"new{i}", "t", EventType.CLAIM, "bob", None, None, {"text": f"after the snapshot {i}"}))
    full = project(read_all(store, "r1"), host="alice")

    async def go():
        app = CouncilApp(store, "r1", "alice", poll_s=0.2)
        started = time.time()
        async with app.run_test(size=(140, 40)) as pilot:
            for _ in range(100):
                if len(app.query(MessageView)) > 0:
                    break
                await pilot.pause(0.1)
            opened = time.time() - started
            await settle(pilot, 5)
            assert opened < 8.0, f"took {opened:.1f}s to show a 100,030 event room"
            assert len(app.tail.all) <= 2 * TAIL_WINDOW and app.tail.hidden_before() > 90_000  # only the end of the room is loaded
            assert len(app.query(MessageView)) <= tui_nav.WINDOW + 10  # and only the recent window is on screen
            assert app.state == full  # the state is the real one, from the snapshot and its tail
            assert "earlier message" in str(app.query_one("FoldRow").render())

    asyncio.run(go())


@pytest.mark.skipif(not os.environ.get("COUNCIL_SLOW"), reason="set COUNCIL_SLOW=1 for the one-million-event test")
def test_a_one_million_event_room_has_bounded_append_and_read_latency(tmp_path):
    store = bulk_room(tmp_path / "huge.db", 1_000_000)
    last = store.last_seq("r1")
    t = time.perf_counter()
    for i in range(50):
        store.append(EventDraft("r1", f"n{i}", "t", EventType.CLAIM, "bob", None, None, {"text": "x"}))
    append_ms = (time.perf_counter() - t) / 50 * 1000
    t = time.perf_counter()
    for _ in range(50):
        store.read_after("r1", last - 40, 500)
    read_ms = (time.perf_counter() - t) / 50 * 1000
    t = time.perf_counter()
    assert maybe_snapshot(store, "r1", "alice", every=1000) is not None
    snapshot_s = time.perf_counter() - t
    t = time.perf_counter()
    load_state_fast(store, "r1")
    open_s = time.perf_counter() - t
    print(f"1M events: append {append_ms:.2f} ms, tail read {read_ms:.2f} ms, first snapshot {snapshot_s:.1f} s, open from snapshot {open_s:.2f} s")
    assert append_ms < 20 and read_ms < 20 and open_s < 2.0
