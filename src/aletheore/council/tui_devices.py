"""Your devices: each machine that holds this account's identity, the words to check them by, and link or revoke."""

from __future__ import annotations

from datetime import datetime

from rich.text import Text
from textual import events
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


def _added(ts: float, now: float) -> str:
    secs = int(now - ts)
    return "just now" if secs < 60 else f"{secs // 60}m ago" if secs < 3600 else f"{secs // 3600}h ago" if secs < 86400 else f"{secs // 86400}d ago"


def devices_text(data: dict, now: float, selected: int, words_read: bool, confirm_revoke: bool) -> Text:
    """Active devices first, then link requests waiting for approval. `selected` indexes devices then pending requests."""
    t = Text()
    t.append("§ ", style=_c("faint"))
    t.append(f"YOUR DEVICES  {fit(str(data.get('handle', '')), 30)}\n\n", style=f"bold {_c('muted')}")
    active = [d for d in data.get("devices", []) if not d.get("revoked")]
    for i, d in enumerate(active):
        here = i == selected
        t.append("▌ " if here else "  ", style=f"bold {_c('accent')}")
        t.append("● ", style=_c("ok"))
        t.append(fit(d["device_id"][:16], 18), style=f"bold {_c('ink')}")
        t.append(" (this device)" if d.get("current") else "", style=_c("faint"))
        t.append(f"\n    words {d['words']}   added {_added(float(d.get('created_at', now)), now)}\n", style=_c("muted"))
    pending = data.get("pending", [])
    if pending:
        t.append("\n")
        t.append("◆ ", style=_c("needs"))
        t.append("WAITING TO BE LINKED\n", style=f"bold {_c('needs')}")
    for j, p in enumerate(pending):
        here = len(active) + j == selected
        t.append("▌ " if here else "  ", style=f"bold {_c('accent')}")
        t.append(fit(p["device_id"][:16], 18), style=_c("ink"))
        t.append("\n    the new machine shows these four words. They must match exactly:\n", style=_c("muted"))
        t.append("    " + "   ".join(str(p["words"]).replace("-", " ").split()[:4]).upper() + "\n", style=f"bold {_c('accent')}")
        if here and not words_read:
            t.append("    compare them, then press w to confirm they match\n", style=_c("needs"))
        elif here:
            t.append("    words confirmed: press a to link this device\n", style=_c("ok"))
    t.append("\n")
    if confirm_revoke:
        t.append("Revoke this device? It can no longer sign in. Sealed rooms you host move to a new key now; rooms\n"
                 "other people host re-key the next time their Council is open. Press v again to confirm, any other key cancels.\n", style=_c("needs"))
    t.append("↑↓ select · w confirm words · a link · v revoke · esc close", style=_c("faint"))
    return t


class DevicesScreen(ModalScreen):
    BINDINGS = [Binding("escape", "close", "close"), Binding("up", "move(-1)", ""), Binding("down", "move(1)", ""),
                Binding("w", "words", ""), Binding("a", "link", ""), Binding("v", "revoke", "")]
    DEFAULT_CSS = """
    DevicesScreen { align: center middle; background: $paper 70%; }
    #devices { width: 92; max-width: 96%; height: auto; max-height: 90%; background: $raised; padding: 1 2; border: round $line-strong; }
    """

    def __init__(self, load, link, revoke) -> None:
        super().__init__()
        self._load, self._link, self._revoke = load, link, revoke
        self.data: dict = {"devices": [], "pending": []}
        self.selected, self.words_read, self.confirm = 0, False, False

    def compose(self) -> ComposeResult:
        with Vertical(id="devices"):
            yield Static(id="devices-body", markup=False)

    def on_mount(self) -> None:
        self.refresh_data()

    def refresh_data(self) -> None:
        try:
            self.data = self._load()
        except (ConnectionError, OSError, PermissionError, AttributeError) as exc:
            self.data = {"devices": [], "pending": [], "handle": f"could not load: {exc}"}
        self._draw()

    def _active(self) -> list[dict]:
        return [d for d in self.data.get("devices", []) if not d.get("revoked")]

    def _draw(self) -> None:
        self.query_one("#devices-body", Static).update(
            devices_text(self.data, datetime.now().timestamp(), self.selected, self.words_read, self.confirm))

    def _pending_at(self) -> dict | None:
        i = self.selected - len(self._active())
        pending = self.data.get("pending", [])
        return pending[i] if 0 <= i < len(pending) else None

    def action_move(self, step: int) -> None:
        total = len(self._active()) + len(self.data.get("pending", []))
        self.selected = max(0, min(max(0, total - 1), self.selected + step))
        self.words_read, self.confirm = False, False
        self._draw()

    def action_words(self) -> None:
        if self._pending_at() is not None:
            self.words_read = True
            self._draw()

    def action_link(self) -> None:
        req = self._pending_at()
        if req is None:
            return
        if not self.words_read:
            self.notify("compare the four words first (press w once they match)", severity="warning")
            return
        self._link(req)
        self.words_read = False
        self.refresh_data()

    def action_revoke(self) -> None:
        active = self._active()
        device = active[self.selected] if 0 <= self.selected < len(active) else None
        if device is None or device.get("current"):
            self.notify("pick another device to revoke; this one is the one you are using", severity="warning")
            return
        if not self.confirm:
            self.confirm = True
            self._draw()
            return
        self.confirm = False
        self._revoke(device)
        self.selected = 0
        self.refresh_data()

    def on_key(self, event: events.Key) -> None:
        if self.confirm and event.key != "v":
            self.confirm = False
            self._draw()

    def action_close(self) -> None:
        self.dismiss(None)
