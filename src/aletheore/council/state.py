"""Pure projection of a room's event log into current state.

The log is written by every participant, so the projection decides whose events count:
  host-only (the room host): room definition, areas, autonomy mode, task requests, verdicts, "applied" marks
  area owner (or host):      approving/rejecting a verdict for that area, acknowledging out-of-area files
  area owner:                reporting progress (started/finished/failed/blocked) on tasks of that area
The host is passed in by callers that can ask the server/store (`load_state`); without it the first
`room_created` event fixes it. A malformed or unauthorized event is skipped, never raised, so one bad
event can never make the room unreadable.

Event body conventions:
  task:      {"op": "request"|"assign"|"started"|"finished"|"failed"|"blocked", "task_id", "area"?, "branch"?, "files"?}
  decision:  {"verdict_id", "decision", "affected_area", "winning_branch"?}
  system:    {"subtype": "room_created", "host"} {"subtype": "area_defined", "name", "globs", "owner", "agent_id"}
             {"subtype": "verdict_approved"|"verdict_rejected"|"verdict_applied", "verdict_id"}
             {"subtype": "autonomy_set", "on"} {"subtype": "out_of_area_acknowledged", "task_id"}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from aletheore.council.protocol import Event, EventType


@dataclass
class Area:
    name: str
    globs: list[str]
    owner_participant: str
    agent_id: str


@dataclass
class TaskState:
    task_id: str
    area: str
    status: str = "assigned"  # assigned | running | done | failed | blocked
    branch: str | None = None
    blocked_files: list[str] = field(default_factory=list)
    acknowledged: bool = False


@dataclass
class Verdict:
    verdict_id: str
    thread_id: str
    decision: str
    affected_area: str
    winning_branch: str | None = None
    status: str = "pending"  # pending | approved | rejected | applied
    candidates: dict[str, str] = field(default_factory=dict)  # agent id -> branch, as recorded by the planner
    affected_by_winner: dict[str, str] = field(default_factory=dict)  # agent id -> area that changes if that agent wins


MAX_PLAN_TASKS = 8


@dataclass
class Plan:
    plan_id: str
    thread_id: str
    reply: str
    tasks: list[dict]
    requested_by: str
    status: str = "pending"  # pending | approved | rejected | dispatched | answered


@dataclass
class RoomState:
    host: str = ""
    areas: dict[str, Area] = field(default_factory=dict)
    tasks: dict[str, TaskState] = field(default_factory=dict)
    verdicts: dict[str, Verdict] = field(default_factory=dict)
    autonomy: bool = False
    plans: dict[str, Plan] = field(default_factory=dict)
    prompts: dict[str, str] = field(default_factory=dict)  # thread id -> participant who asked first
    finished: str = ""  # "", "done", "limit" or "stopped": how an autonomous room ended
    finish_note: str = ""
    goal: str = ""  # an autonomous room's goal text (from its first goal message), kept here so a reader of a long room need not find that event
    goal_thread: str = ""


_TASK_STATUS = {"started": "running", "finished": "done", "failed": "failed", "blocked": "blocked"}
_VERDICT_STATUS = {"verdict_approved": "approved", "verdict_rejected": "rejected", "verdict_applied": "applied"}


def _s(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("expected a non-empty string")
    return value


def _apply(state: RoomState, e: Event, host_fixed: bool) -> None:
    b = e.body
    sender = e.sender_participant
    is_host = bool(state.host) and sender == state.host
    if e.type is EventType.SYSTEM:
        sub = b.get("subtype")
        if sub == "room_created":
            if not host_fixed and not state.host and _s(b.get("host")) == sender:
                state.host = sender
        elif sub == "area_defined" and is_host:
            name, owner, agent = _s(b.get("name")), _s(b.get("owner")), _s(b.get("agent_id"))
            globs = b.get("globs")
            if not isinstance(globs, list) or not globs or not all(isinstance(g, str) and g for g in globs):
                raise ValueError("globs must be a list of strings")
            if any(a.agent_id == agent and a.owner_participant != owner and a.name != name for a in state.areas.values()):
                return  # one agent id belongs to one owner, or branches and worktrees would collide
            state.areas[name] = Area(name, list(globs), owner, agent)
        elif sub in _VERDICT_STATUS:
            v = state.verdicts.get(_s(b.get("verdict_id")))
            if v is None:
                return
            if sub == "verdict_applied":
                if is_host:
                    v.status = "applied"
                return
            area = state.areas.get(v.affected_area)
            allowed = is_host or (area is not None and sender == area.owner_participant)
            if allowed and v.status != "applied":
                v.status = _VERDICT_STATUS[sub]
        elif sub == "winner_chosen" and is_host:
            v = state.verdicts.get(_s(b.get("verdict_id")))
            winner = _s(b.get("winner"))
            if v is None or v.winning_branch or v.status in ("applied", "rejected") or winner not in v.candidates:
                return  # a human only breaks a tie: a planner's winner or an earlier choice stands
            v.winning_branch = v.candidates[winner]
            v.affected_area = v.affected_by_winner.get(winner, v.affected_area)
        elif sub in ("plan_approved", "plan_rejected", "plan_dispatched") and is_host:
            plan = state.plans.get(_s(b.get("plan_id")))
            if plan is None or plan.status in ("rejected", "answered"):
                return  # a rejection is final; plain answers have nothing to approve
            if sub == "plan_dispatched":
                if plan.status == "approved":
                    plan.status = "dispatched"
            elif plan.status == "pending":
                plan.status = "approved" if sub == "plan_approved" else "rejected"
        elif sub == "room_done" and is_host and not state.finished:
            outcome = b.get("outcome")
            if outcome not in ("done", "limit", "stopped"):
                raise ValueError("bad outcome")
            state.finished, state.finish_note = outcome, str(b.get("summary", ""))[:600]
        elif sub == "conductor_stopped" and is_host and not state.finished:
            state.finished, state.finish_note = "stopped", "stopped by the host"
        elif sub == "autonomy_set" and is_host:
            if not isinstance(b.get("on"), bool):
                raise ValueError("on must be a boolean")
            state.autonomy = b["on"]
        elif sub == "out_of_area_acknowledged":
            task = state.tasks.get(_s(b.get("task_id")))
            if task is None:
                return
            area = state.areas.get(task.area)
            if is_host or (area is not None and sender == area.owner_participant):
                task.acknowledged = True
    elif e.type is EventType.TASK:
        op, tid = b.get("op"), b.get("task_id")
        if op in ("assign", "request") and is_host:
            state.tasks[_s(tid)] = TaskState(tid, _s(b.get("area")))
        elif op in _TASK_STATUS:
            task = state.tasks.get(_s(tid))
            area = state.areas.get(task.area) if task else None
            if task is None or area is None or sender != area.owner_participant:
                return
            task.status = _TASK_STATUS[op]
            if isinstance(b.get("branch"), str) and b["branch"]:
                task.branch = b["branch"]
            if op == "blocked":
                files = b.get("files", [])
                if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
                    raise ValueError("files must be a list of strings")
                task.blocked_files = list(files)
    elif e.type is EventType.QUESTION and b.get("kind") in ("prompt", "debate", "task"):
        state.prompts.setdefault(e.thread_id, sender)
        if b.get("goal") is True and not state.goal:
            state.goal, state.goal_thread = str(b.get("text", ""))[:2000], e.thread_id
    elif e.type is EventType.PROPOSAL and is_host and isinstance(b.get("plan_id"), str):
        plan_id = _s(b.get("plan_id"))
        if plan_id in state.plans:
            return
        raw = b.get("tasks", [])
        if not isinstance(raw, list) or len(raw) > MAX_PLAN_TASKS:
            raise ValueError("bad task list")
        tasks = []
        for t in raw:
            if not isinstance(t, dict):
                raise ValueError("bad plan item")
            kind = t.get("kind", "task")
            if kind == "task":
                if _s(t.get("area")) not in state.areas:
                    raise ValueError("task names an unknown area")
                tasks.append({"kind": "task", "area": t["area"], "instruction": _s(t.get("instruction"))})
            elif kind == "debate":
                a, c = _s(t.get("a")), _s(t.get("b"))
                if a == c or a not in state.areas or c not in state.areas:
                    raise ValueError("a debate needs two different known areas")
                tasks.append({"kind": "debate", "a": a, "b": c, "subject": _s(t.get("subject"))[:300]})
            else:
                raise ValueError("unknown plan item kind")
        state.plans[plan_id] = Plan(plan_id, e.thread_id, str(b.get("reply", "")), tasks,
                                    state.prompts.get(e.thread_id, ""), "pending" if tasks else "answered")
    elif e.type is EventType.DECISION and is_host:
        vid = _s(b.get("verdict_id"))
        if vid in state.verdicts:
            return  # the first verdict with an id stands; it cannot be swapped later
        branch = b.get("winning_branch")
        def _strmap(value: object) -> dict[str, str]:
            if not isinstance(value, dict):
                return {}
            return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str) and k and v}

        state.verdicts[vid] = Verdict(
            vid, e.thread_id, str(b.get("decision", "")), _s(b.get("affected_area")),
            branch if isinstance(branch, str) and branch else None,
            candidates=_strmap(b.get("candidates")), affected_by_winner=_strmap(b.get("affected_by_winner")),
        )


def project(events: Iterable[Event], host: str | None = None) -> RoomState:
    state = RoomState(host=host or "")
    for e in events:
        try:
            _apply(state, e, host_fixed=host is not None)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return state


class Projector:
    """The same projection as `project`, one event at a time: feed it events as they arrive and it keeps only the small state, never the log.
    `Projector(host, state)` can also start from a snapshot's state."""

    def __init__(self, host: str | None = None, state: "RoomState | None" = None, last_seq: int = 0) -> None:
        self.state = state if state is not None else RoomState(host=host or "")
        self.host_fixed = host is not None
        self.last_seq = last_seq

    def apply(self, e: Event) -> None:
        if e.seq and e.seq <= self.last_seq:
            return  # already applied: replays and overlapping reads are harmless
        try:
            _apply(self.state, e, host_fixed=self.host_fixed)
        except (KeyError, TypeError, ValueError, AttributeError):
            pass  # a malformed or unauthorised event is skipped, exactly as in `project`
        if e.seq:
            self.last_seq = e.seq

    def apply_all(self, events: Iterable[Event]) -> "Projector":
        for e in events:
            self.apply(e)
        return self


def load_state(store, room_id: str) -> RoomState:
    """Read the whole log for a room (paged) and project it, with the host taken from the store (the server's
    record of who created the room), never from events."""
    from aletheore.council.snapshots import load_state_fast

    return load_state_fast(store, room_id)
