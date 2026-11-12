"""The join request screen: who is asking, four words to compare out loud, and a deliberate approve."""

from __future__ import annotations

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from . import theme
from .tui_cards import fit

_D = theme.DEPTH


def _c(token: str) -> str:
    return theme.rich_color(token, _D)


def join_text(req: dict, title: str, epoch: int | None, sealed: bool, words_read: bool) -> Text:
    """Everything the host needs to decide, in plain words. Text from the request is shown literally."""
    t = Text()
    t.append("SOMEONE WANTS TO JOIN\n", style=f"bold {_c('needs')}")
    t.append(f"{fit(str(req.get('handle', '?')), 40)}", style=f"bold {_c('ink')}")
    t.append(f"  asks to join {fit(title, 40)}\n\n", style=_c("muted"))
    t.append("Ask them to read these four words to you. They must match exactly.\n\n", style=_c("muted"))
    words = [w for w in str(req.get("words", "")).replace("-", " ").split()][:4]
    t.append("   " + "   ".join(words).upper() + "\n\n", style=f"bold {_c('accent')}")
    t.append(f"device  {fit(str(req.get('device_id', ''))[:24], 28)}\n", style=_c("faint"))
    if sealed:
        t.append(f"\nApproving gives them the room key (epoch {epoch if epoch is not None else '?'}) and lets them read what is\n"
                 f"shared from now on. Older messages stay closed unless you grant them later.\n", style=_c("muted"))
    else:
        t.append("\nApproving lets them read and write in this room.\n", style=_c("muted"))
    if not words_read:
        t.append("\nPress tab to focus the words, compare them, then approve.\n", style=_c("needs"))
    return t


class JoinModal(ModalScreen):
    AUTO_FOCUS = "#later"  # never start on the words: reading them has to be a step the host takes
    BINDINGS = [Binding("escape", "later", "later"), Binding("a", "approve", "approve"), Binding("r", "reject", "reject")]
    DEFAULT_CSS = """
    JoinModal { align: center middle; background: $paper 70%; }
    #join { width: 78; max-width: 96%; height: auto; background: $raised; padding: 1 2; border: round $needs; }
    #join-words { height: auto; }
    #join-words:focus { background: $term; }
    #join-buttons { height: 3; margin-top: 1; }
    #join-buttons Button { margin-right: 2; min-width: 14; }
    """

    def __init__(self, req: dict, title: str, epoch: int | None, sealed: bool) -> None:
        super().__init__()
        self.req, self.title_, self.epoch, self.sealed = req, title, epoch, sealed
        self.words_read = False

    def compose(self) -> ComposeResult:
        with Vertical(id="join"):
            words = Static(id="join-words", markup=False)
            words.can_focus = True
            yield words
            with Horizontal(id="join-buttons"):
                yield Button("Approve (a)", id="approve", variant="primary", disabled=True)
                yield Button("Reject (r)", id="reject")
                yield Button("Later (esc)", id="later")

    def on_mount(self) -> None:
        self._draw()

    def _draw(self) -> None:
        self.query_one("#join-words", Static).update(join_text(self.req, self.title_, self.epoch, self.sealed, self.words_read))
        self.query_one("#approve", Button).disabled = not self.words_read

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        if getattr(event.widget, "id", "") == "join-words" and not self.words_read:
            self.words_read = True  # a deliberate step: the host has been at the words before approving
            self._draw()

    def action_approve(self) -> None:
        if self.words_read:
            self.dismiss("approve")
        else:
            self.notify("read the four words with them first (tab)", severity="warning")

    def action_reject(self) -> None:
        self.dismiss("reject")

    def action_later(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        {"approve": self.action_approve, "reject": self.action_reject, "later": self.action_later}[event.button.id]()
