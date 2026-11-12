"""Pure rendering of the stream's cards and dividers (no widgets, no terminal needed to test)."""

from __future__ import annotations

from datetime import datetime, timezone

from rich.cells import cell_len
from rich.text import Text

from . import theme

_D = theme.DEPTH


def _c(token: str) -> str:
    return theme.rich_color(token, _D)


def fit(text: str, width: int) -> str:
    """Cut `text` to `width` terminal cells with an ellipsis (wide characters count as two cells)."""
    if width <= 0:
        return ""
    if cell_len(text) <= width:
        return text
    out, used = "", 0
    for ch in text:
        w = cell_len(ch)
        if used + w > width - 1:
            break
        out, used = out + ch, used + w
    return out + "…"


def status_style(kind: str, status: str) -> str:
    """The colour of a card's status word. Amber means it waits for a person; red only for a rejection."""
    good = {"plan": ("dispatched", "approved"), "verdict": ("applied", "approved")}[kind]
    if status in good:
        return f"bold {_c('ok')}"
    if status == "rejected":
        return f"bold {_c('error')}"
    if status == "answered":
        return _c("muted")
    return f"bold {_c('needs')}"


def card_text(kind: str, ident: str, status: str, author: str, body: str, width: int) -> Text:
    """A plan or verdict card: a corner bracket top-left and bottom-right, the status in words as well as colour."""
    waiting = status_style(kind, status or "pending").endswith(_c("needs"))
    edge = _c("needs") if waiting else _c("line-strong")
    t = Text()
    t.append("┌ ", style=edge)
    t.append(kind.upper(), style=f"bold {_c('needs') if kind == 'plan' else _c('ok')}")
    t.append(f" {fit(ident, 20)}", style=_c("muted"))
    t.append("  " + (status or "pending"), style=status_style(kind, status or "pending"))
    if author:
        t.append(f"   by {fit(author, 24)}", style=_c("faint"))
    t.append("\n")
    t.append(body, style=_c("ink"))
    t.append("\n")
    t.append("└".rjust(max(2, width - 1)), style=edge)
    return t


def day_label(iso: str, now: datetime) -> str:
    try:
        then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return ""
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    days = (now.astimezone(then.tzinfo).date() - then.date()).days
    if days <= 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    stamp = f"{then.day} {then.strftime('%b')}"  # %-d is not portable (Windows rejects it)
    return f"{stamp} {then.year}" if days > 300 else stamp


def day_divider(label: str, width: int) -> Text:
    """`── Today ────────`: a dashed rule with the day in it."""
    t = Text()
    head = f"── {label} "
    t.append(head, style=_c("faint"))
    t.append("─" * max(0, width - cell_len(head)), style=_c("line-strong"))
    return t


def new_marker(count: int) -> Text:
    noun = "message" if count == 1 else "messages"
    t = Text()
    t.append(f" ↓ {count} new {noun} ", style=f"bold {_c('paper')} on {_c('accent')}")
    t.append("  end to jump", style=_c("faint"))
    return t
