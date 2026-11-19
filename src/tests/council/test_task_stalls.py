"""Work that goes quiet is visible. A task nobody starts, or one that stops reporting, used to look the same as one that is simply slow, and a
runner that crashed left its task 'running' for the claim's whole hour. Now: a running task sends progress, the host says when one is stuck
(once), and a crashed run is a failed task."""
import time

import pytest

from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.runners import ScriptedRunner
from aletheore.council.state import load_state
from aletheore.council.waiting import wait_for
from tests.council.world_fixture import daemon, world  # noqa: F401


def all_events(world):
    return world["store"].read_after(world["alice"].room_id, 0)


def stalls(world, reason=None):
    out = [e for e in all_events(world) if e.type is EventType.SYSTEM and e.body.get("subtype") == "task_stalled"]
    return [e for e in out if reason is None or e.body.get("reason") == reason]


def later(seconds):
    return lambda: time.time() + seconds


def started(world, task_id="t-1", area="schema"):
    return world["store"].append(EventDraft(world["alice"].room_id, f"{task_id}:started", task_id, EventType.TASK, "bob", "claude", None,
                                            {"op": "started", "task_id": task_id}))


# -- the host notices a task nobody started ---------------------------------------------------------------------------------------
def test_a_task_nobody_started_is_reported_by_the_host_once_it_has_waited_too_long(world):
    tid = world["alice"].request_task("schema", "design the table")
    d, _ = daemon(world, "alice", clock=later(700))
    d.run_once()
    got = stalls(world, "unclaimed")
    assert len(got) == 1 and got[0].body["task_id"] == tid and got[0].body["area"] == "schema" and got[0].sender_participant == "alice"
    assert "bob" in got[0].body["note"]                     # says whose machine should have picked it up


def test_a_fresh_task_is_not_reported(world):
    world["alice"].request_task("schema", "design the table")
    d, _ = daemon(world, "alice", clock=later(60))
    d.run_once()
    assert stalls(world) == []


def test_the_report_is_not_repeated_by_the_same_worker_or_a_restart(world):
    world["alice"].request_task("schema", "design the table")
    d1, _ = daemon(world, "alice", clock=later(700))
    d1.run_once()
    d1.run_once()
    d2, _ = daemon(world, "alice", clock=later(900))
    d2.run_once()
    assert len(stalls(world, "unclaimed")) == 1


def test_only_the_host_reports_stalls(world):
    world["alice"].request_task("schema", "design the table")
    d, _ = daemon(world, "bob", clock=later(700))
    d.run_once()
    assert stalls(world) == []


def test_a_finished_task_is_never_reported(world):
    world["alice"].request_task("schema", "design the table")
    b, _ = daemon(world, "bob")
    b.run_once()
    assert load_state(world["store"], world["alice"].room_id).tasks and all(t.status == "done" for t in load_state(world["store"], world["alice"].room_id).tasks.values())
    d, _ = daemon(world, "alice", clock=later(5000))
    d.run_once()
    assert stalls(world) == []


# -- a running task that stops reporting --------------------------------------------------------------------------------------------
def test_a_running_task_that_goes_silent_is_reported(world):
    world["alice"].request_task("schema", "design the table")
    started(world, world_task(world))
    d, _ = daemon(world, "alice", clock=later(1000), silent_after_s=360)
    d.run_once()
    assert len(stalls(world, "silent")) == 1 and stalls(world, "unclaimed") == []


def test_progress_keeps_a_running_task_from_being_reported(world):
    tid = world["alice"].request_task("schema", "design the table")
    started(world, tid)
    d, _ = daemon(world, "alice", clock=later(0), silent_after_s=360)
    world["store"].append(EventDraft(world["alice"].room_id, "progress:1", tid, EventType.SYSTEM, "bob", "claude", None,
                                     {"subtype": "task_progress", "task_id": tid}))
    d.run_once()
    assert stalls(world) == []


def world_task(world):
    return next(iter(load_state(world["store"], world["alice"].room_id).tasks))


# -- progress while a task runs ----------------------------------------------------------------------------------------------------
class Slow(ScriptedRunner):
    def run(self, *a, **k):
        time.sleep(0.5)
        return super().run(*a, **k)


def test_a_running_task_sends_progress_and_stops_when_it_ends(world):
    world["alice"].request_task("schema", "design the table")
    b, _ = daemon(world, "bob", runner=Slow(final_text="ok"), progress_every_s=0.1)
    b.run_once()
    progress = [e for e in all_events(world) if e.body.get("subtype") == "task_progress"]
    assert len(progress) >= 2 and all(e.sender_participant == "bob" and e.body["task_id"] for e in progress)
    n = len(progress)
    time.sleep(0.4)
    assert len([e for e in all_events(world) if e.body.get("subtype") == "task_progress"]) == n     # no beat after the task is over


def test_a_beat_that_is_still_being_posted_when_the_task_ends_is_not_left_to_land_afterwards(world):
    class Brief(ScriptedRunner):
        def run(self, *a, **k):
            time.sleep(0.25)
            return super().run(*a, **k)

    world["alice"].request_task("schema", "design the table")
    b, _ = daemon(world, "bob", runner=Brief(final_text="ok"), progress_every_s=0.1)
    store, real_append = b.store, b.store.append

    def slow_append(draft):
        if draft.body.get("subtype") == "task_progress":
            time.sleep(0.4)                                     # the store is slow: the beat is still in flight when the task ends
        return real_append(draft)

    store.append = slow_append
    try:
        b.run_once()
    finally:
        store.append = real_append
    n = len([e for e in all_events(world) if e.body.get("subtype") == "task_progress"])
    time.sleep(0.7)
    assert len([e for e in all_events(world) if e.body.get("subtype") == "task_progress"]) == n     # nothing arrives once the task has returned


def test_progress_can_be_turned_off(world):
    world["alice"].request_task("schema", "design the table")
    b, _ = daemon(world, "bob", runner=Slow(final_text="ok"), progress_every_s=0)
    b.run_once()
    assert [e for e in all_events(world) if e.body.get("subtype") == "task_progress"] == []


# -- a crashed run is a failed task ---------------------------------------------------------------------------------------------------
class Crash(ScriptedRunner):
    def run(self, *a, **k):
        raise RuntimeError("the agent binary vanished")


def test_a_runner_that_raises_fails_the_task_instead_of_leaving_it_running(world):
    tid = world["alice"].request_task("schema", "design the table")
    b, _ = daemon(world, "bob", runner=Crash())
    b.run_once()
    st = load_state(world["store"], world["alice"].room_id).tasks[tid]
    assert st.status == "failed"
    failed = [e for e in all_events(world) if e.type is EventType.TASK and e.body.get("op") == "failed"]
    assert "vanished" in failed[0].body.get("note", "")


def test_a_connection_error_while_posting_the_result_does_not_fail_the_task_for_good(world):
    tid = world["alice"].request_task("schema", "design the table")
    b, _ = daemon(world, "bob", runner=Slow(final_text="ok"))
    real_append = b.store.append

    def flaky(draft):
        if draft.type is EventType.RESULT:
            raise ConnectionError("the room server is unreachable")      # a blip right after the agent finished
        return real_append(draft)

    b.store.append = flaky
    try:
        with pytest.raises(ConnectionError):
            b.run_once()
    finally:
        b.store.append = real_append
    failed = [e for e in all_events(world) if e.type is EventType.TASK and e.body.get("op") == "failed"]
    assert failed == []                                                  # a network blip is not a failed task: the unfinished claim is picked up again
    assert load_state(world["store"], world["alice"].room_id).tasks[tid].status != "failed"


# -- the host's wait hears about it ----------------------------------------------------------------------------------------------------
def test_a_waiting_host_is_woken_by_a_stall(world):
    world["alice"].request_task("schema", "design the table")
    d, _ = daemon(world, "alice", clock=later(700))
    d.run_once()
    got = wait_for(world["store"], world["alice"].room_id, "alice", since=0, timeout_s=0.3, poll_s=0.02)
    assert [e.body.get("subtype") for e in got] == ["task_stalled"]
    bobs = wait_for(world["store"], world["alice"].room_id, "bob", since=0, timeout_s=0.3, poll_s=0.02)
    assert "task_stalled" not in [e.body.get("subtype") for e in bobs]             # guests are not told: only the host can act on it
