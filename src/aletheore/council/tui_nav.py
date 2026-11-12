"""Moving around a long room: search, jump to a date, fold old history, pinned messages. Pure helpers; the app wires them to keys."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from rich.text import Text

from . import theme
from .tui_cards import fit

_D = theme.DEPTH
WINDOW = 200  # how many of the newest messages are on screen before older history is folded


def _c(token: str) -> str:
    return theme.rich_color(token, _D)


def find_matches(items, term: str) -> list[int]:
    """Sequence numbers of the stream items whose text contains `term` (case-insensitive), oldest first. Empty term: no matches."""
    needle = term.strip().lower()
    if not needle:
        return []
    return [i.seq for i in items if needle in i.text.lower() or needle in i.author.lower()]


def next_index(current: int, count: int, step: int) -> int:
    """The match to move to, wrapping around either end. `current` is -1 before the first move."""
    if count <= 0:
        return -1
    return (current + step) % count if current >= 0 else (0 if step > 0 else count - 1)


def parse_day(text: str, now: datetime) -> date | None:
    """`today`, `yesterday`, `2026-11-09`, `9 Nov` or `9 Nov 2026`; None when it is none of these."""
    word = text.strip().lower()
    today = now.astimezone(timezone.utc).date()
    if word == "today":
        return today
    if word == "yesterday":
        return today - timedelta(days=1)
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %b"):
        try:
            parsed = datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
        year = parsed.year if "%Y" in fmt else today.year
        try:
            result = date(year, parsed.month, parsed.day)
        except ValueError:
            return None
        return result if result <= today or "%Y" in fmt else date(year - 1, parsed.month, parsed.day)
    return None


def first_item_on_or_after(items, day: date) -> int | None:
    """The seq of the first item created on or after `day` (UTC dates), or None when everything is older."""
    for item in items:
        try:
            when = datetime.fromisoformat(item.created_at.replace("Z", "+00:00")).astimezone(timezone.utc).date()
        except (ValueError, AttributeError):
            continue
        if when >= day:
            return item.seq
    return None


def fold_start(items, window: int) -> tuple[int, int]:
    """(seq of the first item to show, how many older items stay folded)."""
    if len(items) <= window:
        return 0, 0
    return items[-window].seq, len(items) - window


def fold_text(hidden: int) -> Text:
    t = Text()
    t.append(f"▸ {hidden} earlier message{'s' if hidden != 1 else ''}", style=f"bold {_c('muted')}")
    t.append("   enter or click to open them", style=_c("faint"))
    return t


def pins_text(pins: list[str], width: int = 23) -> Text:
    """The left panel's pinned messages: the first line of each, trimmed to fit."""
    t = Text()
    if not pins:
        return t
    t.append("\n" + "┄" * width + "\n", style=_c("line-strong"))
    t.append("◆ ", style=_c("accent"))
    t.append("PINNED\n", style=f"bold {_c('muted')}")
    for line in pins[:5]:
        t.append(fit(line, width) + "\n", style=_c("muted"))
    return t


def match_note(term: str, index: int, count: int) -> Text:
    t = Text()
    if count == 0:
        t.append(f"○ no match for “{fit(term, 30)}”", style=_c("needs"))
    else:
        t.append(f"● match {index + 1} of {count} for “{fit(term, 30)}”", style=f"bold {_c('accent')}")
        t.append("   f3 next · shift+f3 previous · esc clears", style=_c("faint"))
    return t


def strip_rows(items, height: int, view_top: float, view_bottom: float) -> list[tuple[str, str]]:
    """The timeline strip beside the stream: one (symbol, kind) per row. Plans, verdicts and the newest message are marked where
    they sit in the whole room; a bar shows what part is on screen. view_top and view_bottom are fractions from 0 (oldest) to 1."""
    height = max(1, height)
    rows: list[tuple[str, str]] = [("", "")] * height
    n = len(items)
    if n == 0:
        return rows
    for index, item in enumerate(items):
        row = min(height - 1, int(index / n * height))
        if item.kind == "plan":
            rows[row] = ("◆", "needs")
        elif item.kind == "verdict" and rows[row][1] != "needs":
            rows[row] = ("●", "ok")
    rows[height - 1] = ("▼", "accent")  # now
    top, bottom = int(view_top * height), max(int(view_top * height), min(height - 1, int(view_bottom * height)))
    for r in range(top, bottom + 1):
        if rows[r][0] == "":
            rows[r] = ("│", "faint")
    return rows


def strip_text(rows: list[tuple[str, str]]) -> Text:
    t = Text()
    for symbol, tone in rows:
        t.append((symbol or " ") + "\n", style=_c(tone) if tone else "")
    return t


def pin_click_target(pin_rows: tuple[int, list[int]], line: int) -> int | None:
    """Which pinned message a click on row `line` of the left panel means (pin_rows: first pin row, the seqs in order)."""
    start, seqs = pin_rows
    return seqs[line - start] if 0 <= line - start < len(seqs) else None
