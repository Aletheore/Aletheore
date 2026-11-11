"""The command palette (ctrl+k) and the key help (f1, or ? with an empty message box): every action and its key, in one place."""

from __future__ import annotations

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static

from . import theme
from .tui_rooms import fuzzy, highlight

_D = theme.DEPTH

# (what it does, the key, the app action that does it). One list: the palette, the help screen and the tests all read it.
ACTIONS: list[tuple[str, str, str]] = [
    ("Switch room", "ctrl+r", "rooms"),
    ("Search this room", "ctrl+f", "search"),
    ("Next match", "f3", "next_match"),
    ("Previous match", "shift+f3", "prev_match"),
    ("Jump to a day", "ctrl+t", "jump_date"),
    ("Jump to the latest message", "end", "jump_end"),
    ("Pin or unpin the focused message", "p", ""),
    ("Show or hide the side panel", "tab", "toggle_sidebar"),
    ("See who is waiting to join", "ctrl+g", "joins"),
    ("Your devices", "ctrl+d", "devices"),
    ("Security of this room", "ctrl+s", "security"),
    ("Stop the autonomous run (host)", "ctrl+x", "stop_run"),
    ("Approve the plan or verdict waiting on you", "a", "approve"),
    ("Reject it", "r", "reject"),
    ("Skip it for now", "s", "skip"),
    ("Key help", "f1", "help"),
    ("Quit", "ctrl+c", "quit"),
]


def help_text() -> Text:
    t = Text()
    t.append("KEYS\n\n", style=f"bold {theme.rich_color('muted', _D)}")
    for label, key, _ in ACTIONS:
        t.append(f"{key:<10}", style=f"bold {theme.rich_color('accent', _D)}")
        t.append(label + "\n", style=theme.rich_color("ink", _D))
    t.append("\nIn the message box: enter sends, ctrl+j adds a line, ctrl+v pastes an image, @all or @name asks, /task and /debate assign work.\n",
             style=theme.rich_color("faint", _D))
    t.append("COUNCIL_REDUCED_MOTION=1 stops animation; NO_COLOR=1 removes colour. esc closes this.", style=theme.rich_color("faint", _D))
    return t


class PaletteScreen(ModalScreen):
    """Type to filter, up and down, enter runs it."""

    BINDINGS = [Binding("escape", "close", "close")]
    DEFAULT_CSS = """
    PaletteScreen { align: center middle; background: $paper 70%; }
    #palette { width: 72; max-width: 96%; height: auto; max-height: 80%; background: $raised; padding: 1 2; border: round $accent; }
    #palette-list { height: auto; max-height: 18; background: $raised; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.needle = ""
        self.shown: list[tuple[str, str, str]] = list(ACTIONS)

    def compose(self) -> ComposeResult:
        with Vertical(id="palette"):
            yield Static(id="palette-head", markup=False)
            yield OptionList(id="palette-list")

    def on_mount(self) -> None:
        self._refill()
        self.query_one("#palette-list", OptionList).focus()

    def _refill(self) -> None:
        lst = self.query_one("#palette-list", OptionList)
        lst.clear_options()
        self.shown = []
        for label, key, action in ACTIONS:
            hits = fuzzy(self.needle, label)
            if hits is None:
                continue
            self.shown.append((label, key, action))
            row = highlight(label, hits, "")
            row.append(f"   {key}", style=theme.rich_color("faint", _D))
            lst.add_option(row)
        if self.shown:
            lst.highlighted = 0
        head = Text()
        head.append("command  ", style=theme.rich_color("muted", _D))
        head.append(self.needle or "type to filter", style=theme.rich_color("accent", _D) if self.needle else theme.rich_color("faint", _D))
        self.query_one("#palette-head", Static).update(head)

    def on_key(self, event: events.Key) -> None:
        if event.key == "backspace":
            self.needle = self.needle[:-1]
        elif event.is_printable and event.character:
            self.needle += event.character
        else:
            return
        event.stop()
        self._refill()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.shown[event.option_index][2])

    def action_close(self) -> None:
        self.dismiss(None)


class HelpScreen(ModalScreen):
    BINDINGS = [Binding("escape", "close", "close")]
    DEFAULT_CSS = """
    HelpScreen { align: center middle; background: $paper 70%; }
    #help { width: 84; max-width: 96%; height: auto; max-height: 92%; background: $raised; padding: 1 2; border: round $line-strong; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="help"):
            yield Static(help_text(), markup=False)

    def action_close(self) -> None:
        self.dismiss(None)
