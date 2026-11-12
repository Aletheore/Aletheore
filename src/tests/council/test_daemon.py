import threading

import pytest

from aletheore.council.bundle_transport import TransportError, import_bundle
from aletheore.council.daemon import WorkerDaemon
from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.room import Room
from aletheore.council.runners import ScriptedRunner
from aletheore.council.state import load_state
from tests.council.world_fixture import daemon, git, world  # noqa: F401


def test_room_created_event_records_base_sha(world):
    created = world["store"].read_after(world["alice"].room_id, 0)[0]
    assert created.body["base_sha"] == git(world["a"], "rev-parse", "HEAD") == world["bob"].base_sha


def test_daemon_runs_only_tasks_for_its_participant(world):
    t_redis = world["alice"].request_task("redis", "build cache")
    t_schema = world["alice"].request_task("schema", "design table")
    d, runner = daemon(world, "alice")
    assert d.run_once() >= 1
    state = load_state(world["store"], world["alice"].room_id)
    assert state.tasks[t_redis].status == "done" and state.tasks[t_schema].status == "assigned"
    assert len(runner.calls) == 1 and "build cache" in runner.calls[0]


def test_second_daemon_for_same_participant_does_not_duplicate_work(world):
    world["alice"].request_task("redis", "build cache")
    d1, r1 = daemon(world, "alice")
    d2, r2 = daemon(world, "alice")
    ts = [threading.Thread(target=d.run_once) for d in (d1, d2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(r1.calls) + len(r2.calls) == 1


def test_daemon_uploads_bundle_and_posts_branch_published(world):
    t = world["alice"].request_task("schema", "design table")
    d, _ = daemon(world, "bob")
    d.run_once()
    room_id = world["alice"].room_id
    pub = [e for e in world["store"].read_after(room_id, 0) if e.body.get("subtype") == "branch_published"]
    assert len(pub) == 1
    body = pub[0].body
    assert body["participant"] == "bob" and body["task_id"] == t and body["branch"] == f"council/{room_id}/claude"
    assert pub[0].sender_participant == "bob"
    dest = world["tmp"] / "got.bundle"
    world["bundles"].get_bundle(room_id, body["sha256"], dest)
    ref = import_bundle(world["a"], "bob", body["branch"], dest, body["sha256"])
    assert "schema/n.sql" in git(world["a"], "ls-tree", "-r", "--name-only", ref)


def test_daemon_ignores_unknown_ops_and_foreign_senders(world):
    room_id = world["alice"].room_id
    # bob (not the host) tries to order alice's daemon to do work
    world["store"].append(EventDraft(room_id, "evil1", "task-x", EventType.TASK, "bob", None, None,
                                     {"op": "request", "task_id": "task-x", "area": "redis", "instruction": "rm -rf"}))
    # the host sends an op the daemon does not know
    world["store"].append(EventDraft(room_id, "odd1", "task-y", EventType.TASK, "alice", None, None,
                                     {"op": "explode", "task_id": "task-y", "area": "redis", "instruction": "x"}))
    d, runner = daemon(world, "alice")
    assert d.run_once() == 0 and runner.calls == []


def test_crash_after_claim_is_reclaimed_after_timeout(world):
    task_id = world["alice"].request_task("redis", "build cache")
    room_id = world["alice"].room_id
    world["store"].append(EventDraft(room_id, f"claim:{task_id}", task_id, EventType.TASK, "alice", "codex", None,
                                     {"op": "claimed", "task_id": task_id, "instance": "dead-instance", "at": 0.0}))
    now = [100.0]
    d, runner = daemon(world, "alice", claim_timeout_s=3600, clock=lambda: now[0])
    assert d.run_once() == 0 and runner.calls == []  # a live-looking claim is respected
    now[0] = 4000.0
    d2, runner2 = daemon(world, "alice", claim_timeout_s=3600, clock=lambda: now[0])
    assert d2.run_once() >= 1 and len(runner2.calls) == 1
    assert load_state(world["store"], room_id).tasks[task_id].status == "done"


def test_resumes_from_last_seq_after_restart(world):
    world["alice"].request_task("redis", "build cache")
    d, runner = daemon(world, "alice")
    d.run_once()
    d_again, runner_again = daemon(world, "alice")
    d_again.run_once()  # re-reads the whole log, but the task is already done
    assert runner_again.calls == []
    d_resume, runner_resume = daemon(world, "alice", after_seq=d.after_seq)
    assert d_resume.run_once() == 0 and runner_resume.calls == []


def test_open_remote_refuses_when_base_commit_missing(world):
    stranger = world["tmp"] / "stranger"
    stranger.mkdir()
    git(stranger, "init", "-q", "-b", "main")
    (stranger / "x").write_text("x")
    git(stranger, "add", "-A")
    git(stranger, "commit", "-q", "-m", "unrelated")
    room_id = world["alice"].room_id
    with pytest.raises(TransportError):
        Room.open_remote(world["store"], room_id, stranger, world["tmp"] / "state_x")
    assert git(stranger, "branch", "--list", f"council/{room_id}/*") == ""
    assert not (world["tmp"] / "state_x" / "worktrees").exists()


def test_rerun_after_new_commits_publishes_a_fresh_bundle_event(world):
    t = world["alice"].request_task("schema", "design table")
    d, _ = daemon(world, "bob")
    d.run_once()
    room_id = world["alice"].room_id
    wt = d.room.state_dir / "worktrees" / room_id / "claude"
    (wt / "schema" / "more.sql").write_text("select 1;\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "second run")
    d._publish(t, "claude")
    pubs = [e for e in world["store"].read_after(room_id, 0) if e.body.get("subtype") == "branch_published"]
    assert len(pubs) == 2 and pubs[0].body["sha256"] != pubs[1].body["sha256"]


# ---- parallel mode: a long task must not stop the worker answering other things ------------------------------
import threading
import time

from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.runners import RunResult, ScriptedRunner
from aletheore.council.state import Area


class GatedRunner:
    """A runner that blocks until released, so tests can hold a task 'in progress'."""
    name = "gated"

    def __init__(self, final_text="gated done"):
        self.started, self.release, self.final_text, self.calls = threading.Event(), threading.Event(), final_text, []

    def run(self, instruction, worktree, mcp_config, identity_env, timeout_s, images=(), read_only=False):
        self.calls.append(instruction)
        self.started.set()
        self.release.wait(10)
        (worktree / "schema").mkdir(exist_ok=True)
        (worktree / "schema" / "g.sql").write_text("1\n")
        return RunResult(True, self.final_text, 0, [])


def turn(world, agent, turn_id, thread="dbg"):
    rid = world["alice"].room_id
    world["store"].append(EventDraft(rid, f"turn-req:{turn_id}", thread, EventType.TASK, "alice", None, None,
                                     {"op": "debate_turn", "turn_id": turn_id, "participant": "bob", "agent": agent, "round": 1, "instruction": "argue"}))


def answered(world, turn_id):
    return any(e.client_event_id == f"turn:{turn_id}" for e in world["store"].read_after(world["alice"].room_id, 0, limit=500))


def wait_for(cond, secs=8):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_parallel_worker_answers_another_agents_turn_while_a_task_runs(world):
    world["alice"].define_area(Area("docs", ["docs/**"], "bob", "bob-codex"))
    gated, other = GatedRunner(), ScriptedRunner(final_text="my position")
    d = WorkerDaemon(world["store"], world["bob"], "bob", {"claude": gated, "bob-codex": other}, bundles=world["bundles"], parallel=True)
    world["alice"].request_task("schema", "slow job")
    d.run_once()
    assert gated.started.wait(5)
    turn(world, "bob-codex", "T1")
    d.run_once()  # returns at once even though the schema task is still running
    assert wait_for(lambda: answered(world, "T1"))
    gated.release.set()
    d.wait_idle()
    states = load_state(world["store"], world["alice"].room_id).tasks
    assert next(iter(states.values())).status == "done"


def test_same_agent_turn_waits_for_its_own_task_then_answers(world):
    gated = GatedRunner(final_text="position")
    d = WorkerDaemon(world["store"], world["bob"], "bob", {"claude": gated}, bundles=world["bundles"], parallel=True)
    world["alice"].request_task("schema", "slow job")
    d.run_once()
    assert gated.started.wait(5)
    turn(world, "claude", "T2")
    d.run_once()
    time.sleep(0.4)
    assert not answered(world, "T2")  # same worktree: it must not run while the task does
    gated.release.set()
    assert wait_for(lambda: answered(world, "T2"))
    d.wait_idle()


def test_parallel_mode_does_not_run_the_same_turn_twice(world):
    runner = ScriptedRunner(final_text="position")
    d = WorkerDaemon(world["store"], world["bob"], "bob", {"claude": runner}, bundles=world["bundles"], parallel=True)
    turn(world, "claude", "T3")
    for _ in range(4):
        d.run_once()
    d.wait_idle()
    assert len(runner.calls) == 1


def test_parallel_task_is_claimed_once_even_with_two_daemons(world):
    r1, r2 = ScriptedRunner(final_text="ok", writes={"schema/a.sql": "1\n"}), ScriptedRunner(final_text="ok", writes={"schema/a.sql": "1\n"})
    d1 = WorkerDaemon(world["store"], world["bob"], "bob", {"claude": r1}, bundles=world["bundles"], parallel=True)
    d2 = WorkerDaemon(world["store"], world["bob"], "bob", {"claude": r2}, bundles=world["bundles"], parallel=True)
    world["alice"].request_task("schema", "once")
    for d in (d1, d2, d1, d2):
        d.run_once()
    d1.wait_idle()
    d2.wait_idle()
    assert len(r1.calls) + len(r2.calls) == 1


def test_close_waits_for_running_work_and_is_safe_to_repeat(world):
    gated = GatedRunner()
    d = WorkerDaemon(world["store"], world["bob"], "bob", {"claude": gated}, bundles=world["bundles"], parallel=True)
    world["alice"].request_task("schema", "slow job")
    d.run_once()
    assert gated.started.wait(5)
    threading.Timer(0.3, gated.release.set).start()
    d.close()  # returns only after the running task finished
    d.close()
    assert next(iter(load_state(world["store"], world["alice"].room_id).tasks.values())).status == "done"
    WorkerDaemon(world["store"], world["bob"], "bob", {}, bundles=world["bundles"]).close()  # inline mode: nothing to do


def test_a_turn_requested_as_an_answer_is_posted_labelled_as_one(world):
    rid = world["alice"].room_id
    d, runner = daemon(world, "bob", ScriptedRunner(final_text="my view"))
    world["store"].append(EventDraft(rid, "turn-req:Q1", "q-1", EventType.TASK, "alice", None, None,
                                     {"op": "debate_turn", "turn_id": "Q1", "participant": "bob", "agent": "claude", "round": 1,
                                      "instruction": "what do you think?", "post_kind": "answer"}))
    d.run_once()
    got = [e for e in world["store"].read_after(rid, 0, limit=500) if e.client_event_id == "turn:Q1"]
    assert len(got) == 1 and got[0].body["kind"] == "answer" and got[0].body["text"] == "my view"
    assert runner.read_only_calls == [True]
