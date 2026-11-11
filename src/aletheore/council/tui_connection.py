"""What the screen says when the server or the host cannot be reached: staged, in plain words, never a wall of red."""

from __future__ import annotations

from rich.text import Text

from . import theme
from .view_model import ConnectionState

_D = theme.DEPTH


def _c(token: str) -> str:
    return theme.rich_color(token, _D)


def _ago(secs: int) -> str:
    return f"{secs} s" if secs < 90 else f"{secs // 60} min"


def banner_text(state: ConnectionState, reason: str) -> Text | None:
    """None while everything is fine. Amber, with a symbol and words; says whose side is down and what happens next."""
    if state.kind == "live":
        return None
    who = "your connection to the server is down" if reason == "network" else "the host is not answering"
    t = Text()
    if state.kind == "reconnecting":
        t.append("◌ reconnecting ", style=f"bold {_c('needs')}")
        t.append(f"({_ago(state.since)}). Nothing is lost; trying again in {state.next_try_s} s.", style=_c("muted"))
    elif state.kind == "unreachable":
        t.append("◌ unreachable ", style=f"bold {_c('needs')}")
        t.append(f"for {_ago(state.since)}: {who}. Messages you send wait here ", style=_c("muted"))
        t.append(f"({state.queued} waiting)" if state.queued else "(none waiting)", style=_c("faint"))
        t.append(f"; next try in {state.next_try_s} s.", style=_c("muted"))
    else:
        t.append("◌ away ", style=f"bold {_c('needs')}")
        t.append(f"for {_ago(state.since)}: {who}. Agents finish their current task and hold results until it is back; "
                 "everything will catch up when it returns.", style=_c("muted"))
    return t


def works_text(state: ConnectionState) -> Text:
    """For the side panel: what still works and what waits while things are down."""
    t = Text()
    if state.kind == "live":
        return t
    t.append("§ WHILE AWAY\n", style=f"bold {_c('muted')}")
    for line, ok in (("reading what you have", True), ("writing messages (queued)", True), ("agents starting tasks", state.kind != "away"),
                     ("approvals", False), ("new members", False)):
        t.append(("✓ " if ok else "○ "), style=_c("ok") if ok else _c("faint"))
        t.append(line + "\n", style=_c("ink") if ok else _c("faint"))
    return t


def returned_text(away_s: int) -> Text:
    return Text(f"  · the host is back after {_ago(away_s)}; everything is up to date", style=_c("ok"))
