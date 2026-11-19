"""`council wait` also wakes a session for the results of what it set going: finished work, a plan to approve, a verdict, and
people waiting to join. Before this, only @mentions, task requests and debate turns did, so a waiting host never learned its work was done."""
import time

import pytest

from aletheore.council.protocol import EventDraft, EventType
from aletheore.council.waiting import Cursor, wait_for
from tests.council.world_fixture import uid, world  # noqa: F401


def put(world, who, etype, body, thread="th-1", agent=None):
    return world["store"].append(EventDraft(world["alice"].room_id, f"x:{uid()}", thread, etype, who, agent, None, body))


def wait(world, me, **kw):
    kw.setdefault("timeout_s", 0.3)
    kw.setdefault("poll_s", 0.02)
    kw.setdefault("since", 0)
    return wait_for(world["store"], world["alice"].room_id, me, **kw)


def ops(events):
    return [e.body.get("op") or e.body.get("subtype") for e in events]


# -- results of work the host asked for ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("op", ["finished", "failed", "blocked"])
def test_the_host_hears_when_its_task_ends(world, op):
    put(world, "bob", EventType.TASK, {"op": op, "task_id": "t1"}, thread="t1", agent="claude")
    assert ops(wait(world, "alice")) == [op]


def test_a_task_that_only_started_does_not_wake_anyone(world):
    put(world, "bob", EventType.TASK, {"op": "started", "task_id": "t1"}, thread="t1", agent="claude")
    put(world, "bob", EventType.TASK, {"op": "claimed", "task_id": "t1"}, thread="t1", agent="claude")
    assert wait(world, "alice") == []


def test_a_task_result_is_for_the_host_not_for_other_guests(world):
    put(world, "bob", EventType.TASK, {"op": "finished", "task_id": "t1"}, thread="t1", agent="claude")
    assert wait(world, "carol") == []


def test_a_result_my_own_agent_posted_does_wake_my_session(world):
    # the host's worker runs the host's own agent: that agent finishing a task is news for the person (or session) waiting as the host
    put(world, "alice", EventType.TASK, {"op": "finished", "task_id": "t1"}, thread="t1", agent="codex")
    assert ops(wait(world, "alice")) == ["finished"]


def test_a_result_event_with_no_agent_behind_it_is_me_talking_to_myself(world):
    put(world, "alice", EventType.TASK, {"op": "finished", "task_id": "t1"}, thread="t1")
    assert wait(world, "alice") == []


def test_my_own_workers_notices_reach_my_session(world):
    put(world, "alice", EventType.SYSTEM, {"subtype": "worker_error", "note": "boom"}, thread="room")
    put(world, "alice", EventType.SYSTEM, {"subtype": "wake_failed", "to": "alice", "note": "x"}, thread="room", agent="codex")
    put(world, "alice", EventType.SYSTEM, {"subtype": "wake_skipped", "to": "alice", "note": "x"}, thread="room", agent="codex")
    assert [e.body["subtype"] for e in wait(world, "alice")] == ["worker_error", "wake_failed", "wake_skipped"]


def test_my_workers_notice_about_someone_elses_mention_is_not_mine(world):
    put(world, "alice", EventType.SYSTEM, {"subtype": "wake_skipped", "to": "bob", "note": "x"}, thread="room", agent="codex")
    assert wait(world, "alice") == []


def test_include_all_still_leaves_out_my_own_events(world):
    put(world, "alice", EventType.TASK, {"op": "finished", "task_id": "t1"}, thread="t1", agent="codex")
    assert wait(world, "alice", include_all=True) == []


# -- plans waiting for approval -----------------------------------------------------------------------------------------------
PLAN = {"plan_id": "plan-p1", "reply": "two tasks", "tasks": [{"kind": "task", "area": "redis", "instruction": "x"}]}


def test_a_plan_waiting_for_approval_wakes_the_host_even_though_its_own_planner_wrote_it(world):
    put(world, "alice", EventType.PROPOSAL, PLAN, thread="p1", agent="planner")
    got = wait(world, "alice")
    assert [e.type for e in got] == [EventType.PROPOSAL]


def test_a_plan_with_nothing_to_approve_does_not_wake_the_host(world):
    put(world, "alice", EventType.PROPOSAL, {"plan_id": "plan-p1", "reply": "just an answer", "tasks": []}, thread="p1", agent="planner")
    assert wait(world, "alice") == []


def test_a_plan_does_not_wake_guests(world):
    put(world, "alice", EventType.PROPOSAL, PLAN, thread="p1", agent="planner")
    assert wait(world, "bob") == []


def test_a_plan_in_an_autonomous_room_needs_no_approval_so_it_wakes_nobody(world):
    put(world, "alice", EventType.SYSTEM, {"subtype": "autonomy_set", "on": True}, thread="room")
    put(world, "alice", EventType.PROPOSAL, PLAN, thread="p1", agent="planner")
    assert wait(world, "alice") == []


# -- verdicts -----------------------------------------------------------------------------------------------------------------
def test_a_verdict_reaches_everyone_but_whoever_wrote_it(world):
    put(world, "alice", EventType.DECISION, {"text": "use the schema branch"}, thread="d1")
    assert [e.type for e in wait(world, "bob")] == [EventType.DECISION]
    assert wait(world, "alice") == []


# -- people waiting to join (these are not events in the room log: the server holds them) ----------------------------------------
def joins(*items):
    """A stand-in for the server's list of pending join requests; each call returns whatever is waiting now."""
    state = list(items)
    return lambda: list(state)


REQ = {"request_id": "jr-1", "handle": "carol", "words": "red apple tree"}


def test_the_host_is_woken_for_a_person_waiting_to_join(world):
    got = wait(world, "alice", joins=joins(REQ))
    assert len(got) == 1
    assert got[0].body["subtype"] == "join_request_pending"
    assert got[0].body["request_id"] == "jr-1" and got[0].body["handle"] == "carol" and got[0].body["words"] == "red apple tree"


def test_a_guest_is_never_told_about_join_requests(world):
    assert wait(world, "bob", joins=joins(REQ)) == []


def test_each_join_request_is_reported_once_across_waits(world, tmp_path):
    c = tmp_path / "cursor.json"
    assert len(wait(world, "alice", joins=joins(REQ), cursor=Cursor(c))) == 1
    assert wait(world, "alice", joins=joins(REQ), cursor=Cursor(c)) == []                  # a new process, same file
    other = {"request_id": "jr-2", "handle": "dave", "words": "blue fish"}
    assert [e.body["request_id"] for e in wait(world, "alice", joins=joins(REQ, other), cursor=Cursor(c))] == ["jr-2"]


def test_peek_does_not_use_up_a_join_request(world, tmp_path):
    c = tmp_path / "cursor.json"
    assert len(wait(world, "alice", joins=joins(REQ), cursor=Cursor(c), peek=True)) == 1
    assert len(wait(world, "alice", joins=joins(REQ), cursor=Cursor(c))) == 1


def test_a_server_that_cannot_list_requests_does_not_stop_the_wait(world):
    def broken():
        raise ConnectionError("server away")

    put(world, "bob", EventType.TASK, {"op": "finished", "task_id": "t1"}, thread="t1", agent="claude")
    assert ops(wait(world, "alice", joins=broken)) == ["finished"]                           # the room still works
    assert wait(world, "alice", joins=broken, since=10 ** 9) == []


def test_a_join_request_arriving_while_waiting_wakes_the_wait(world):
    import threading
    pending: list[dict] = []
    out: list = []
    t = threading.Thread(target=lambda: out.extend(wait(world, "alice", joins=lambda: list(pending), timeout_s=5, since=None)))
    t.start()
    time.sleep(0.3)
    assert t.is_alive()
    pending.append(REQ)
    t.join(3)
    assert [e.body["request_id"] for e in out] == ["jr-1"]


def test_include_all_does_not_report_join_requests_twice(world):
    got = wait(world, "alice", joins=joins(REQ), include_all=True)
    assert [e.body["subtype"] for e in got] == ["join_request_pending"]


# -- the person who typed a prompt hears what became of it --------------------------------------------------------------------------
def prompt(world, who="bob", thread="p1"):
    return put(world, who, EventType.QUESTION, {"kind": "prompt", "text": "add a cache"}, thread=thread)


def test_the_author_of_a_prompt_hears_the_plans_answer_even_when_it_needs_no_approval(world):
    prompt(world)
    put(world, "alice", EventType.PROPOSAL, {"plan_id": "plan-p1", "reply": "no code needed", "tasks": []}, thread="p1", agent="planner")
    got = wait(world, "bob")
    assert [e.type for e in got] == [EventType.PROPOSAL]


def test_other_guests_do_not_hear_someone_elses_plan(world):
    prompt(world)
    put(world, "alice", EventType.PROPOSAL, PLAN, thread="p1", agent="planner")
    assert wait(world, "carol") == []


def test_the_author_hears_when_a_task_from_their_plan_ends(world):
    prompt(world)
    put(world, "carol", EventType.TASK, {"op": "finished", "task_id": "plan-p1-1"}, thread="plan-p1-1", agent="claude")
    assert [e.body.get("op") for e in wait(world, "bob") if e.type is EventType.TASK] == ["finished"]
    assert [e for e in wait(world, "dave") if e.type is EventType.TASK] == []


def test_the_author_of_another_prompt_is_not_told(world):
    prompt(world, "bob", "p1")
    prompt(world, "carol", "p2")
    put(world, "alice", EventType.TASK, {"op": "finished", "task_id": "plan-p2-1"}, thread="plan-p2-1", agent="codex")
    assert [e.body.get("op") for e in wait(world, "carol") if e.type is EventType.TASK] == ["finished"]
    assert [e for e in wait(world, "bob") if e.type is EventType.TASK] == []
