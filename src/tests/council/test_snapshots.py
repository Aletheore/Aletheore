"""Snapshots: opening a long room from a snapshot and its tail gives exactly what replaying the whole log gives."""

import base64
import json
import random
import tracemalloc
import zlib

import pytest

from aletheore.council.protocol import Event, EventDraft, EventType
from aletheore.council.snapshots import (SNAPSHOT_EVERY, latest_snapshot, load_state_fast, make_snapshot_body, maybe_snapshot, read_snapshot,
                                         state_from_json, state_hash, state_to_json)
from aletheore.council.sqlite_store import SqliteRoomStore
from aletheore.council.state import Projector, load_state, project
from aletheore.council.store import read_all

PEOPLE = ["alice", "bob", "carol"]


def script(store, room_id, rng, n, snapshot_every=None):
    """A room with a random but realistic life: areas, tasks, claims, plans, decisions, verdict approvals, autonomy, and some junk
    (events from people who may not do that, malformed bodies). Optionally writes snapshots as the host's worker would."""
    store.create_room(room_id, "alice", title=room_id)
    for p in PEOPLE[1:]:
        store.add_member(room_id, p)
    post = lambda cid, etype, body, who="alice", thread="room", agent=None: store.append(EventDraft(room_id, cid, thread, etype, who, agent, None, body))  # noqa: E731
    post("c0", EventType.SYSTEM, {"subtype": "room_created", "host": "alice"})
    areas, tasks, verdicts, plans = [], [], [], []
    for i in range(n):
        r = rng.random()
        who = rng.choice(PEOPLE)
        cid = f"e{i}"
        if r < 0.05:
            name = f"area{len(areas)}"
            areas.append(name)
            post(cid, EventType.SYSTEM, {"subtype": "area_defined", "name": name, "globs": [f"{name}/**"], "owner": rng.choice(PEOPLE), "agent_id": f"{name}-agent"}, who)
        elif r < 0.20 and areas:
            tid = f"t{len(tasks)}"
            tasks.append(tid)
            post(cid, EventType.TASK, {"op": "request", "task_id": tid, "area": rng.choice(areas), "instruction": "do it"}, who)
        elif r < 0.30 and tasks:
            post(cid, EventType.TASK, {"op": rng.choice(["started", "finished", "failed", "blocked"]), "task_id": rng.choice(tasks), "files": []}, who)
        elif r < 0.36 and areas:
            vid = f"v{len(verdicts)}"
            verdicts.append(vid)
            post(cid, EventType.DECISION, {"verdict_id": vid, "decision": "ship it", "affected_area": rng.choice(areas)}, who)
        elif r < 0.42 and verdicts:
            post(cid, EventType.SYSTEM, {"subtype": rng.choice(["verdict_approved", "verdict_rejected", "verdict_applied"]), "verdict_id": rng.choice(verdicts)}, who)
        elif r < 0.47 and areas:
            pid = f"p{len(plans)}"
            plans.append(pid)
            post(cid, EventType.PROPOSAL, {"plan_id": pid, "reply": "plan", "tasks": [{"area": rng.choice(areas), "instruction": "x"}]}, who, thread=f"th{i}")
        elif r < 0.50 and plans:
            post(cid, EventType.SYSTEM, {"subtype": rng.choice(["plan_approved", "plan_rejected", "plan_dispatched"]), "plan_id": rng.choice(plans)}, who)
        elif r < 0.52:
            post(cid, EventType.SYSTEM, {"subtype": "autonomy_set", "on": rng.choice([True, False, "junk"])}, who)
        elif r < 0.53:
            post(cid, EventType.SYSTEM, {"subtype": "room_done", "outcome": rng.choice(["done", "limit", "stopped", "bogus"]), "summary": "s"}, who)
        elif r < 0.56:
            post(cid, EventType.QUESTION, {"text": "q", "kind": rng.choice(["prompt", "chat", "debate"])}, who, thread=f"th{rng.randrange(40)}")
        else:
            post(cid, EventType.CLAIM, {"text": f"message {i}"}, who, thread="t")
        if snapshot_every and (i + 1) % snapshot_every == 0:
            maybe_snapshot(store, room_id, "alice", every=snapshot_every)
    return store


@pytest.mark.parametrize("seed", range(8))
def test_snapshot_plus_tail_equals_full_replay(tmp_path, seed):
    rng = random.Random(seed)
    store = script(SqliteRoomStore(tmp_path / "s.db"), "r1", rng, rng.randrange(300, 1200), snapshot_every=rng.choice([50, 100, 250]))
    full = project(read_all(store, "r1"), host="alice")
    fast = load_state_fast(store, "r1")
    assert fast == full, f"seed {seed}"
    assert state_hash(fast) == state_hash(full)
    assert latest_snapshot(store, "r1", "alice") is not None  # a snapshot really was used


def test_state_survives_the_round_trip_through_json_exactly(tmp_path):
    store = script(SqliteRoomStore(tmp_path / "s.db"), "r1", random.Random(3), 400)
    state = project(read_all(store, "r1"), host="alice")
    assert state_from_json(json.loads(json.dumps(state_to_json(state)))) == state


def corrupt_cases(good: Event, state, host="alice"):
    body = dict(good.body)
    yield "wrong hash", {**body, "hash": "0" * 64}
    yield "garbled state", {**body, "state_z": "!!not base64!!"}
    yield "not compressed", {**body, "state_z": base64.b64encode(b"plain").decode()}
    forged = state_to_json(state)
    forged["autonomy"] = not forged["autonomy"]
    raw = json.dumps(forged, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    yield "state that does not match its hash", {**body, "state_z": base64.b64encode(zlib.compress(raw)).decode()}
    yield "covers the future", {**body, "covers_seq": good.seq + 5}
    yield "missing field", {k: v for k, v in body.items() if k != "covers_seq"}


def test_a_bad_or_missing_snapshot_falls_back_to_full_replay(tmp_path):
    store = script(SqliteRoomStore(tmp_path / "s.db"), "r1", random.Random(5), 600, snapshot_every=100)
    full = project(read_all(store, "r1"), host="alice")
    snaps = [e for e in read_all(store, "r1") if e.body.get("subtype") == "snapshot"]
    assert snaps
    good = snaps[-1]
    for label, body in corrupt_cases(good, full):
        bad = Event(good.room_id, good.client_event_id, good.thread_id, good.type, good.sender_participant, None, None, body, good.event_id, good.seq, good.created_at)
        assert read_snapshot(bad, "alice") is None, label
    forged_sender = Event(good.room_id, "x", good.thread_id, good.type, "bob", None, None, good.body, "id", good.seq, good.created_at)
    assert read_snapshot(forged_sender, "alice") is None  # only the host's snapshot counts
    # a store whose every snapshot is damaged still opens correctly, by replaying everything
    other = script(SqliteRoomStore(tmp_path / "o.db"), "r1", random.Random(5), 600)  # the same room with no snapshots at all
    assert load_state_fast(other, "r1") == full


def test_a_member_away_for_weeks_catches_up_through_the_snapshot_reading_only_the_tail(tmp_path):
    store = script(SqliteRoomStore(tmp_path / "s.db"), "r1", random.Random(11), 3000, snapshot_every=500)

    class Counting:
        def __init__(self, inner):
            self.inner, self.read = inner, 0

        def read_after(self, room, after, limit=500):
            page = self.inner.read_after(room, after, limit)
            self.read += len(page)
            return page

        def __getattr__(self, name):
            return getattr(self.inner, name)

    counted = Counting(store)
    state = load_state(counted, "r1")
    assert state == project(read_all(store, "r1"), host="alice")
    total = store.last_seq("r1")
    assert counted.read < total / 2, f"read {counted.read} of {total} events: the snapshot was not used"


def test_a_new_joiner_bootstraps_from_the_snapshot_and_sees_the_same_state(tmp_path):
    store = script(SqliteRoomStore(tmp_path / "s.db"), "r1", random.Random(21), 1500, snapshot_every=300)
    store.add_member("r1", "newbie")
    assert load_state_fast(store, "r1") == project(read_all(store, "r1"), host="alice")


def test_no_snapshot_is_written_until_enough_events_have_passed_and_it_is_not_repeated(tmp_path):
    store = script(SqliteRoomStore(tmp_path / "s.db"), "r1", random.Random(2), 250)
    assert maybe_snapshot(store, "r1", "alice", every=1000) is None  # not enough yet
    first = maybe_snapshot(store, "r1", "alice", every=100)
    assert first is not None
    assert maybe_snapshot(store, "r1", "alice", every=100) is None  # nothing new since
    assert load_state_fast(store, "r1") == project(read_all(store, "r1"), host="alice")


def test_incremental_projector_memory_stays_flat_over_100000_events():
    def events():
        for i in range(100_000):
            kind = i % 4
            if kind == 0:
                yield Event("r1", f"a{i}", "t", EventType.CLAIM, "bob", None, None, {"text": "hello world " * 20}, f"id{i}", i + 1, "2026-11-10T00:00:00+00:00")
            elif kind == 1:
                yield Event("r1", f"b{i}", f"th{i % 50}", EventType.QUESTION, "bob", None, None, {"text": "q", "kind": "chat"}, f"id{i}", i + 1, "2026-11-10T00:00:00+00:00")
            else:
                yield Event("r1", f"c{i}", "room", EventType.CLAIM, "carol", None, None, {"text": "x"}, f"id{i}", i + 1, "2026-11-10T00:00:00+00:00")

    proj = Projector(host="alice")
    tracemalloc.start()
    for e in events():
        proj.apply(e)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert proj.last_seq == 100_000 and peak < 8 * 1024 * 1024, f"peak {peak / 1e6:.1f} MB: memory grew with the log"
    assert len(proj.state.prompts) <= 50  # the state is bounded by what the room contains, not by how long it has run
