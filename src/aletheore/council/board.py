"""The task board: every task in a room on one screen, worked out from the log and the room state (it keeps nothing of its own)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from aletheore.council.daemon import SILENT_AFTER_S, UNCLAIMED_AFTER_S
from aletheore.council.protocol import Event, EventType, clip_text
from aletheore.council.state import RoomState

_STATUS = {"assigned": "waiting", "running": "running", "done": "done", "failed": "failed", "blocked": "blocked", "cancelled": "cancelled"}
_CLOSED = ("done", "failed", "cancelled")


@dataclass(frozen=True)
class BoardRow:
    task_id: str
    area: str
    owner: str
    agent: str
    status: str       # waiting | running | done | failed | blocked
    branch: str
    instruction: str
    idle_s: float     # seconds since anything was said about the task
    stuck: str        # "" | "unclaimed" (never started) | "silent" (running, but nothing for a while)
    note: str = ""    # why it is blocked, or "no files changed" on a task that finished without changing anything


def _ts(e: Event) -> float:
    try:
        return datetime.fromisoformat(e.created_at.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def board_rows(state: RoomState, events: list[Event], now: float,
               unclaimed_after_s: float = UNCLAIMED_AFTER_S, silent_after_s: float = SILENT_AFTER_S) -> list[BoardRow]:
    """One row per task, open work first (newest first), then finished work."""
    instruction: dict[str, str] = {}
    last: dict[str, float] = {}
    order: dict[str, int] = {}
    for e in events:
        tid = e.body.get("task_id") if e.type in (EventType.TASK, EventType.SYSTEM, EventType.RESULT) else None
        if not isinstance(tid, str):
            continue
        if e.type is EventType.SYSTEM and e.body.get("subtype") != "task_progress":
            continue  # only a beat from the running task is the task moving: the host's own "stalled" notice, or an acknowledgement, is not
        last[tid] = max(last.get(tid, 0.0), _ts(e))
        if e.type is EventType.TASK and e.body.get("op") in ("request", "assign"):
            instruction.setdefault(tid, str(e.body.get("instruction", "")))
            order.setdefault(tid, e.seq)
    rows = []
    for tid, task in state.tasks.items():
        area = state.areas.get(task.area)
        idle = max(0.0, now - last[tid]) if tid in last else 0.0
        stuck = ""
        if task.status == "assigned" and idle > unclaimed_after_s:
            stuck = "unclaimed"
        elif task.status == "running" and idle > silent_after_s:
            stuck = "silent"
        rows.append(BoardRow(tid, task.area, area.owner_participant if area else "?", area.agent_id if area else "?",
                             _STATUS.get(task.status, task.status), task.branch or "", instruction.get(tid, ""), idle, stuck, task.note))
    rows.sort(key=lambda r: (r.status in _CLOSED, -order.get(r.task_id, 0)))
    return rows


def _age(seconds: float) -> str:
    return f"{int(seconds)}s" if seconds < 90 else f"{int(seconds // 60)}m" if seconds < 5400 else f"{int(seconds // 3600)}h"


def render_board(rows: list[BoardRow]) -> str:
    if not rows:
        return "No tasks yet."
    lines = [f"{'TASK':<14} {'AREA':<10} {'WHO':<18} {'STATUS':<9} {'IDLE':<5} WHAT"]
    for r in rows:
        status = r.status + (f" ({r.stuck})" if r.stuck else "")
        what = clip_text(' '.join(r.instruction.split()), 80) + (f"  [{clip_text(r.note, 80)}]" if r.note else "")
        lines.append(f"{r.task_id[:14]:<14} {r.area[:10]:<10} {(r.owner + '/' + r.agent)[:18]:<18} {status:<9} {_age(r.idle_s):<5} {what}")
    return "\n".join(lines)
