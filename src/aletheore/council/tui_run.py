"""The autonomous run: a goal card that follows the log (rounds, steps, merged work, limits, how it ended)."""

from __future__ import annotations

from dataclasses import dataclass, field

from rich.text import Text

from . import theme
from .conductor import MAX_ROUNDS, MAX_STEPS
from .protocol import EventType
from .tui_cards import fit

_D = theme.DEPTH


def _c(token: str) -> str:
    return theme.rich_color(token, _D)


@dataclass
class RunTimeline:
    goal: str
    rounds: list[tuple[str, int, str]] = field(default_factory=list)  # (plan id, step count, status)
    steps: int = 0
    merged: int = 0
    finished: str = ""
    note: str = ""


def run_timeline(events, state) -> RunTimeline | None:
    """Built from the room's state (which remembers the goal), so it works however much of the log is on screen: None for an ordinary room."""
    if not state.goal:
        return None
    mine = [p for p in state.plans.values() if p.thread_id == state.goal_thread or p.thread_id.startswith(state.goal_thread + "-r")]
    return RunTimeline(
        goal=state.goal, rounds=[(p.plan_id, len(p.tasks), p.status) for p in mine],
        steps=sum(len(p.tasks) for p in mine), merged=sum(1 for v in state.verdicts.values() if v.status == "applied"),
        finished=state.finished, note=state.finish_note)


def run_card_text(tl: RunTimeline, width: int, host: bool) -> Text:
    waiting = not tl.finished
    edge = _c("needs") if waiting else _c("line-strong")
    t = Text()
    t.append("┌ ", style=edge)
    t.append("GOAL", style=f"bold {_c('accent')}")
    t.append("  " + ("running" if waiting else {"done": "goal met", "limit": "stopped at the limit", "stopped": "stopped by the host"}.get(tl.finished, tl.finished)),
             style=f"bold {_c('ok') if tl.finished == 'done' else _c('needs')}")
    t.append("\n" + tl.goal.strip()[:400] + "\n", style=_c("ink"))
    for i, (plan_id, count, status) in enumerate(tl.rounds, 1):
        mark, col = {"dispatched": ("✓", "ok"), "approved": ("◐", "ok"), "rejected": ("✕", "error")}.get(status, ("○", "muted"))
        t.append(f"  {mark} round {i}", style=_c(col))
        t.append(f"  {count} step{'s' if count != 1 else ''}  {status}\n", style=_c("muted"))
    t.append(f"  round {len(tl.rounds)} of {MAX_ROUNDS} · {tl.steps} of {MAX_STEPS} steps (default limits) · {tl.merged} merged\n", style=_c("faint"))
    if tl.finished and tl.note:
        t.append("  " + fit(tl.note, max(20, width - 4)) + "\n", style=_c("muted"))
    if waiting and host:
        t.append("  ctrl+x stops the run\n", style=_c("faint"))
    t.append("└".rjust(max(2, width - 1)), style=edge)
    return t
