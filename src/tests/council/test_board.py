"""`council board`: every task in the room on one screen, with who owns it, where it stands, how long since it moved, and whether it is stuck."""
import time

from aletheore.council.board import board_rows, render_board
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.state import load_state
from aletheore.council.store import read_all
from tests.council.world_fixture import daemon, world  # noqa: F401


def rows(world, now=None):
    rid = world["alice"].room_id
    return board_rows(load_state(world["store"], rid), read_all(world["store"], rid), now if now is not None else time.time())


def test_a_new_task_shows_owner_agent_status_and_instruction(world):
    tid = world["alice"].request_task("schema", "design the table")
    (r,) = rows(world)
    assert (r.task_id, r.area, r.owner, r.agent, r.status) == (tid, "schema", "bob", "claude", "waiting")
    assert r.instruction == "design the table" and r.stuck == ""


def test_a_finished_task_shows_done_and_its_branch(world):
    world["alice"].request_task("schema", "design the table")
    b, _ = daemon(world, "bob")
    b.run_once()
    (r,) = rows(world)
    assert r.status == "done" and r.branch


def test_a_task_nobody_started_is_marked_stuck_after_a_while(world):
    world["alice"].request_task("schema", "design the table")
    assert rows(world)[0].stuck == ""
    assert rows(world, time.time() + 1200)[0].stuck == "unclaimed"


def test_a_running_task_that_stopped_reporting_is_marked_stuck(world):
    tid = world["alice"].request_task("schema", "x")
    world["store"].append(EventDraft(world["alice"].room_id, f"{tid}:started", tid, EventType.TASK, "bob", "claude", None, {"op": "started", "task_id": tid}))
    assert rows(world)[0].status == "running" and rows(world)[0].stuck == ""
    assert rows(world, time.time() + 1000)[0].stuck == "silent"


def test_finished_tasks_are_never_stuck(world):
    world["alice"].request_task("schema", "x")
    b, _ = daemon(world, "bob")
    b.run_once()
    assert rows(world, time.time() + 10 ** 6)[0].stuck == ""


def test_the_last_activity_age_follows_the_newest_event_for_the_task(world):
    tid = world["alice"].request_task("schema", "x")
    world["store"].append(EventDraft(world["alice"].room_id, "pg:1", tid, EventType.SYSTEM, "bob", "claude", None, {"subtype": "task_progress", "task_id": tid}))
    r = rows(world, time.time() + 100)[0]
    assert 90 <= r.idle_s <= 110


def test_open_work_sorts_before_finished_work_and_an_empty_room_is_fine(world):
    assert rows(world) == []
    world["alice"].request_task("schema", "first")
    b, _ = daemon(world, "bob")
    b.run_once()
    world["alice"].request_task("redis", "second")
    assert [r.instruction for r in rows(world)] == ["second", "first"]


def test_render_is_plain_text_one_line_per_task_and_clips_long_instructions(world):
    world["alice"].request_task("schema", "x" * 500)
    out = render_board(rows(world))
    assert len(out.splitlines()) == 2 and "schema" in out and "bob/claude" in out and "waiting" in out
    assert len(out.splitlines()[1]) < 200
    assert render_board([]) == "No tasks yet."
