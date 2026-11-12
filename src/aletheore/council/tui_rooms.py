"""Every room at once: a low-rate watcher, the room list with the needs-you section, and fuzzy matching for the switcher."""

from __future__ import annotations

import threading
from datetime import datetime

from rich.cells import cell_len
from rich.text import Text

from . import theme
from .state import project
from .terminal import RoomTail
from .tui_cards import fit
from .view_model import RoomSummary, cross_room_needs, summarize_room

_D = theme.DEPTH


def _c(token: str) -> str:
    return theme.rich_color(token, _D)


def fuzzy(query: str, text: str) -> list[int] | None:
    """Indexes in `text` where the characters of `query` appear in order (case-insensitive), or None when they do not."""
    if not query:
        return []
    low, hits, i = text.lower(), [], 0
    for ch in query.lower():
        i = low.find(ch, i)
        if i < 0:
            return None
        hits.append(i)
        i += 1
    return hits


def highlight(text: str, hits: list[int], base: str) -> Text:
    t = Text()
    marks = set(hits)
    for i, ch in enumerate(text):
        t.append(ch, style=f"bold underline {_c('accent')}" if i in marks else base)
    return t


class RoomWatcher:
    """Keeps a summary of every room the person is in without reading them all at once: one list call per round, and one
    other room's log per round (round robin), so ten rooms cost one small read each every ten rounds."""

    def __init__(self, store, me: str, resume=None) -> None:
        self.store, self.me, self.resume = store, me, resume
        self.summaries: dict[str, RoomSummary] = {}
        self._tails: dict[str, RoomTail] = {}
        self._turn = 0
        self.list_calls = 0
        self.log_reads = 0
        self._lock = threading.Lock()

    def round(self, current: str, current_state=None) -> None:
        """One refresh. The open room is passed in with its state (the app already follows it), so it costs nothing extra."""
        rooms = self.store.list_rooms(self.me)
        self.list_calls += 1
        others = sorted(r.room_id for r in rooms if r.room_id != current)
        target = others[self._turn % len(others)] if others else None
        self._turn += 1
        with self._lock:
            ids = {r.room_id for r in rooms}
            for gone in set(self.summaries) - ids:
                self.summaries.pop(gone, None)
                self._tails.pop(gone, None)
            for r in rooms:
                unread = max(0, r.last_seq - self.resume.read_seq(r.room_id)) if (self.resume and r.room_id != current) else 0
                if r.room_id == current and current_state is not None:
                    self.summaries[r.room_id] = summarize_room(r, current_state, self.me)
                elif r.room_id == target:
                    tail = self._tails.setdefault(r.room_id, RoomTail(self.store, r.room_id))
                    tail.poll()
                    self.log_reads += 1
                    self.summaries[r.room_id] = summarize_room(r, project(tail.all, host=r.host), self.me)
                elif r.room_id in self.summaries:  # keep its needs, refresh the cheap facts from the list
                    old = self.summaries[r.room_id]
                    old.title, old.mode, old.members, old.last_activity = r.title, r.mode, list(r.members), r.last_activity
                    old.state = "closed" if r.status != "open" else old.state
                    old.unread = unread
                else:
                    self.summaries[r.room_id] = RoomSummary(r.room_id, r.title, r.mode, "closed" if r.status != "open" else "live",
                                                            list(r.members), 0, r.last_activity)
            self._stamp_unread(rooms, current)

    def _stamp_unread(self, rooms, current: str) -> None:
        for r in rooms:
            if r.room_id in self.summaries:
                self.summaries[r.room_id].unread = (max(0, r.last_seq - self.resume.read_seq(r.room_id))
                                                    if (self.resume and r.room_id != current) else 0)

    def ordered(self) -> list[RoomSummary]:
        with self._lock:
            return sorted(self.summaries.values(), key=lambda s: (-s.needs, s.state == "closed", s.title.lower() or s.room_id))


def _dot(s: RoomSummary) -> tuple[str, str]:
    return {"waiting": ("●", _c("needs")), "closed": ("○", _c("faint"))}.get(s.state, ("●", _c("ok")))


def _sub(s: RoomSummary) -> str:
    """The quiet second line under a room: progress is drawn separately; this is the words (closed, or how many are unread)."""
    return "closed" if s.state == "closed" else (f"{s.unread} new" if s.unread else "")


def room_list_text(summaries: list[RoomSummary], current: str, width: int = 23) -> Text:
    """The left pane: every room with its state dot, title and count badge; the open one is a filled row with a white bar;
    below a dashed rule, what is waiting for you across all rooms."""
    plain = theme.DEPTH == "none"
    fill = _c("term")
    out = Text()
    out.append("§ ", style=_c("faint"))
    out.append("ROOMS", style=f"bold {_c('muted')}")
    out.append("  ctrl+r\n\n", style=_c("faint"))
    for s in summaries:
        here = s.room_id == current
        dot, dot_color = _dot(s)
        badge = f" {s.needs} " if s.needs else ""
        title = fit(s.title or s.room_id, width - 4 - len(badge))
        bg = "" if plain or not here else f" on {fill}"
        out.append("▌" if here else " ", style=f"bold {_c('accent')}{bg}")
        out.append(f" {dot} ", style=f"{dot_color}{bg}")
        out.append(title, style=f"{'bold ' if here else ''}{_c('ink') if here else _c('muted')}{bg}")
        pad = width - 4 - cell_len(title) - len(badge)
        out.append(" " * max(0, pad), style=bg.strip() if bg else "")
        if badge:
            out.append(badge, style=f"bold {_c('paper')} on {_c('needs')}")
        out.append("\n")
        if s.mode == "autonomous" and s.progress:
            done, total = s.progress
            filled = round(8 * done / total) if total else 0
            out.append("    " + "█" * filled + "░" * (8 - filled), style=_c("ok"))
            out.append(f" {done}/{total}\n", style=_c("faint"))
        elif _sub(s):
            out.append("    " + _sub(s) + "\n", style=_c("muted") if s.unread else _c("faint"))
    needs = cross_room_needs(summaries)
    if needs:
        out.append("\n" + "┄" * width + "\n", style=_c("line-strong"))
        out.append("◆ ", style=_c("needs"))
        out.append("NEEDS YOU\n", style=f"bold {_c('needs')}")
        for s, n in needs[:6]:
            out.append(f"{fit(s.title or s.room_id, width)}\n", style=_c("muted"))
            out.append("  " + fit(f"{n.kind} {n.verdict_id}", width - 2) + "\n", style=_c("faint"))
    return out


def room_at_row(summaries: list[RoomSummary], line: int) -> str | None:
    """Which room a click on row `line` of the list opens (row 0 is the heading, rooms start at row 2; the needs-you lines
    below the rule open the room they belong to), or None."""
    row = 2
    for s in summaries:
        height = 1 + (1 if (s.mode == "autonomous" and s.progress) or _sub(s) else 0)
        if row <= line < row + height:
            return s.room_id
        row += height
    needs = cross_room_needs(summaries)
    row += 3  # the blank line, the dashed rule and the NEEDS YOU heading
    for s, _ in needs[:6]:
        if row <= line < row + 2:
            return s.room_id
        row += 2
    return None
