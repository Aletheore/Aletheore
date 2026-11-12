"""The shell of the Council terminal: the top bar, the rooms pane, the key-hint line, and the rules for how the screen responds to
the window size. Pure functions that build Rich `Text`, so they can be tested without a terminal and drawn by any widget.

Sizes (columns by rows): 100 and up shows everything; 76 to 99 hides the right panel; 60 to 75 shows only the stream and composer
(the room list is still reachable with ctrl+r); under 60 columns or 20 rows one clear line asks for a larger window.
"""

from __future__ import annotations

from rich.cells import cell_len
from rich.text import Text

from aletheore.council import theme

MIN_WIDTH, MIN_HEIGHT = 60, 20
FULL_WIDTH, PANES_WIDTH = 100, 76


def layout_mode(width: int, height: int) -> str:
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        return "too-small"
    if width < PANES_WIDTH:
        return "stream-only"
    if width < FULL_WIDTH:
        return "no-panel"
    return "full"


def too_small_text(width: int, height: int) -> Text:
    return Text(f"Council needs a window of at least {MIN_WIDTH} by {MIN_HEIGHT}. This one is {width} by {height}. Please make it larger.",
                style=theme.color("needs"), justify="center")


def _block(fg_token: str, bg_token: str) -> str:
    """A filled block of colour (dark text on a light fill). With no colour at all it falls back to reverse video."""
    if theme.DEPTH == "none":
        return "bold reverse"
    return f"bold {theme.color(fg_token)} on {theme.color(bg_token)}"


def top_bar_text(width: int, *, title: str, room_id: str, sealed: bool, mode: str, round_no: int, closed: bool, finished: str, host: str,
                 me: str, autonomy: bool, connected: bool, pulse: bool = True) -> Text:
    """One row, three clear parts: the product mark as a filled white block on the left, then the room (labelled, title bold, id quiet),
    then the state on the right. Never longer than `width`: the room part is shortened with an ellipsis first, then optional
    right-hand items are dropped, so the mark and who you are always show. `pulse` alternates the live dot's brightness."""
    ink, muted, faint, accent, ok, needs, dim = (theme.color(t) for t in ("ink", "muted", "faint", "accent", "ok", "needs", "dim"))
    left = Text(" ◆ ALETHEORE COUNCIL ", style=_block("paper", "accent"))
    sep = Text(" │ ", style=dim)

    middle = Text()
    middle.append("ROOM ", style=faint)
    middle.append(title if title else room_id, style=f"bold {ink}")
    if title:
        middle.append(f"  {room_id}", style=faint)
    if mode == "autonomous":
        middle.append("  autonomous", style=needs)
        if round_no:
            middle.append(f" · round {round_no}", style=needs)
    if closed:
        middle.append("  closed", style=muted)
    if finished:
        middle.append(f"  finished ({finished})", style=ok if finished == "done" else needs)
    if autonomy:
        middle.append("  autonomy on", style=needs)

    stamp = Text("▌SEALED▐", style=f"bold {accent}") if sealed else None
    live_style = ok if pulse else dim  # the caller passes pulse=True when motion is off, so the dot stays solid
    conn = Text("● live", style=live_style) if connected else Text("● server unreachable, retrying", style=needs)
    conn_short = Text("● live", style=live_style) if connected else Text("● unreachable", style=needs)
    host_t = Text(f"host {host}", style=muted) if host else None
    you = Text(f"you: {me}", style=theme.person_color(me, theme.DEPTH))

    def join(parts: list[Text | None]) -> Text:
        out = Text()
        for part in parts:
            if part is None:
                continue
            if out.plain:
                out.append("  ")
            out.append_text(part)
        return out

    variants = [join([stamp, conn, host_t, you]), join([stamp, conn, you]), join([stamp, conn_short, you]), join([stamp, you]), join([you])]
    gap_min = 2
    min_middle = 8
    right = variants[-1]
    for candidate in variants:
        if left.cell_len + sep.cell_len + min_middle + gap_min + candidate.cell_len <= width:
            right = candidate
            break
    room_space = max(0, width - left.cell_len - sep.cell_len - right.cell_len - gap_min)
    if middle.cell_len > room_space:
        middle.truncate(room_space, overflow="ellipsis")
    out = Text()
    out.append_text(left)
    out.append_text(sep)
    out.append_text(middle)
    pad = width - out.cell_len - right.cell_len
    out.append(" " * max(1, pad))
    out.append_text(right)
    if out.cell_len > width:  # a window so narrow that even the minimum does not fit: keep the left side whole
        out.truncate(width, overflow="ellipsis")
    return out


def key_hints(needs: int, width: int) -> Text:
    """The one line of keys under the composer. It changes with what is waiting and shrinks with the window."""
    faint = theme.color("faint")
    if needs:
        full = "a approve · r reject · s skip · enter send · /task · /debate · ctrl+r rooms · ctrl+c quit"
        short = "a approve · r reject · ctrl+r rooms"
    else:
        full = "enter send · @all/@agent ask · /task · /debate · ctrl+r rooms · ctrl+c quit"
        short = "enter send · ctrl+r rooms · ctrl+c quit"
    text = full if cell_len(full) <= width else short
    return Text(text if cell_len(text) <= width else "", style=faint)


def status_line(left: Text, needs: int, width: int) -> Text:
    """Status on the left, keys on the right when they fit; keys are dropped rather than the window overflowing."""
    out = Text()
    out.append_text(left)
    room = width - left.cell_len - 4
    hints = key_hints(needs, room) if room > 0 else Text("")
    if hints.plain:
        out.append("    ")
        out.append_text(hints)
    if out.cell_len > width:
        out.truncate(width, overflow="ellipsis")
    return out


def fit_cells(text: str, width: int) -> str:
    from .tui_cards import fit

    return fit(text, width)


def rooms_text(title: str, room_id: str, status: str, mode: str, state_line: str, width: int = 23, needs: int = 0) -> Text:
    """The rooms pane. Task T4 makes this the live list of every room; for now it shows the current room as a selected, filled row:
    a white bar down its left edge, the title bold, a green live dot, and its state in quiet text."""
    muted, faint, accent, ink, ok = (theme.color(t) for t in ("muted", "faint", "accent", "ink", "ok"))
    fill = theme.color("term")
    plain = theme.DEPTH == "none"
    out = Text()
    out.append("§ ", style=faint)
    out.append("ROOMS", style=f"bold {muted}")
    out.append("  ctrl+r\n\n", style=faint)

    def line(parts: list[tuple[str, str]]) -> None:
        used = 0
        for text, style in parts:
            text = text[: max(0, width - used)]
            out.append(text, style=style if plain else f"{style} on {fill}")
            used += cell_len(text)
        out.append(" " * max(0, width - used), style="" if plain else f"on {fill}")
        out.append("\n")

    count = f" {needs} " if needs else ""
    line([("▌ ", f"bold {accent}"), (fit_cells(title or room_id, width - 2 - len(count)), f"bold {ink}"),
          *([(count, f"bold {theme.color('paper')} on {theme.color('needs')}")] if count else [])])
    line([("▌ ", f"bold {accent}"), ("● ", ok), (state_line, muted)])
    line([("▌ ", f"bold {accent}"), (f"{status} · {mode}", faint)])
    if needs:
        out.append(f"\n◆ {needs} waiting on you\n", style=theme.color("needs"))
    return out
