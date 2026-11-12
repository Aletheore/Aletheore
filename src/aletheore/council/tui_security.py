"""The security screen for one room: who can read it, how far they were checked, what was ignored, what the server sees."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from . import theme
from .tui_cards import fit

_D = theme.DEPTH


def _c(token: str) -> str:
    return theme.rich_color(token, _D)


def security_text(room: dict, selected: int) -> Text:
    """`room`: sealed, epoch, members [{name, host, you, status, devices}], counters {verified, rejected, unreadable, gaps}."""
    t = Text()
    t.append("§ ", style=_c("faint"))
    t.append("SECURITY  ", style=f"bold {_c('muted')}")
    if not room.get("sealed"):
        t.append("\n\nThis room is not sealed. The server holds the messages in a form it can read.\n", style=_c("ink"))
        t.append("People are identified by the server's list, not by checked devices.\n\nesc close", style=_c("faint"))
        return t
    t.append("▌SEALED▐", style=f"bold {_c('accent')}")
    t.append(f"  key {room.get('epoch') if room.get('epoch') is not None else '?'}\n\n", style=_c("muted"))
    t.append("WHO CAN READ THIS ROOM\n", style=f"bold {_c('muted')}")
    words = {"compared": ("✓", "ok", "words compared"), "partly": ("◐", "needs", "some devices compared"),
             "none": ("○", "faint", "not compared yet"), "changed": ("!", "needs", "a device is new or changed since you compared")}
    for i, m in enumerate(room.get("members", [])):
        mark, tone, label = words[m.get("status", "none")]
        t.append("▌ " if i == selected else "  ", style=f"bold {_c('accent')}")
        t.append(f"{fit(m['name'], 20)}", style=f"bold {_c('ink')}")
        t.append(" host" if m.get("host") else "", style=_c("faint"))
        t.append(" you" if m.get("you") else "", style=_c("faint"))
        t.append(f"  {m.get('devices', 0)} device{'s' if m.get('devices', 0) != 1 else ''}  ", style=_c("muted"))
        t.append(f"{mark} {label}\n", style=_c(tone))
        if i == selected and m.get("words") and not m.get("you"):
            t.append("      check these with them, out loud: " + "  |  ".join(str(w).replace("-", " ") for w in m["words"]) + "\n", style=_c("accent"))
    c = room.get("counters", {})
    t.append("\nWHAT THIS DEVICE CHECKED\n", style=f"bold {_c('muted')}")
    t.append(f"✓ {c.get('verified', 0)} messages read and checked\n", style=_c("ok"))
    bad = [(c.get("rejected", 0), "failed a check (signature or sender)"), (c.get("unreadable", 0), "could not be opened (no key for them)"),
           (c.get("gaps", 0), "gaps in the server's order (something was skipped)")]
    if any(n for n, _ in bad):
        for n, why in bad:
            if n:
                t.append(f"! {n} {why}\n", style=_c("needs"))
    else:
        t.append("✓ nothing ignored, no gaps\n", style=_c("ok"))
    t.append("\nWHAT THE SERVER CAN AND CANNOT SEE\n", style=f"bold {_c('muted')}")
    t.append("can see   who is in the room, room title, when and how large messages are, which devices sent them\n", style=_c("muted"))
    t.append("cannot    the text, the files, who wrote what inside, or the room key\n", style=_c("ok"))
    t.append("\nc mark the selected person's words compared · g grant older history (host) · esc close", style=_c("faint"))
    return t


class SecurityScreen(ModalScreen):
    BINDINGS = [Binding("escape", "close", "close"), Binding("up", "move(-1)", ""), Binding("down", "move(1)", ""),
                Binding("c", "compare", ""), Binding("g", "history", "")]
    DEFAULT_CSS = """
    SecurityScreen { align: center middle; background: $paper 70%; }
    #security { width: 100; max-width: 96%; height: auto; max-height: 92%; background: $raised; padding: 1 2; border: round $line-strong; }
    """

    def __init__(self, load, compare, history) -> None:
        super().__init__()
        self._load, self._compare, self._history = load, compare, history
        self.room: dict = {}
        self.selected = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="security"):
            yield Static(id="security-body", markup=False)

    def on_mount(self) -> None:
        self.refresh_data()

    def refresh_data(self) -> None:
        self.room = self._load()
        self.query_one("#security-body", Static).update(security_text(self.room, self.selected))

    def _member(self) -> dict | None:
        members = self.room.get("members", [])
        return members[self.selected] if 0 <= self.selected < len(members) else None

    def action_move(self, step: int) -> None:
        self.selected = max(0, min(max(0, len(self.room.get("members", [])) - 1), self.selected + step))
        self.refresh_data()

    def action_compare(self) -> None:
        m = self._member()
        if m is not None and not m.get("you"):
            self._compare(m)
            self.refresh_data()

    def action_history(self) -> None:
        m = self._member()
        if m is not None and self.room.get("host_is_me") and not m.get("you"):
            self._history(m)
            self.refresh_data()

    def action_close(self) -> None:
        self.dismiss(None)
