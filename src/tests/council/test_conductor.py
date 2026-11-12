import json

import pytest

from aletheore.council.approval import set_autonomy
from aletheore.council.conductor import Conductor, stop_room
from aletheore.council.daemon import WorkerDaemon
from aletheore.council.dispatcher import Dispatcher, send_message
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.runners import ScriptedRunner
from aletheore.council.state import Area, load_state
from aletheore.council.store import read_all
from tests.council.world_fixture import world  # noqa: F401


def task(area, text):
    return {"kind": "task", "area": area, "instruction": text}


def plan_json(reply, *items):
    return json.dumps({"reply": reply, "tasks": list(items)})


def autonomous(w, goal="build the cache", **kw):
    """Make the world's room an autonomous one with a goal, a host planner, and a host worker for redis."""
    rid = w["alice"].room_id
    with w["store"]._connect() as conn:
        conn.execute("UPDATE rooms SET mode='autonomous' WHERE room_id=?", (rid,))
    set_autonomy(w["store"], rid, "alice", True)
    send_message(w["store"], rid, "alice", goal, goal=True)
    return rid


def build(w, planner_texts, max_rounds=4, max_steps=16, texts_for_agent=None):
    planner = ScriptedRunner(texts=planner_texts)
    d = Dispatcher(w["store"], w["alice"], "alice", planner, blobs=w["bundles"], agent_runner=lambda a: ScriptedRunner(final_text="ok"))
    c = Conductor(d, max_rounds=max_rounds, max_steps=max_steps)
    worker = WorkerDaemon(w["store"], w["alice"], "alice", {"codex": ScriptedRunner(final_text="built it", writes={"redis/c.py": "1\n"})},
                          bundles=w["bundles"])
    return c, worker, planner


def drive(c, worker, rounds=6):
    for _ in range(rounds):
        c.run_once()
        worker.run_once()
        c.dispatcher.wait_idle()


def state(w):
    return load_state(w["store"], w["alice"].room_id)


def test_goal_is_planned_run_reviewed_and_declared_done(world):
    rid = autonomous(world)
    c, worker, planner = build(world, [plan_json("one task", task("redis", "write the cache")),
                                       json.dumps({"done": True, "reply": "cache is written"})])
    drive(c, worker)
    s = state(world)
    assert s.finished == "done" and "cache is written" in s.finish_note
    assert [p.status for p in s.plans.values()] == ["dispatched"]
    assert len(planner.calls) == 2 and "built it" in planner.calls[1]  # the review saw the task's result
    drive(c, worker, 3)
    assert len(planner.calls) == 2  # finished means finished


def test_review_can_ask_for_another_round_until_done(world):
    autonomous(world)
    c, worker, planner = build(world, [plan_json("r1", task("redis", "first")), plan_json("r2", task("redis", "second")),
                                       json.dumps({"done": True, "reply": "ok"})])
    drive(c, worker, 10)
    s = state(world)
    assert list(s.plans) == [f"plan-{next(iter(s.prompts))}", f"plan-{next(iter(s.prompts))}-r2"] and s.finished == "done"


def test_round_limit_stops_the_room_without_asking_the_planner_again(world):
    autonomous(world)
    c, worker, planner = build(world, [plan_json("r1", task("redis", "a")), plan_json("r2", task("redis", "b")),
                                       plan_json("r3", task("redis", "c"))], max_rounds=2)
    drive(c, worker, 12)
    s = state(world)
    assert s.finished == "limit" and len(s.plans) == 2 and len(planner.calls) == 2


def test_step_limit_counts_every_task_and_debate(world):
    autonomous(world)
    c, worker, planner = build(world, [plan_json("big", task("redis", "a"), task("redis", "b"), task("redis", "c")),
                                       plan_json("never", task("redis", "d"))], max_steps=3)
    drive(c, worker, 12)
    assert state(world).finished == "limit" and len(planner.calls) == 1


def test_stop_is_host_only_and_halts_the_conductor(world):
    rid = autonomous(world)
    c, worker, planner = build(world, [plan_json("r1", task("redis", "a")), plan_json("r2", task("redis", "b"))])
    c.run_once()
    with pytest.raises(PermissionError):
        stop_room(world["store"], rid, "bob")
    stop_room(world["store"], rid, "alice")
    drive(c, worker, 6)
    s = state(world)
    assert s.finished == "stopped" and s.autonomy is False and len(planner.calls) == 1


def test_a_forged_finish_from_a_non_host_is_ignored(world):
    rid = autonomous(world)
    world["store"].append(EventDraft(rid, "f", "room", EventType.SYSTEM, "bob", None, None, {"subtype": "room_done", "outcome": "done", "summary": "x"}))
    assert state(world).finished == ""


def test_interactive_rooms_are_left_alone(world):
    rid = world["alice"].room_id
    send_message(world["store"], rid, "alice", "goal-like prompt", goal=True)
    planner = ScriptedRunner(texts=[plan_json("p", task("redis", "x")), json.dumps({"done": True, "reply": "r"})])
    c = Conductor(Dispatcher(world["store"], world["alice"], "alice", planner, blobs=world["bundles"]))
    for _ in range(3):
        c.run_once()
    assert state(world).finished == "" and len(planner.calls) == 1  # planned once (a normal prompt), never reviewed


# ---- honesty: what actually landed -----------------------------------------------------------------------------
from aletheore.council.worktrees import integration_branch
from tests.council.world_fixture import git


def test_finished_branches_are_merged_to_the_integration_branch_and_the_planner_is_told(world):
    rid = autonomous(world)
    c, worker, planner = build(world, [plan_json("one", task("redis", "write the cache")), json.dumps({"done": True, "reply": "cache written"})])
    drive(c, worker)
    assert "redis/c.py" in git(world["a"], "ls-tree", "-r", "--name-only", integration_branch(rid)).split()
    assert "redis/c.py" in planner.calls[1] and "merged" in planner.calls[1].lower()  # the review was told what landed
    s = state(world)
    assert s.finished == "done" and "redis/c.py" in s.finish_note
    notes = [e.body for e in read_all(world["store"], rid) if e.body.get("subtype") == "branch_integrated"]
    assert notes and notes[0]["area"] == "redis" and notes[0]["ok"] is True


def test_done_is_not_accepted_while_a_task_is_blocked_and_nothing_merged(world):
    rid = autonomous(world)
    planner = ScriptedRunner(texts=[plan_json("one", task("redis", "write")), json.dumps({"done": True, "reply": "all good"})])
    d = Dispatcher(world["store"], world["alice"], "alice", planner, blobs=world["bundles"], agent_runner=lambda a: ScriptedRunner(final_text="ok"))
    c = Conductor(d)
    sneaky = WorkerDaemon(world["store"], world["alice"], "alice",
                          {"codex": ScriptedRunner(final_text="done", writes={"redis/ok.py": "1\n", "schema/oops.sql": "2\n"})}, bundles=world["bundles"])
    drive(c, sneaky)
    s = state(world)
    assert s.finished == "limit" and "blocked" in s.finish_note and "schema/oops.sql" in s.finish_note
    assert "redis/ok.py" not in git(world["a"], "ls-tree", "-r", "--name-only", integration_branch(rid)).split()
    assert "blocked" in planner.calls[1] and "schema/oops.sql" in planner.calls[1]  # the planner saw the problem


# ---- rework is part of the round ---------------------------------------------------------------------------------
def test_a_debate_round_waits_for_the_losers_rework_and_merges_it(world):
    rid = autonomous(world)
    world["alice"].define_area(Area("docs", ["docs/**"], "alice", "alice-claude"))
    plan = plan_json("build then settle", task("redis", "write the cache"), {"kind": "debate", "a": "redis", "b": "docs", "subject": "naming"})
    planner = ScriptedRunner(texts=[plan, "WINNER: codex\nredis naming wins", json.dumps({"done": True, "reply": "settled"})])
    d = Dispatcher(world["store"], world["alice"], "alice", planner, blobs=world["bundles"],
                   agent_runner=lambda a: ScriptedRunner(texts=["my view", "AGREE: fine"]))
    c = Conductor(d)
    worker = WorkerDaemon(world["store"], world["alice"], "alice",
                          {"codex": ScriptedRunner(final_text="cache", writes={"redis/c.py": "1\n"}),
                           "alice-claude": ScriptedRunner(final_text="docs aligned", writes={"docs/d.md": "aligned\n"})},
                          bundles=world["bundles"])
    drive(c, worker, 14)
    s = state(world)
    vid = next(iter(s.verdicts))
    assert s.verdicts[vid].status == "applied" and s.tasks[f"rework-{vid}"].status == "done"
    landed = git(world["a"], "ls-tree", "-r", "--name-only", integration_branch(rid)).split()
    assert "redis/c.py" in landed and "docs/d.md" in landed  # the loser's reworked files were merged too
    assert s.finished == "done" and "docs/d.md" in planner.calls[2]  # the review knew about them


def settle_world(world, planner_extra=None):
    rid = autonomous(world)
    world["alice"].define_area(Area("docs", ["docs/**"], "alice", "alice-claude"))
    plan = plan_json("p", {"kind": "debate", "a": "redis", "b": "docs", "subject": "naming"})
    planner = ScriptedRunner(texts=[plan, "WINNER: codex\nredis wins", json.dumps({"done": True, "reply": "settled"})])
    d = Dispatcher(world["store"], world["alice"], "alice", planner, blobs=world["bundles"],
                   agent_runner=lambda a: ScriptedRunner(texts=["view", "AGREE: ok"]))
    return rid, planner, Conductor(d)


def test_a_failed_rework_is_reported_as_a_problem_and_blocks_a_false_done(world):
    rid, planner, c = settle_world(world)
    failing = WorkerDaemon(world["store"], world["alice"], "alice",
                           {"codex": ScriptedRunner(final_text="ok"), "alice-claude": ScriptedRunner(final_text="x", ok=False)},
                           bundles=world["bundles"])
    drive(c, failing, 10)
    s = state(world)
    assert s.finished == "limit" and "rework" in s.finish_note.lower() and "failed" in s.finish_note.lower()


def test_the_round_stays_open_while_the_rework_is_still_running(world):
    import threading
    rid, planner, c = settle_world(world)
    gate, started = threading.Event(), threading.Event()

    class Slow:
        name = "slow"

        def run(self, instruction, worktree, mcp_config, identity_env, timeout_s, images=(), read_only=False):
            started.set()
            gate.wait(10)
            (worktree / "docs").mkdir(exist_ok=True)
            (worktree / "docs" / "r.md").write_text("1\n")
            from aletheore.council.runners import RunResult
            return RunResult(True, "reworked", 0, [])

    worker = WorkerDaemon(world["store"], world["alice"], "alice", {"codex": ScriptedRunner(final_text="ok"), "alice-claude": Slow()},
                          bundles=world["bundles"], parallel=True)
    for _ in range(10):
        c.run_once()
        worker.run_once()
        c.dispatcher.wait_idle()
        if started.is_set():
            break
    assert started.wait(5)
    c.run_once()
    assert state(world).finished == "" and len(planner.calls) == 2  # no review while the rework is in flight
    gate.set()
    worker.wait_idle()
    drive(c, worker, 4)
    assert state(world).finished == "done" and "docs/r.md" in git(world["a"], "ls-tree", "-r", "--name-only", integration_branch(rid))
