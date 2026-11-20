"""A task must not read "finished" when nothing happened. An agent that could not do the work says so (a BLOCKED: first line) and the task is blocked
with its reason; a task that changed no files says so; and nobody is told "publish failed" for a branch that had nothing to publish."""
import time

from aletheore.council.board import board_rows, render_board
from aletheore.council.cli import describe_event
from aletheore.council.protocol import EventType
from aletheore.council.runners import ScriptedRunner
from aletheore.council.state import load_state
from aletheore.council.store import read_all
from tests.council.world_fixture import daemon, world  # noqa: F401


def run(world, runner, instruction="design the table"):
    tid = world["alice"].request_task("schema", instruction)
    b, _ = daemon(world, "bob", runner=runner)
    b.run_once()
    rid = world["alice"].room_id
    return tid, load_state(world["store"], rid), read_all(world["store"], rid)


def test_an_agent_that_says_it_is_blocked_leaves_the_task_blocked_with_its_reason(world):
    tid, state, events = run(world, ScriptedRunner(final_text="BLOCKED: the workspace is read-only and no shell can start"))
    t = state.tasks[tid]
    assert t.status == "blocked" and "read-only" in t.note and t.blocked_files == []
    assert any(e.type is EventType.RESULT for e in events)  # what the agent said is still in the room


def test_the_marker_is_found_after_whitespace_and_in_any_case_but_not_in_the_middle_of_a_reply(world):
    tid, state, _ = run(world, ScriptedRunner(final_text="\n  blocked:   no network", writes={"schema/a.sql": "1\n"}))
    assert state.tasks[tid].status == "blocked" and state.tasks[tid].note == "no network"
    tid2, state2, _ = run(world, ScriptedRunner(final_text="I wrote it. A test says BLOCKED: only in a log line.", writes={"schema/b.sql": "1\n"}))
    assert state2.tasks[tid2].status == "done"


def test_the_agent_is_told_how_to_say_it_cannot_do_the_work(world):
    runner = ScriptedRunner(final_text="ok", writes={"schema/a.sql": "1\n"})
    run(world, runner)
    assert "BLOCKED:" in runner.calls[0]


def test_a_task_that_changed_no_files_is_done_but_says_so(world):
    tid, state, events = run(world, ScriptedRunner(final_text="nothing to do"))
    t = state.tasks[tid]
    assert t.status == "done" and t.no_changes is True and "no files changed" in t.note
    fin = next(e for e in events if e.type is EventType.TASK and e.body.get("op") == "finished")
    assert "no files changed" in describe_event(fin)[1]


def test_a_task_that_changed_files_does_not_claim_no_changes(world):
    tid, state, _ = run(world, ScriptedRunner(final_text="ok", writes={"schema/a.sql": "1\n"}))
    assert state.tasks[tid].no_changes is False and state.tasks[tid].note == ""


def test_the_board_shows_the_reason_and_the_empty_finish(world):
    tid, state, events = run(world, ScriptedRunner(final_text="BLOCKED: no shell"))
    out = render_board(board_rows(state, events, time.time()))
    assert "blocked" in out and "no shell" in out
    tid2, state2, events2 = run(world, ScriptedRunner(final_text="nothing to do"), "second job")
    assert "no files changed" in render_board(board_rows(state2, events2, time.time()))


def test_a_blocked_report_wakes_the_host_with_its_reason(world):
    tid, _, events = run(world, ScriptedRunner(final_text="BLOCKED: no shell"))
    ev = next(e for e in events if e.type is EventType.TASK and e.body.get("op") == "blocked")
    label, text = describe_event(ev)
    assert label == "blocked" and "no shell" in text and tid in text


def test_no_publish_failure_is_reported_for_a_task_with_nothing_to_publish(world):
    _, _, events = run(world, ScriptedRunner(final_text="nothing to do"))
    assert not [e for e in events if e.body.get("subtype") == "publish_failed"]
    assert not [e for e in events if e.body.get("subtype") == "branch_published"]
