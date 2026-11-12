from aletheore.council.protocol import Event, EventType, is_host_only
from aletheore.council.state import project


def ev(seq, etype, sender, body, agent=None, thread="room"):
    return Event("r1", f"c{seq}", thread, etype, sender, agent, None, body, f"e{seq}", seq, "2026-11-09T12:00:00Z")


def room():
    return [
        ev(1, EventType.SYSTEM, "alice", {"subtype": "room_created", "host": "alice"}),
        ev(2, EventType.SYSTEM, "alice", {"subtype": "area_defined", "name": "redis", "globs": ["redis/**"], "owner": "alice", "agent_id": "alice-codex"}),
        ev(3, EventType.SYSTEM, "alice", {"subtype": "area_defined", "name": "schema", "globs": ["schema/**"], "owner": "bob", "agent_id": "bob-claude"}),
        ev(4, EventType.QUESTION, "bob", {"text": "add user accounts", "kind": "prompt"}, thread="p-1"),
    ]


PLAN = {"plan_id": "plan-p-1", "reply": "Splitting it", "tasks": [
    {"area": "schema", "instruction": "users table"}, {"area": "redis", "instruction": "session cache"}]}


def test_host_plan_is_pending_and_remembers_who_asked():
    s = project(room() + [ev(5, EventType.PROPOSAL, "alice", PLAN, agent="planner", thread="p-1")], host="alice")
    p = s.plans["plan-p-1"]
    assert p.status == "pending" and p.requested_by == "bob" and [t["area"] for t in p.tasks] == ["schema", "redis"]


def test_plan_from_a_non_host_is_ignored():
    s = project(room() + [ev(5, EventType.PROPOSAL, "bob", PLAN, thread="p-1")], host="alice")
    assert s.plans == {}


def test_plan_without_tasks_is_just_an_answer():
    s = project(room() + [ev(5, EventType.PROPOSAL, "alice", {"plan_id": "plan-p-1", "reply": "hi", "tasks": []}, thread="p-1")], host="alice")
    assert s.plans["plan-p-1"].status == "answered"


def test_plan_with_unknown_area_or_too_many_tasks_is_dropped():
    bad = {"plan_id": "plan-p-1", "reply": "x", "tasks": [{"area": "nope", "instruction": "i"}]}
    assert project(room() + [ev(5, EventType.PROPOSAL, "alice", bad, thread="p-1")], host="alice").plans == {}
    many = {"plan_id": "plan-p-1", "reply": "x", "tasks": [{"area": "redis", "instruction": "i"}] * 9}
    assert project(room() + [ev(5, EventType.PROPOSAL, "alice", many, thread="p-1")], host="alice").plans == {}


def test_only_host_can_approve_reject_or_dispatch_and_first_plan_stands():
    base = room() + [ev(5, EventType.PROPOSAL, "alice", PLAN, thread="p-1")]
    forged = project(base + [ev(6, EventType.SYSTEM, "bob", {"subtype": "plan_approved", "plan_id": "plan-p-1"})], host="alice")
    assert forged.plans["plan-p-1"].status == "pending"
    ok = project(base + [ev(6, EventType.SYSTEM, "alice", {"subtype": "plan_approved", "plan_id": "plan-p-1"})], host="alice")
    assert ok.plans["plan-p-1"].status == "approved"
    done = project(base + [ev(6, EventType.SYSTEM, "alice", {"subtype": "plan_approved", "plan_id": "plan-p-1"}),
                           ev(7, EventType.SYSTEM, "alice", {"subtype": "plan_dispatched", "plan_id": "plan-p-1"})], host="alice")
    assert done.plans["plan-p-1"].status == "dispatched"
    swapped = project(base + [ev(6, EventType.PROPOSAL, "alice", {**PLAN, "tasks": [{"area": "redis", "instruction": "evil"}]}, thread="p-1")], host="alice")
    assert swapped.plans["plan-p-1"].tasks[0]["instruction"] == "users table"
    rejected = project(base + [ev(6, EventType.SYSTEM, "alice", {"subtype": "plan_rejected", "plan_id": "plan-p-1"}),
                               ev(7, EventType.SYSTEM, "alice", {"subtype": "plan_approved", "plan_id": "plan-p-1"})], host="alice")
    assert rejected.plans["plan-p-1"].status == "rejected"  # a rejection is final


def test_plan_decisions_are_host_only_events():
    for sub in ("plan_approved", "plan_rejected", "plan_dispatched"):
        assert is_host_only(EventType.SYSTEM, {"subtype": sub})


from aletheore.council.view_model import build_stream, needs_me
from datetime import datetime, timezone

NOW = datetime(2026, 11, 9, 12, 5, tzinfo=timezone.utc)


def test_stream_shows_prompts_and_plan_cards_and_host_is_asked_to_approve():
    events = room() + [ev(5, EventType.PROPOSAL, "alice", PLAN, agent="planner", thread="p-1")]
    s = project(events, host="alice")
    items = build_stream(events, s, NOW)
    ask = next(i for i in items if i.author == "bob")
    assert ask.kind == "message" and ask.label == "asks" and ask.text == "add user accounts"
    card = next(i for i in items if i.kind == "plan")
    assert card.plan_id == "plan-p-1" and "schema" in card.text and "users table" in card.text and "Splitting it" in card.text
    assert [(n.kind, n.verdict_id) for n in needs_me(s, "alice")] == [("plan", "plan-p-1")]
    assert needs_me(s, "bob") == []  # the person who asked cannot approve their own request unless they are the host


def test_decide_plan_is_host_only_and_only_for_pending_plans():
    import pytest
    from aletheore.council.approval import decide_plan
    from aletheore.council.sqlite_store import SqliteRoomStore
    import tempfile, pathlib
    store = SqliteRoomStore(pathlib.Path(tempfile.mkdtemp()) / "r.db")
    store.create_room("r1", "alice")
    from aletheore.council.protocol import EventDraft
    for e in room() + [ev(5, EventType.PROPOSAL, "alice", PLAN, agent="planner", thread="p-1")]:
        store.append(EventDraft("r1", e.client_event_id, e.thread_id, e.type, e.sender_participant, e.sender_agent, None, e.body))
    with pytest.raises(PermissionError):
        decide_plan(store, "r1", "plan-p-1", "bob", True)
    decide_plan(store, "r1", "plan-p-1", "alice", True)
    with pytest.raises(ValueError):
        decide_plan(store, "r1", "plan-p-1", "alice", False)  # already decided
    with pytest.raises(ValueError):
        decide_plan(store, "r1", "plan-nope", "alice", True)


def test_plans_can_hold_debate_items_and_reject_bad_ones():
    mixed = {"plan_id": "plan-p-1", "reply": "r", "tasks": [
        {"kind": "task", "area": "schema", "instruction": "users table"},
        {"kind": "debate", "a": "schema", "b": "redis", "subject": "int or uuid ids"}]}
    s = project(room() + [ev(5, EventType.PROPOSAL, "alice", mixed, thread="p-1")], host="alice")
    assert [t["kind"] for t in s.plans["plan-p-1"].tasks] == ["task", "debate"]
    for bad in ({"kind": "debate", "a": "schema", "b": "schema", "subject": "x"},
                {"kind": "debate", "a": "schema", "b": "nope", "subject": "x"},
                {"kind": "debate", "a": "schema", "b": "redis", "subject": ""},
                {"kind": "wat", "area": "schema", "instruction": "x"}):
        s = project(room() + [ev(5, EventType.PROPOSAL, "alice", {"plan_id": "plan-p-1", "reply": "r", "tasks": [bad]}, thread="p-1")], host="alice")
        assert s.plans == {}
    # old plans without a kind are still tasks
    assert project(room() + [ev(5, EventType.PROPOSAL, "alice", PLAN, thread="p-1")], host="alice").plans["plan-p-1"].tasks[0]["kind"] == "task"


def test_plan_card_and_approval_summary_describe_debates():
    mixed = {"plan_id": "plan-p-1", "reply": "r", "tasks": [{"kind": "debate", "a": "schema", "b": "redis", "subject": "int or uuid ids"}]}
    events = room() + [ev(5, EventType.PROPOSAL, "alice", mixed, agent="planner", thread="p-1")]
    s = project(events, host="alice")
    card = next(i for i in build_stream(events, s, NOW) if i.kind == "plan")
    assert "debate schema vs redis: int or uuid ids" in card.text
    assert "debate" in needs_me(s, "alice")[0].summary
