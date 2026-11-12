"""Archiving: old events move to compressed cold storage behind a horizon, and nothing a reader needs is lost."""

import random

import pytest
from fastapi.testclient import TestClient

from aletheore.council.housekeeping import archive_old_events
from aletheore.council.protocol import EventDraft, EventType, is_horizon
from aletheore.council.remote_store import RemoteRoomStore
from aletheore.council.server import create_app
from aletheore.council.server_config import ServerSettings
from aletheore.council.snapshots import load_state_fast, maybe_snapshot
from aletheore.council.sqlite_store import SqliteRoomStore
from aletheore.council.state import project
from aletheore.council.store import read_all
from tests.council.test_long_rooms import bulk_room
from tests.council.test_snapshots import script

DAY = 86400.0


def settings(**kw):
    return ServerSettings(archive_keep_events=kw.get("keep", 300), archive_min_age_days=kw.get("age", 30))


def test_archive_moves_old_events_and_readers_below_the_horizon_use_the_snapshot(tmp_path):
    store = script(SqliteRoomStore(tmp_path / "s.db"), "r1", random.Random(9), 3000, snapshot_every=500)
    before = read_all(store, "r1")
    full_state = project(before, host="alice")
    import time

    far_future = lambda: time.time() + 90 * DAY  # noqa: E731  (the events are now "old enough")
    moved = archive_old_events(store, settings(keep=300), far_future)
    assert moved.get("r1", 0) > 1000
    horizon = store.horizon("r1")
    live = store.read_after("r1", 0, 100_000)
    assert is_horizon(live[0]) and live[0].seq == horizon and all(e.seq > horizon for e in live[1:])  # a marker, then only live events
    assert len(live) < len(before) / 2  # the live table really shrank
    assert load_state_fast(store, "r1") == full_state  # the snapshot gives exactly the state a full replay gave
    assert [e.seq for e in store.read_after("r1", horizon, 100_000)] == [e.seq for e in live[1:]]  # reading from the horizon has no marker
    # and the old text is still there for anyone who wants it: the archive plus the live events are the original log, event for event
    restored = list(store.read_archive("r1")) + [e for e in live if not is_horizon(e)]
    assert [(e.seq, e.client_event_id, e.body) for e in restored] == [(e.seq, e.client_event_id, e.body) for e in before]


def test_nothing_is_archived_without_a_snapshot_or_before_the_events_are_old_enough(tmp_path):
    import time

    store = script(SqliteRoomStore(tmp_path / "s.db"), "r1", random.Random(4), 1500)  # no snapshots at all
    assert archive_old_events(store, settings(keep=100), lambda: time.time() + 90 * DAY) == {}
    maybe_snapshot(store, "r1", "alice", every=100)
    assert archive_old_events(store, settings(keep=100), time.time) == {}  # snapshot exists, but the events are minutes old
    assert store.horizon("r1") == 0 and len(read_all(store, "r1")) > 1500
    assert archive_old_events(store, ServerSettings(archive_keep_events=0), lambda: time.time() + 90 * DAY) == {}  # switched off


def test_appending_and_reading_carry_on_normally_after_archiving(tmp_path):
    import time

    store = script(SqliteRoomStore(tmp_path / "s.db"), "r1", random.Random(2), 2500, snapshot_every=500)
    archive_old_events(store, settings(keep=200), lambda: time.time() + 90 * DAY)
    last = store.last_seq("r1")
    new = store.append(EventDraft("r1", "after-archive", "t", EventType.CLAIM, "bob", None, None, {"text": "still here"}))
    assert new.seq == last + 1 and store.last_seq("r1") == last + 1
    again = store.append(EventDraft("r1", "after-archive", "t", EventType.CLAIM, "bob", None, None, {"text": "still here"}))
    assert again.seq == new.seq  # idempotency still holds for live events
    assert [e.body.get("text") for e in store.read_after("r1", last, 10)] == ["still here"]


def test_a_member_downloads_the_archive_over_http_and_a_stranger_cannot(tmp_path):
    import time

    store = script(SqliteRoomStore(tmp_path / "s.db"), "r1", random.Random(6), 2500, snapshot_every=500)
    original = read_all(store, "r1")
    archive_old_events(store, settings(keep=200), lambda: time.time() + 90 * DAY)
    app = create_app(store, {"ta": "alice", "tb": "bob", "tx": "mallory"}, tmp_path / "b")
    http = TestClient(app, base_url="http://x.test")
    member = RemoteRoomStore("http://x.test", "tb", client=http)
    old = list(member.read_archive("r1"))
    assert old and [e.seq for e in old] == list(range(1, len(old) + 1))
    assert [e.body for e in old] == [e.body for e in original[:len(old)]]
    assert http.get("/v1/rooms/r1/archive", headers={"Authorization": "Bearer tx"}).status_code in (403, 404)  # not a member
    page = member.read_after("r1", 0, 500)  # a remote reader below the horizon sees the marker first
    assert is_horizon(page[0])
    assert load_state_fast(member, "r1") == project(original, host="alice")


def test_archiving_is_safe_to_repeat_and_deleting_a_room_removes_its_archive(tmp_path):
    import time

    store = script(SqliteRoomStore(tmp_path / "s.db"), "r1", random.Random(8), 2200, snapshot_every=500)
    now = lambda: time.time() + 90 * DAY  # noqa: E731
    first = archive_old_events(store, settings(keep=200), now)
    again = archive_old_events(store, settings(keep=200), now)
    assert first and not again  # nothing more to move the second time
    assert store.delete_room("r1") and list(store.read_archive("r1")) == []
