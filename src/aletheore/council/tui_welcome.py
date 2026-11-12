"""First run: one centred screen, three steps. Nothing leaves this machine until the person presses enter on a step."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, NamedTuple

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, Static

from . import theme

from .sponsor import SPONSOR  # noqa: E402  (kept importable from here)
_D = theme.DEPTH


def _c(token: str) -> str:
    return theme.rich_color(token, _D)


class Actions(NamedTuple):
    """What the screen can do. The command line supplies the real ones; tests supply fakes (so no test touches a network)."""
    check: Callable[[str], tuple[bool, int, str]]  # server -> (reachable, milliseconds, one plain line)
    register: Callable[[str, str, str], str]  # server, code, handle -> message
    join: Callable[[str, str], str]  # link, handle -> message (the host still has to approve)


def looks_like_link(text: str) -> bool:
    return text.strip().lower().startswith(("http://", "https://")) and "/j/" in text


class WelcomeApp(App):
    CSS = """
    Screen { background: $paper; color: $ink; align: center middle; }
    #card { width: 84; max-width: 96%; height: auto; background: $raised; padding: 1 3; border: round $line-strong; }
    Input { margin: 0 0 1 0; background: $term; border: tall $line-strong; }
    Input:focus { border: tall $accent; }
    .step { height: auto; margin-top: 1; }
    """

    def __init__(self, device_id: str, words: str, actions: Actions, marker: Path, server: str = "") -> None:
        super().__init__()
        self.device_id, self.words, self.actions, self.marker, self.server = device_id, words, actions, marker, server
        self.result: dict | None = None
        self.reachable = False

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables.update(theme.css_variables(_D))
        return variables

    def compose(self) -> ComposeResult:
        with Vertical(id="card"):
            head = Text()
            head.append(" ◆ ALETHEORE COUNCIL ", style=f"bold {_c('paper')} on {_c('accent')}")
            yield Static(head, markup=False)
            yield Static(id="device", classes="step", markup=False)
            yield Static(Text("2  Which server? Press enter to check it is reachable.", style=_c("muted")), classes="step", markup=False)
            yield Input(value=self.server, placeholder="https://your-room.aletheorecouncil.com", id="server")
            yield Static(id="reach", markup=False)
            yield Static(Text("3  Paste an invitation link, or a registration code from the server's admin.", style=_c("muted")),
                         classes="step", markup=False)
            yield Input(placeholder="invitation link or registration code", id="credential")
            yield Input(placeholder="the name you want in rooms (3 to 32 letters, digits, - or _)", id="handle")
            yield Static(id="result", markup=False)
            yield Static(id="sponsor", classes="step", markup=False)

    def on_mount(self) -> None:
        t = Text()
        t.append("1  This device\n", style=_c("muted"))
        t.append(f"   {self.device_id}\n", style=f"bold {_c('ink')}")
        t.append(f"   safety words  {self.words.replace('-', ' ').upper()}\n", style=f"bold {_c('accent')}")
        t.append("   Read these aloud if someone asks you to compare devices. The private key stays on this machine.", style=_c("faint"))
        self.query_one("#device", Static).update(t)
        if not self.marker.exists():  # shown once
            self.query_one("#sponsor", Static).update(Text(SPONSOR, style=_c("faint")))
            try:
                self.marker.parent.mkdir(parents=True, exist_ok=True)
                self.marker.write_text("shown")
            except OSError:
                pass
        self.query_one("#server", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        which = event.input.id
        if which == "server":
            self._check(event.value.strip())
        elif which == "credential":
            self.query_one("#handle", Input).focus()
        elif which == "handle":
            self._submit()

    def _check(self, server: str) -> None:
        reach = self.query_one("#reach", Static)
        if not server:
            reach.update(Text("   type the server's address first", style=_c("needs")))
            return
        reach.update(Text("   checking…", style=_c("muted")))
        self.server = server

        def run() -> None:
            ok, ms, line = self.actions.check(server)
            self.reachable = ok
            text = Text("   ✓ " if ok else "   ○ ", style=_c("ok") if ok else _c("needs"))
            text.append(f"{line}" + (f" ({ms} ms)" if ok else ""), style=_c("ink") if ok else _c("muted"))
            self.call_from_thread(reach.update, text)
            if ok:
                self.call_from_thread(self.query_one("#credential", Input).focus)

        self.run_worker(run, thread=True, group="check", exclusive=True)

    def _submit(self) -> None:
        server = self.server or self.query_one("#server", Input).value.strip()
        credential = self.query_one("#credential", Input).value.strip()
        handle = self.query_one("#handle", Input).value.strip()
        out = self.query_one("#result", Static)
        if not server or not credential or not handle:
            out.update(Text("   fill in the server, the link or code, and your name first", style=_c("needs")))
            return
        out.update(Text("   sending…", style=_c("muted")))

        def run() -> None:
            try:
                if looks_like_link(credential):
                    message = self.actions.join(credential, handle)
                    self.result = {"server": server, "handle": handle, "pending": True}
                else:
                    message = self.actions.register(server, credential, handle)
                    self.result = {"server": server, "handle": handle, "pending": False}
                self.call_from_thread(out.update, Text("   ✓ " + message, style=_c("ok")))
            except Exception as exc:  # shown in plain words; the screen stays so the person can fix it
                self.call_from_thread(out.update, Text(f"   ○ {exc}", style=_c("needs")))

        self.run_worker(run, thread=True, group="submit", exclusive=True)
