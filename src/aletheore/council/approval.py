"""Who may approve a verdict, and what approving does.

The owner of the affected area approves; the host can always override. An
approved verdict is applied by merging the winning branch into the room's
integration branch and creating a rework task for the other area. Files a
worker touched outside its declared area block the merge until acknowledged.
Autonomy mode (host only) approves and applies without waiting, but never
skips the out-of-area and merge-conflict checks.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from aletheore.council.protocol import Event, EventDraft, EventType, clip_text
from aletheore.council.state import RoomState, load_state
from aletheore.council.store import RoomStore
from aletheore.council.worktrees import (
    MergeResult, branch_changed_files, files_outside_area, merge_branch, worker_branch,
)


def _verdict(state: RoomState, verdict_id: str):
    if verdict_id not in state.verdicts:
        raise ValueError(f"unknown verdict: {verdict_id}")
    return state.verdicts[verdict_id]


def required_approver(state: RoomState, verdict_id: str) -> str:
    area = state.areas.get(_verdict(state, verdict_id).affected_area)
    return area.owner_participant if area else state.host


def can_approve(state: RoomState, verdict_id: str, participant: str) -> bool:
    return participant == state.host or participant == required_approver(state, verdict_id)


def _system(store: RoomStore, room_id: str, sender: str, cid: str, body: dict, thread: str = "room") -> Event:
    return store.append(EventDraft(room_id, cid, thread, EventType.SYSTEM, sender, None, None, body))


def _decide(store: RoomStore, room_id: str, verdict_id: str, participant: str, subtype: str) -> Event:
    state = load_state(store, room_id)
    _verdict(state, verdict_id)
    if not can_approve(state, verdict_id, participant):
        raise PermissionError(f"{participant} may not decide verdict {verdict_id}")
    return _system(store, room_id, participant, f"{subtype}:{verdict_id}:{participant}",
                   {"subtype": subtype, "verdict_id": verdict_id, "by": participant})


def approve(store: RoomStore, room_id: str, verdict_id: str, participant: str) -> Event:
    return _decide(store, room_id, verdict_id, participant, "verdict_approved")


def reject(store: RoomStore, room_id: str, verdict_id: str, participant: str) -> Event:
    return _decide(store, room_id, verdict_id, participant, "verdict_rejected")


def choose_winner(store: RoomStore, room_id: str, verdict_id: str, participant: str, winner: str) -> Event:
    """The host picks the winning side when the planner could not (`WINNER: none`)."""
    state = load_state(store, room_id)
    verdict = _verdict(state, verdict_id)
    if participant != state.host:
        raise PermissionError("only the host can choose a winner")
    if verdict.winning_branch:
        raise ValueError(f"verdict {verdict_id} already has a winner")
    if verdict.status in ("applied", "rejected"):
        raise ValueError(f"verdict {verdict_id} is {verdict.status}")
    if winner not in verdict.candidates:
        raise ValueError(f"{winner} is not one of the sides: {', '.join(sorted(verdict.candidates)) or '(none recorded)'}")
    return _system(store, room_id, participant, f"winner:{verdict_id}",
                   {"subtype": "winner_chosen", "verdict_id": verdict_id, "winner": winner})


def decide_plan(store: RoomStore, room_id: str, plan_id: str, participant: str, approved: bool) -> Event:
    """The host approves or rejects a planner's proposal. Approving lets the host's worker send the tasks out."""
    state = load_state(store, room_id)
    plan = state.plans.get(plan_id)
    if plan is None:
        raise ValueError(f"unknown plan: {plan_id}")
    if participant != state.host:
        raise PermissionError("only the host can decide a plan")
    if plan.status != "pending":
        raise ValueError(f"plan {plan_id} is already {plan.status}")
    sub = "plan_approved" if approved else "plan_rejected"
    return _system(store, room_id, participant, f"{sub}:{plan_id}", {"subtype": sub, "plan_id": plan_id, "by": participant},
                   thread=plan.thread_id)


def set_autonomy(store: RoomStore, room_id: str, participant: str, on: bool) -> Event:
    if participant != store.host_of(room_id):
        raise PermissionError("only the host can change autonomy mode")
    return _system(store, room_id, participant, f"autonomy:{uuid.uuid4().hex}", {"subtype": "autonomy_set", "on": bool(on)})


def apply_verdict(
    store: RoomStore, room_id: str, repo: Path, verdict_id: str, winning_branch: str | None, losing_area: str,
    merge_ref: str | None = None,
) -> MergeResult:
    """`winning_branch` is the logical branch name (used to find the owning area and task records).
    `merge_ref` is the ref to actually merge, when the branch came from another machine as a bundle."""
    state = load_state(store, room_id)
    verdict = _verdict(state, verdict_id)
    if not winning_branch:
        raise ValueError("this verdict has no winning branch; a human must choose one")
    if verdict.status == "applied":
        return MergeResult(True, [], "already applied")
    if verdict.status != "approved":
        raise PermissionError(f"verdict {verdict_id} is {verdict.status}, not approved")
    host = state.host or store.host_of(room_id)

    blocked_set = {f for t in state.tasks.values() if t.branch == winning_branch and t.status == "blocked"
                   and not t.acknowledged for f in t.blocked_files}
    # Do not trust recorded task status alone: recompute from the branch contents (renames reported as delete + add).
    acknowledged = {f for t in state.tasks.values() if t.branch == winning_branch and t.acknowledged for f in t.blocked_files}
    owner_area = next((a for a in state.areas.values() if worker_branch(room_id, a.agent_id) == winning_branch), None)
    if owner_area is not None:
        outside = files_outside_area(branch_changed_files(repo, room_id, merge_ref or winning_branch), owner_area.globs)
        blocked_set |= {f for f in outside if f not in acknowledged}
    blocked = sorted(blocked_set)
    if blocked:
        msg = f"blocked: files outside the declared area need acknowledgement: {', '.join(blocked)}"
        _system(store, room_id, host, f"apply-blocked:{verdict_id}:area:{','.join(blocked)}",
                {"subtype": "apply_blocked", "verdict_id": verdict_id, "reason": "out_of_area", "files": blocked})
        return MergeResult(False, [], msg)

    result = merge_branch(repo, room_id, merge_ref or winning_branch)
    if not result.ok:
        _system(store, room_id, host, f"apply-blocked:{verdict_id}:conflict:{','.join(result.conflicts)}",
                {"subtype": "apply_blocked", "verdict_id": verdict_id, "reason": "merge_conflict", "files": result.conflicts})
        return result

    _system(store, room_id, host, f"applied:{verdict_id}", {"subtype": "verdict_applied", "verdict_id": verdict_id})
    winner_area = owner_area.name if owner_area is not None else "the winning side"
    instruction = (
        f"A debate between {winner_area} and {losing_area} was settled by the planner (verdict {verdict_id}):\n"
        f"{clip_text(verdict.decision, 600)}\n\n"
        f"The winning side's work is now merged into the room's integration branch and has been brought into your branch. "
        f"Update the files inside your area ({losing_area}) so they are consistent with that decision. "
        f"Only change files inside your area."
    )
    store.append(EventDraft(
        room_id, f"rework:{verdict_id}", "room", EventType.TASK, host, None, None,
        {"op": "request", "task_id": f"rework-{verdict_id}", "area": losing_area, "instruction": instruction,
         "sync_integration": True, "reason": f"rework after verdict {verdict_id}"},
    ))
    return result


def maybe_auto_apply(store: RoomStore, room_id: str, repo: Path, verdict_id: str, losing_area: str) -> MergeResult | None:
    state = load_state(store, room_id)
    if not state.autonomy:
        return None
    verdict = _verdict(state, verdict_id)
    if not verdict.winning_branch:
        return None
    if verdict.status == "pending":
        _system(store, room_id, state.host, f"auto-approve:{verdict_id}",
                {"subtype": "verdict_approved", "verdict_id": verdict_id, "by": state.host, "auto": True})
    return apply_verdict(store, room_id, repo, verdict_id, verdict.winning_branch, losing_area)
