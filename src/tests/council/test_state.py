from aletheore.council.protocol import Event, EventType
from aletheore.council.state import project


def ev(seq, type_, body, sender="alice"):
    return Event(
        room_id="r1", client_event_id=f"c{seq}", thread_id="t", type=type_, sender_participant=sender,
        sender_agent=None, reply_to=None, body=body, event_id=f"e{seq}", seq=seq, created_at="2026-11-09T00:00:00Z",
    )


def base():
    return [
        ev(1, EventType.SYSTEM, {"subtype": "room_created", "host": "alice"}),
        ev(2, EventType.SYSTEM, {"subtype": "area_defined", "name": "redis", "globs": ["redis/**"], "owner": "alice", "agent_id": "codex"}),
        ev(3, EventType.SYSTEM, {"subtype": "area_defined", "name": "schema", "globs": ["schema/**"], "owner": "bob", "agent_id": "claude"}),
    ]


def test_areas_and_host_projected():
    s = project(base())
    assert s.host == "alice"
    assert s.areas["schema"].owner_participant == "bob"
    assert s.areas["redis"].globs == ["redis/**"]


def test_assign_then_finish_updates_status():
    events = base() + [
        ev(4, EventType.TASK, {"op": "assign", "task_id": "t1", "area": "redis"}),
        ev(5, EventType.TASK, {"op": "started", "task_id": "t1", "branch": "council/r1/codex"}),
    ]
    assert project(events).tasks["t1"].status == "running"
    events.append(ev(6, EventType.TASK, {"op": "finished", "task_id": "t1"}))
    s = project(events)
    assert s.tasks["t1"].status == "done"
    assert s.tasks["t1"].branch == "council/r1/codex"


def test_blocked_task_and_acknowledgement():
    events = base() + [
        ev(4, EventType.TASK, {"op": "assign", "task_id": "t1", "area": "redis"}),
        ev(5, EventType.TASK, {"op": "blocked", "task_id": "t1", "files": ["schema/x.sql"]}),
    ]
    t = project(events).tasks["t1"]
    assert t.status == "blocked" and t.blocked_files == ["schema/x.sql"] and not t.acknowledged
    events.append(ev(6, EventType.SYSTEM, {"subtype": "out_of_area_acknowledged", "task_id": "t1"}))
    assert project(events).tasks["t1"].acknowledged


def test_projection_is_pure():
    events = base() + [ev(4, EventType.TASK, {"op": "assign", "task_id": "t1", "area": "redis"})]
    assert project(events) == project(events)


def test_unknown_system_subtype_ignored():
    events = base() + [ev(4, EventType.SYSTEM, {"subtype": "who_knows"})]
    assert project(events).host == "alice"


def test_verdict_status_follows_approval_events():
    events = base() + [
        ev(4, EventType.DECISION, {"verdict_id": "v1", "decision": "use hash", "affected_area": "schema", "winning_branch": "b"}),
    ]
    assert project(events).verdicts["v1"].status == "pending"
    events.append(ev(5, EventType.SYSTEM, {"subtype": "verdict_approved", "verdict_id": "v1"}, sender="bob"))
    assert project(events).verdicts["v1"].status == "approved"
    events.append(ev(6, EventType.SYSTEM, {"subtype": "verdict_applied", "verdict_id": "v1"}))
    assert project(events).verdicts["v1"].status == "applied"


def test_autonomy_flag_toggles():
    events = base() + [ev(4, EventType.SYSTEM, {"subtype": "autonomy_set", "on": True})]
    assert project(events).autonomy is True
    events.append(ev(5, EventType.SYSTEM, {"subtype": "autonomy_set", "on": False}))
    assert project(events).autonomy is False
