import json
import threading
import time

import pytest

from aletheore.council.daemon import WorkerDaemon
from aletheore.council.dispatcher import (
    ASK_LIMIT_PER_HOUR, Dispatcher, asks_in_window, send_ask, send_message, send_task,
)
from aletheore.council.protocol import EventDraft, EventType, is_host_only
from aletheore.council.runners import ScriptedRunner
from aletheore.council.state import Area, load_state
from aletheore.council.store import read_all
from tests.council.world_fixture import world  # noqa: F401


def events_in(w, thread):
    return [e for e in read_all(w["store"], w["alice"].room_id) if e.thread_id == thread]


def build(w, runners, planner_text="SUMMARY: they agree", **kw):
    kw.setdefault("remote_timeout_s", 0.3)  # an agent whose owner's worker is not running simply does not answer
    planner = ScriptedRunner(final_text=planner_text)
    d = Dispatcher(w["store"], w["alice"], "alice", planner, blobs=w["bundles"], agent_runner=lambda a: runners[a], **kw)
    return d, planner


def run(d):
    d.run_once()
    d.wait_idle()


def answers(w, thread):
    return [e for e in events_in(w, thread) if e.body.get("kind") == "answer"]


# ---- the plumbing -----------------------------------------------------------------------------------------------
def test_the_limit_is_25_an_hour_and_ask_events_are_host_issued():
    assert ASK_LIMIT_PER_HOUR == 25
    for sub in ("ask_started", "ask_done", "ask_limited"):
        assert is_host_only(EventType.SYSTEM, {"subtype": sub})


def test_send_ask_and_send_task_check_their_input(world):
    rid = world["alice"].room_id
    thread = send_ask(world["store"], rid, "bob", "all", "what do you think about UUIDs?")
    ev = events_in(world, thread)[0]
    assert ev.body["kind"] == "ask" and ev.body["to"] == "all" and ev.body["question"] == "what do you think about UUIDs?"
    assert ev.body["text"].startswith("@all ")
    for bad in (("", "x"), ("all", "   ")):
        with pytest.raises(ValueError):
            send_ask(world["store"], rid, "bob", *bad)
    t = send_task(world["store"], rid, "bob", "redis", "write the cache")
    assert events_in(world, t)[0].body["kind"] == "task"
    with pytest.raises(ValueError):
        send_task(world["store"], rid, "bob", "redis", "  ")


def test_asks_in_window_counts_one_askers_recent_asks(world):
    rid = world["alice"].room_id
    for i in range(3):
        send_ask(world["store"], rid, "bob", "all", f"q{i}")
    send_ask(world["store"], rid, "carol", "all", "other person")
    send_message(world["store"], rid, "bob", "not an ask")
    evs = read_all(world["store"], rid)
    assert asks_in_window(evs, "bob", time.time()) == 3 and asks_in_window(evs, "carol", time.time()) == 1
    assert asks_in_window(evs, "bob", time.time() + 7200) == 0  # an hour later they no longer count


# ---- one agent -------------------------------------------------------------------------------------------------
def test_ask_to_a_hosts_agent_gets_a_read_only_answer_that_knows_the_room(world):
    rid = world["alice"].room_id
    runner = ScriptedRunner(final_text="I would use UUIDs because ...")
    d, planner = build(world, {"codex": runner})
    thread = send_ask(world["store"], rid, "bob", "codex", "should ids be UUIDs?")
    run(d)
    got = answers(world, thread)
    assert len(got) == 1 and got[0].sender_participant == "alice" and got[0].sender_agent == "codex"
    assert "UUIDs because" in got[0].body["text"]
    assert runner.read_only_calls == [True]
    prompt = runner.calls[0]
    assert "should ids be UUIDs?" in prompt and "bob" in prompt and "schema" in prompt and "read-only" in prompt.lower()
    subs = [e.body.get("subtype") for e in events_in(world, thread) if e.type is EventType.SYSTEM]
    assert subs[0] == "ask_started" and subs[-1] == "ask_done"
    run(d)  # asking again changes nothing
    assert len(answers(world, thread)) == 1 and len(runner.calls) == 1


def test_ask_to_another_participants_agent_goes_through_their_worker(world):
    rid = world["alice"].room_id
    bob_runner = ScriptedRunner(final_text="bob's claude here: use UUIDs")
    bobd = WorkerDaemon(world["store"], world["bob"], "bob", {"claude": bob_runner}, bundles=world["bundles"], poll_interval_s=0.05)
    stop = threading.Event()
    t = threading.Thread(target=bobd.run_forever, args=(stop,), daemon=True)
    t.start()
    try:
        d, _ = build(world, {"codex": ScriptedRunner(final_text="x")})
        thread = send_ask(world["store"], rid, "alice", "claude", "your view on ids?")
        run(d)
    finally:
        stop.set()
        t.join(3)
    got = answers(world, thread)
    assert len(got) == 1 and got[0].sender_participant == "bob" and "bob's claude" in got[0].body["text"]
    assert bob_runner.read_only_calls == [True]


def test_asking_a_person_reaches_all_of_their_agents_and_unknown_targets_are_reported(world):
    rid = world["alice"].room_id
    runner = ScriptedRunner(final_text="alice's agent answers")
    d, _ = build(world, {"codex": runner})
    thread = send_ask(world["store"], rid, "bob", "alice", "hi alice's agents")
    run(d)
    assert len(answers(world, thread)) == 1
    t2 = send_ask(world["store"], rid, "bob", "nobody-here", "hello?")
    run(d)
    done = [e for e in events_in(world, t2) if e.body.get("subtype") == "ask_done"][0]
    assert done.body["ok"] is False and "no agent" in done.body["note"]


# ---- everyone ---------------------------------------------------------------------------------------------------
def test_ask_all_runs_two_rounds_shows_each_agent_the_others_answers_and_posts_a_summary(world):
    rid = world["alice"].room_id
    world["alice"].define_area(Area("docs", ["docs/**"], "alice", "alice-claude"))
    codex = ScriptedRunner(texts=["codex round one: ints are simpler", "codex round two: fair, uuids then"])
    claude = ScriptedRunner(texts=["claude round one: uuids avoid collisions", "AGREE: ok"])
    d, planner = build(world, {"codex": codex, "alice-claude": claude}, planner_text="Summary: use UUIDs.")
    thread = send_ask(world["store"], rid, "bob", "all", "ints or uuids?")
    run(d)
    assert len(codex.calls) == 2 and len(claude.calls) == 2
    assert "claude round one: uuids avoid collisions" in codex.calls[1]  # round two shows the others' answers...
    assert "<<untrusted" in codex.calls[1]  # ...as framed, untrusted data
    assert "codex round one: ints are simpler" in claude.calls[1]
    assert codex.read_only_calls == [True, True] and planner.read_only_calls == [True]
    summary = [e for e in events_in(world, thread) if e.body.get("kind") == "summary"]
    assert len(summary) == 1 and summary[0].type is EventType.RESULT and summary[0].sender_agent == "planner"
    assert "UUIDs" in summary[0].body["text"]
    # a single agent never gets a summary
    t2 = send_ask(world["store"], rid, "bob", "codex", "just you")
    run(d)
    assert not [e for e in events_in(world, t2) if e.body.get("kind") == "summary"]


def test_everyone_agreeing_in_round_one_ends_the_discussion_early(world):
    rid = world["alice"].room_id
    world["alice"].define_area(Area("docs", ["docs/**"], "alice", "alice-claude"))
    codex, claude = ScriptedRunner(texts=["AGREE: whatever you say"]), ScriptedRunner(texts=["AGREE: yes"])
    d, _ = build(world, {"codex": codex, "alice-claude": claude})
    send_ask(world["store"], rid, "bob", "all", "q?")
    run(d)
    assert len(codex.calls) == 1 and len(claude.calls) == 1


# ---- the rate limit ----------------------------------------------------------------------------------------------
def test_too_many_asks_from_one_person_are_refused_but_the_planners_and_hosts_work_is_not(world):
    rid = world["alice"].room_id
    runner = ScriptedRunner(final_text="ok")
    d, planner = build(world, {"codex": runner}, ask_limit=2, planner_text=json.dumps({"reply": "split", "tasks": [
        {"kind": "task", "area": "redis", "instruction": "write it"}]}))
    threads = [send_ask(world["store"], rid, "bob", "codex", f"q{i}") for i in range(3)]
    run(d)
    limited = [t for t in threads if any(e.body.get("subtype") == "ask_limited" for e in events_in(world, t))]
    assert limited == [threads[2]] and len(runner.calls) == 2  # the third in the hour is refused
    other = send_ask(world["store"], rid, "carol", "codex", "carol is not limited by bob's asks")
    run(d)
    assert len(answers(world, other)) == 1
    # work the planner or host assigns is never counted: a prompt still becomes a plan, and approved plans still dispatch tasks
    pt = send_message(world["store"], rid, "bob", "please build the cache")
    d.run_once()
    world["store"].append(EventDraft(rid, "ap", pt, EventType.SYSTEM, "alice", None, None, {"subtype": "plan_approved", "plan_id": f"plan-{pt}"}))
    d.run_once()
    d.wait_idle()
    reqs = [e for e in read_all(world["store"], rid) if e.type is EventType.TASK and e.body.get("op") == "request"]
    assert len(reqs) == 1 and reqs[0].body["area"] == "redis"
    world["alice"].request_task("redis", "host says finish this")  # and the host's own tasks have no limit at all
    assert load_state(world["store"], rid).tasks


def test_a_non_host_cannot_fake_the_hosts_progress_markers(world):
    rid = world["alice"].room_id
    runner = ScriptedRunner(final_text="ok")
    d, _ = build(world, {"codex": runner})
    thread = send_ask(world["store"], rid, "bob", "codex", "q")
    world["store"].append(EventDraft(rid, "fake", thread, EventType.SYSTEM, "bob", None, None, {"subtype": "ask_started"}))
    run(d)
    assert len(runner.calls) == 1  # bob's forged "started" did not stop the real answer


# ---- tasks from the box ------------------------------------------------------------------------------------------
def test_slash_task_makes_a_pending_one_task_plan_for_the_host_to_approve(world):
    rid = world["alice"].room_id
    d, planner = build(world, {"codex": ScriptedRunner(final_text="x")})
    thread = send_task(world["store"], rid, "bob", "redis", "write the cache")
    d.run_once()
    plan = load_state(world["store"], rid).plans[f"plan-{thread}"]
    assert planner.calls == [] and plan.status == "pending" and plan.requested_by == "bob"
    assert plan.tasks == [{"kind": "task", "area": "redis", "instruction": "write the cache"}]
    bad = send_task(world["store"], rid, "bob", "nowhere", "x")
    d.run_once()
    assert load_state(world["store"], rid).plans[f"plan-{bad}"].status == "answered"


# ---- a cut-short discussion --------------------------------------------------------------------------------------
def test_a_discussion_cut_short_by_a_restart_is_resumed_after_it_goes_quiet(world):
    rid = world["alice"].room_id
    thread = send_ask(world["store"], rid, "bob", "codex", "q")
    world["store"].append(EventDraft(rid, "st", thread, EventType.SYSTEM, "alice", None, None, {"subtype": "ask_started"}))
    runner = ScriptedRunner(final_text="late answer")
    fresh, _ = build(world, {"codex": runner})
    run(fresh)
    assert runner.calls == []  # too recent: another process may still be running it
    late, _ = build(world, {"codex": runner}, clock=lambda: time.time() + 600)
    run(late)
    assert len(answers(world, thread)) == 1


def test_an_agent_that_does_not_answer_is_noted_and_the_others_carry_on(world):
    rid = world["alice"].room_id
    world["alice"].define_area(Area("docs", ["docs/**"], "alice", "alice-claude"))
    codex, claude = ScriptedRunner(texts=["codex view", "codex addition"]), ScriptedRunner(texts=["claude view", "AGREE: ok"])
    d, _ = build(world, {"codex": codex, "alice-claude": claude})
    thread = send_ask(world["store"], rid, "alice", "all", "thoughts?")
    run(d)
    failed = [e for e in events_in(world, thread) if e.body.get("subtype") == "turn_failed"]
    assert failed and all(e.body["agent"] == "claude" for e in failed)  # bob's agent never answered
    assert len(answers(world, thread)) >= 3 and any(e.body.get("subtype") == "ask_done" and e.body["ok"] for e in events_in(world, thread))
