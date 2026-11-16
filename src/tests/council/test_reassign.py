"""A task whose owner is gone does not stay stuck for good: the host can cancel it or hand it to another area. A cancelled task is never run, a late
result from the old owner cannot revive it, and a plan counts a reassigned task as unfinished until its replacement is finished."""
import pytest

from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.state import load_state, task_settled
from aletheore.council.store import read_all
from aletheore.council.waiting import wait_for
from tests.council.world_fixture import daemon, world  # noqa: F401


def state(world):
    return load_state(world["store"], world["alice"].room_id)


def test_the_host_can_cancel_a_task_and_the_owner_never_runs_it(world):
    tid = world["alice"].request_task("schema", "design the table")
    world["alice"].cancel_task(tid, "bob is away")
    assert state(world).tasks[tid].status == "cancelled"
    b, runner = daemon(world, "bob")
    b.run_once()
    assert runner.calls == []


def test_only_the_host_can_cancel(world):
    tid = world["alice"].request_task("schema", "x")
    world["store"].append(EventDraft(world["alice"].room_id, "c:x", tid, EventType.TASK, "bob", "claude", None,
                                     {"op": "cancelled", "task_id": tid, "area": "schema"}))
    assert state(world).tasks[tid].status == "assigned"


def test_a_late_result_from_the_old_owner_cannot_revive_a_cancelled_task(world):
    tid = world["alice"].request_task("schema", "x")
    world["alice"].cancel_task(tid)
    for op in ("started", "finished", "failed"):
        world["store"].append(EventDraft(world["alice"].room_id, f"late:{op}", tid, EventType.TASK, "bob", "claude", None, {"op": op, "task_id": tid}))
    assert state(world).tasks[tid].status == "cancelled"


def test_a_finished_task_cannot_be_cancelled_and_an_unknown_one_is_an_error(world):
    tid = world["alice"].request_task("schema", "x")
    b, _ = daemon(world, "bob")
    b.run_once()
    with pytest.raises(ValueError):
        world["alice"].cancel_task(tid)
    with pytest.raises(KeyError):
        world["alice"].cancel_task("nope")


def test_cancelling_twice_is_harmless(world):
    tid = world["alice"].request_task("schema", "x")
    world["alice"].cancel_task(tid)
    with pytest.raises(ValueError):
        world["alice"].cancel_task(tid)
    assert len([e for e in read_all(world["store"], world["alice"].room_id) if e.body.get("op") == "cancelled"]) == 1


# -- reassign ----------------------------------------------------------------------------------------------------------------------
def test_reassigning_cancels_the_old_task_and_gives_the_same_work_to_another_area(world):
    tid = world["alice"].request_task("schema", "design the table")
    new = world["alice"].reassign_task(tid, "redis")
    st = state(world)
    assert st.tasks[tid].status == "cancelled" and st.tasks[tid].replaced_by == new
    assert st.tasks[new].area == "redis" and st.tasks[new].status == "assigned"
    a, runner = daemon(world, "alice")
    a.run_once()
    assert len(runner.calls) == 1 and "design the table" in runner.calls[0]
    assert state(world).tasks[new].status == "done"


def test_the_replacement_keeps_the_attachments_and_gets_a_fresh_id_each_time(world):
    tid = world["alice"].request_task("schema", "x", attachments=[{"id": "a1"}])
    n1 = world["alice"].reassign_task(tid, "redis")
    req = next(e for e in read_all(world["store"], world["alice"].room_id) if e.body.get("task_id") == n1 and e.body.get("op") == "request")
    assert req.body["attachments"] == [{"id": "a1"}]
    n2 = world["alice"].reassign_task(n1, "schema")             # and it can be moved again
    assert len({tid, n1, n2}) == 3 and state(world).tasks[n2].area == "schema"


def test_reassign_refuses_a_finished_task_an_unknown_area_and_the_same_area(world):
    tid = world["alice"].request_task("schema", "x")
    with pytest.raises(KeyError):
        world["alice"].reassign_task(tid, "nowhere")
    with pytest.raises(ValueError):
        world["alice"].reassign_task(tid, "schema")
    assert state(world).tasks[tid].status == "assigned"         # nothing happened
    b, _ = daemon(world, "bob")
    b.run_once()
    with pytest.raises(ValueError):
        world["alice"].reassign_task(tid, "redis")


# -- a reassigned task is not finished until its replacement is --------------------------------------------------------------------------
def test_a_cancelled_task_is_settled_only_when_its_replacement_is(world):
    tid = world["alice"].request_task("schema", "x")
    new = world["alice"].reassign_task(tid, "redis")
    assert not task_settled(state(world), tid)
    a, _ = daemon(world, "alice")
    a.run_once()
    assert task_settled(state(world), tid) and task_settled(state(world), new)


def test_a_cancelled_task_with_no_replacement_is_settled(world):
    tid = world["alice"].request_task("schema", "x")
    world["alice"].cancel_task(tid)
    assert task_settled(state(world), tid)
    assert not task_settled(state(world), "unknown")


# -- who is told ---------------------------------------------------------------------------------------------------------------------------
def test_the_old_owner_hears_the_task_was_taken_away(world):
    tid = world["alice"].request_task("schema", "x")
    world["alice"].cancel_task(tid)
    got = wait_for(world["store"], world["alice"].room_id, "bob", since=0, timeout_s=0.3, poll_s=0.02)
    assert [e.body["op"] for e in got] == ["request", "cancelled"]
    assert [e for e in wait_for(world["store"], world["alice"].room_id, "carol", since=0, timeout_s=0.3, poll_s=0.02)] == []


def test_the_board_shows_cancelled_work_as_closed_and_never_stuck(world):
    from aletheore.council.board import board_rows
    import time
    tid = world["alice"].request_task("schema", "x")
    new = world["alice"].reassign_task(tid, "redis")
    rows = board_rows(state(world), read_all(world["store"], world["alice"].room_id), time.time() + 10 ** 6)
    by = {r.task_id: r for r in rows}
    assert by[tid].status == "cancelled" and by[tid].stuck == ""
    assert rows[0].task_id == new                                # open work first


def test_a_plan_is_not_complete_while_a_reassigned_task_is_still_unfinished(world):
    from types import SimpleNamespace
    from aletheore.council.conductor import Conductor
    plan = SimpleNamespace(plan_id="plan-x", status="dispatched", tasks=[{"kind": "task", "area": "schema", "instruction": "x"}])
    world["alice"].request_task("schema", "x", task_id="plan-x-1")
    new = world["alice"].reassign_task("plan-x-1", "redis")
    c = Conductor.__new__(Conductor)
    events = read_all(world["store"], world["alice"].room_id)
    assert c._complete(plan, state(world), events) is False
    a, _ = daemon(world, "alice")
    a.run_once()
    assert state(world).tasks[new].status == "done"
    assert c._complete(plan, state(world), read_all(world["store"], world["alice"].room_id)) is True


def test_the_cli_commands_exist():
    from typer.testing import CliRunner
    from aletheore.council.cli import app
    for cmd in ("cancel", "handover"):
        r = CliRunner().invoke(app, [cmd, "--help"])
        assert r.exit_code == 0 and "host only" in r.output
