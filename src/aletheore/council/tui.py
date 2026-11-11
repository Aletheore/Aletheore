"""The live room view: a message stream, a sidebar of agents/tasks/verdicts, and a footer that turns into an
action panel when something needs you. Layout ideas follow OpenCode's terminal UI (a flowing stream with a
thin coloured bar per author, a raised sidebar that hides when the terminal is narrow, an inline permission-style
panel instead of a prompt dialog, and a one-line status row); the code is original.

All text from agents and other people is drawn as inert `rich.text.Text` (never parsed as markup).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Button, Input, OptionList, Static, TextArea

from aletheore.council import theme, tui_cards, tui_connection, tui_devices, tui_join, tui_nav, tui_panel, tui_security, tui_rooms, tui_run
from aletheore.council import tui_palette
from aletheore.council.connection import ConnectionMonitor
from aletheore.council.marks import VerifiedMarks
from aletheore.council.attachments import fetch_attachment
from aletheore.council.clipboard import open_in_viewer, read_clipboard_image
from aletheore.council.approval import approve, choose_winner, decide_plan, reject
from aletheore.council.dispatcher import ASK_LIMIT_PER_HOUR, asks_in_window, send_ask, send_debate, send_message, send_task
from aletheore.council.snapshots import load_state_and_seq
from aletheore.council.state import Projector, RoomState, project
from aletheore.council.store import RoomStore
from aletheore.council.terminal import RoomTail
from aletheore.council.tui_shell import layout_mode, rooms_text, status_line, too_small_text, top_bar_text
from aletheore.council.view_model import (
    AgentRow, Need, StreamItem, agent_rows, build_stream, needs_me, normalize_pasted_path, parse_composer, summarize,
)

_DEPTH = theme.DEPTH  # truecolor, 256, 16 or none; see theme.py for what each means
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
MUTED, TEXT, GOOD, WARN, BAD = (theme.rich_color(t, _DEPTH) for t in ("muted", "ink", "ok", "needs", "error"))
COLLAPSED_LINES = 6
TAIL_WINDOW = 1500  # events loaded when a room opens (the end of it); older ones are fetched on request
NARROW = 100


def color_for(name: str) -> str:
    return theme.person_color(name, _DEPTH)


class MessageView(Static, can_focus=True):
    """One message. Long ones are collapsed; enter or click toggles."""

    def __init__(self, item: StreamItem, show_author: bool) -> None:
        super().__init__(markup=False)
        self.item, self.show_author, self.expanded = item, show_author, False
        self.verdict_status, self.highlight = "", ""
        color = color_for(item.author if item.kind not in ("verdict", "plan") else item.kind)
        self.styles.border_left = ("heavy", color)
        self.refresh_edge()
        self.add_class("message")
        self.draw()

    def refresh_edge(self) -> None:
        if self.item.kind in ("plan", "verdict"):
            waiting = tui_cards.status_style(self.item.kind, self.verdict_status or "pending").endswith(WARN)
            self.styles.border_left = ("heavy", WARN if waiting else (GOOD if self.verdict_status != "rejected" else BAD))

    def on_resize(self) -> None:
        if self.item.kind in ("plan", "verdict"):
            self.draw()

    def set_status(self, status: str) -> None:
        if status != self.verdict_status:
            self.verdict_status = status
            self.refresh_edge()
            self.draw()

    def draw(self) -> None:
        it = self.item
        t = Text()
        if it.kind in ("plan", "verdict"):
            ident = it.plan_id if it.kind == "plan" else it.verdict_id
            body, more = (it.text, 0) if self.expanded else summarize(it.text, COLLAPSED_LINES)
            if more:
                body += f"\n… {more} more lines (enter to expand)"
            width = max(20, (self.size.width or 60) - 4)
            self.update(tui_cards.card_text(it.kind, ident, self.verdict_status, it.author, body, width))
            return
        if self.show_author:
            t.append(it.author, style=f"bold {color_for(it.author)}")
            if it.agent:
                t.append(f" · {it.agent}", style=MUTED)
            t.append(f"  {it.label}", style=MUTED)
            t.append(f"  {it.age}\n", style=MUTED)
        body, more = (it.text, 0) if self.expanded else summarize(it.text, COLLAPSED_LINES)
        start = len(t)
        t.append(body, style=TEXT)
        if self.highlight:
            end = len(t)
            part = t[start:end]
            part.highlight_words([self.highlight], style=f"bold underline {theme.rich_color('accent', _DEPTH)}", case_sensitive=False)
            t = t[:start] + part
        if it.attachments:
            t.append("\n  click, then o to open the image" if len(it.attachments) == 1 else "\n  click, then o to open the first image", style=MUTED)
        if more:
            t.append(f"\n… {more} more lines (enter to expand)", style=MUTED)
        elif self.expanded and len(it.text.splitlines()) > COLLAPSED_LINES:
            t.append("\n(enter to collapse)", style=MUTED)
        self.update(t)

    def on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            self.expanded = not self.expanded
            self.draw()
            event.stop()
        elif event.key == "p":
            self.app.toggle_pin(self.item)  # type: ignore[attr-defined]
            event.stop()
        elif event.key == "o" and self.item.attachments:
            self.app.open_attachment(self.item.attachments[0])  # type: ignore[attr-defined]
            event.stop()

    def on_click(self) -> None:
        self.expanded = not self.expanded
        self.draw()


class Notice(Static):
    def __init__(self, item: StreamItem) -> None:
        t = Text()
        t.append("  · ", style=MUTED)
        t.append(item.text, style=MUTED if item.muted else TEXT)
        t.append(f"  {item.age}", style=MUTED)
        super().__init__(t, markup=False)
        self.add_class("notice")


class FoldRow(Static, can_focus=True):
    """Older history, folded away so a long room stays fast. enter or a click opens the next batch."""

    def __init__(self, hidden: int) -> None:
        super().__init__(tui_nav.fold_text(hidden), markup=False)
        self.add_class("fold")

    def set_hidden(self, hidden: int) -> None:
        self.update(tui_nav.fold_text(hidden))

    def on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            self.app.expand_history()  # type: ignore[attr-defined]
            event.stop()

    def on_click(self) -> None:
        self.app.expand_history()  # type: ignore[attr-defined]


class AskLine(ModalScreen):
    """One question with one line to answer (search for, jump to). enter answers, escape cancels."""

    BINDINGS = [Binding("escape", "cancel", "cancel")]
    DEFAULT_CSS = """
    AskLine { align: center middle; background: $paper 70%; }
    #ask { width: 70; max-width: 96%; height: auto; background: $raised; padding: 1 2; border: round $accent; }
    """

    def __init__(self, question: str, placeholder: str = "", initial: str = "") -> None:
        super().__init__()
        self.question, self.placeholder, self.initial = question, placeholder, initial

    def compose(self) -> ComposeResult:
        with Vertical(id="ask"):
            yield Static(Text(self.question, style=MUTED), markup=False)
            yield Input(value=self.initial, placeholder=self.placeholder, id="ask-input")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class RunCard(Static):
    def __init__(self) -> None:
        super().__init__(markup=False)
        self.add_class("runcard")


class DayDivider(Static):
    def __init__(self, label: str, day_seq: int = 0) -> None:
        super().__init__(tui_cards.day_divider(label, 200), markup=False)
        self.add_class("day")
        self.day_seq = day_seq  # the first message of that day, so "jump to a date" can scroll here


class ThreadHeader(Static):
    def __init__(self, thread: str) -> None:
        super().__init__(Text(f"── {thread} ", style=MUTED), markup=False)
        self.add_class("thread")


class Composer(TextArea):
    """The message box: enter sends, ctrl+j (or shift+enter where the terminal reports it) adds a line, and a file
    dropped onto the terminal arrives as pasted text; if that is an image, it becomes an @attachment."""

    class Submitted(Message):
        def __init__(self, composer: "Composer", text: str) -> None:
            super().__init__()
            self.composer, self.text = composer, text

    def attach_clipboard_image(self) -> bool:
        """Put the image on the clipboard (if any) into the box as an @attachment. False when there is none."""
        app = self.app
        path = app.clipboard_image()  # type: ignore[attr-defined]
        if path is None:
            return False
        self.insert((f'@"{path}"' if " " in str(path) else f"@{path}") + " ")
        return True

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "ctrl+v" and self.attach_clipboard_image():
            event.stop()
            event.prevent_default()
            return
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self, self.text))
            return
        if event.key == "question_mark" and not self.text.strip():
            event.stop()
            event.prevent_default()
            self.app.action_help()  # type: ignore[attr-defined]
            return
        if event.key in ("pageup", "pagedown") or (event.key == "end" and not self.text.strip()):
            event.stop()  # with nothing typed, the keys that scroll or jump belong to the stream
            event.prevent_default()
            app = self.app
            if event.key == "end":
                app.action_jump_end()  # type: ignore[attr-defined]
            else:
                stream = app.query_one("#stream")
                (stream.scroll_page_up if event.key == "pageup" else stream.scroll_page_down)(animate=False)
            return
        if event.key in ("ctrl+j", "shift+enter"):
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        await super()._on_key(event)

    def _on_paste(self, event: events.Paste) -> None:
        path = normalize_pasted_path(event.text)
        if path is None:
            return super()._on_paste(event)
        event.stop()
        event.prevent_default()
        self.insert((f'@"{path}"' if " " in path else f"@{path}") + " ")


class RoomPicker(ModalScreen):
    """The room switcher: type to filter (letters match in order, so `bil` finds `Billing refactor`), up and down to move,
    enter opens, escape closes. The right side previews the highlighted room and what waits for you in it."""

    BINDINGS = [Binding("escape", "close", "close")]
    DEFAULT_CSS = """
    RoomPicker { align: center middle; background: $paper 70%; }
    #picker { width: 100; max-width: 96%; height: auto; max-height: 80%; background: $raised; padding: 1 2; border: round $line-strong; }
    #picker-head { height: auto; }
    #picker-body { height: auto; max-height: 20; }
    #room-list { width: 1fr; height: auto; max-height: 20; background: $raised; }
    #room-preview { width: 36; height: auto; padding: 0 0 0 2; border-left: solid $line; }
    """

    def __init__(self, rooms: list, current: str, summaries: dict | None = None) -> None:
        super().__init__()
        self.rooms, self.current, self.summaries, self.needle = rooms, current, summaries or {}, ""
        self.shown: list = list(rooms)

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Static(id="picker-head", markup=False)
            with Horizontal(id="picker-body"):
                yield OptionList(id="room-list")
                yield Static(id="room-preview", markup=False)

    def _label(self, r, hits: list[int]) -> Text:
        s = self.summaries.get(r.room_id)
        t = Text()
        t.append(("● " if r.room_id == self.current else "  "), style=GOOD)
        t.append_text(tui_rooms.highlight(r.title or r.room_id, hits, "bold"))
        t.append(f"  {r.mode}", style=WARN if r.mode == "autonomous" else MUTED)
        if r.status != "open":
            t.append(f"  {r.status}", style=BAD)
        if s is not None and s.needs:
            t.append(f"  {s.needs} waiting", style=f"bold {WARN}")
        elif s is not None and s.unread:
            t.append(f"  {s.unread} new", style=MUTED)
        return t

    def _refill(self) -> None:
        lst = self.query_one("#room-list", OptionList)
        lst.clear_options()
        self.shown = []
        for r in self.rooms:
            hits = tui_rooms.fuzzy(self.needle, r.title or r.room_id)
            if hits is None:
                continue
            self.shown.append(r)
            lst.add_option(self._label(r, hits))
        if self.shown:
            lst.highlighted = next((i for i, r in enumerate(self.shown) if r.room_id == self.current), 0)
        head = Text()
        head.append("rooms  ", style=MUTED)
        head.append(self.needle or "type to filter", style=theme.rich_color("accent", _DEPTH) if self.needle else MUTED)
        head.append("   enter open · esc close" + ("" if self.shown else "   no room matches"), style=MUTED)
        self.query_one("#picker-head", Static).update(head)
        self._preview()

    def _preview(self) -> None:
        lst = self.query_one("#room-list", OptionList)
        idx = lst.highlighted
        box = self.query_one("#room-preview", Static)
        if idx is None or idx >= len(self.shown):
            box.update("")
            return
        r = self.shown[idx]
        s = self.summaries.get(r.room_id)
        t = Text()
        t.append((r.title or r.room_id) + "\n", style="bold")
        t.append(f"{r.mode} · {r.status}\nhost {r.host}\n", style=MUTED)
        t.append("with " + (", ".join(r.members[:6]) or "no one else") + "\n", style=MUTED)
        if s is not None and s.need_items:
            t.append("\nwaiting on you\n", style=f"bold {WARN}")
            for n in s.need_items[:4]:
                t.append(f"  {n.kind} {n.summary[:40]}\n", style=TEXT)
        elif s is not None:
            t.append("\nnothing waiting on you\n", style=MUTED)
        box.update(t)

    def on_mount(self) -> None:
        self._refill()
        self.query_one("#room-list", OptionList).focus()

    def on_key(self, event: events.Key) -> None:
        if event.key == "backspace":
            self.needle = self.needle[:-1]
        elif event.is_printable and event.character:
            self.needle += event.character
        else:
            return
        event.stop()
        self._refill()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self._preview()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.shown[event.option_index].room_id)

    def action_close(self) -> None:
        self.dismiss(None)


RoomSwitcher = RoomPicker


class CouncilApp(App):
    CSS = f"""
    Screen {{ background: $paper; color: $ink; }}
    #top {{ height: 3; background: $raised; padding: 1 2 0 2; border-bottom: solid $line; }}
    #too-small {{ display: none; height: 100%; width: 100%; content-align: center middle; background: $paper; padding: 0 2; }}
    .too-small #top, .too-small #body, .too-small #footer {{ display: none; }}
    .too-small #too-small {{ display: block; }}
    #queued {{ display: none; height: auto; padding: 0 2; }}
    #banner {{ display: none; height: auto; background: $raised; border-left: heavy $needs; padding: 0 2; }}
    .too-small #banner {{ display: none; }}
    #body {{ height: 1fr; }}
    #rooms {{ width: 26; height: 100%; background: $raised; padding: 1 1; border-right: solid $line; }}
    .mode-stream-only #rooms {{ display: none; }}
    #stream {{ width: 1fr; padding: 1 2 0 2; scrollbar-size-vertical: 1; }}
    #strip {{ width: 1; height: 100%; margin: 1 0 0 0; }}
    .mode-no-panel #strip, .mode-stream-only #strip {{ display: none; }}
    #sidebar {{ width: 30; height: 100%; background: $raised; padding: 1 2; display: block; border-left: solid $line; }}
    #sidebar.hidden {{ display: none; }}
    #footer {{ height: auto; }}
    #action {{ height: auto; background: $raised; border-left: heavy $needs; padding: 1 2; display: none; }}
    #action.show {{ display: block; }}
    #action-text {{ height: auto; }}
    #buttons {{ height: 3; margin-top: 1; }}
    #buttons Button {{ margin-right: 2; min-width: 14; }}
    #buttons Button.-primary {{ background: $accent; color: $paper; border-top: tall $accent; border-bottom: tall $accent; }}
    #buttons Button.-default {{ background: $term; color: $ink; border-top: tall $line-strong; border-bottom: tall $line-strong; }}
    #composer {{ margin: 0 2; border: round $line-strong; background: $raised; padding: 0 1; height: auto; min-height: 3; max-height: 10; }}
    #composer:focus {{ border: round $accent; background: $term; }}
    #status {{ height: 1; background: $raised; padding: 0 2; }}
    .message {{ margin-top: 1; padding-left: 2; height: auto; }}
    .message:focus {{ background: $term; }}
    .notice {{ height: auto; }}
    .thread {{ margin-top: 1; height: 1; }}
    .runcard {{ height: auto; border-left: heavy $needs; padding: 0 1; margin-bottom: 1; }}
    .fold {{ height: 1; margin-bottom: 1; }}
    .fold:focus {{ background: $term; }}
    .day {{ margin-top: 1; height: 1; overflow: hidden; }}
    #new-marker {{ display: none; height: 1; background: $raised; padding: 0 2; }}
    """
    BINDINGS = [
        Binding("ctrl+c", "quit", "quit", priority=True),
        Binding("ctrl+r", "rooms", "rooms", priority=True),
        Binding("tab", "toggle_sidebar", "sidebar", priority=True),
        Binding("end", "jump_end", "jump to latest", show=False),
        Binding("escape", "clear_search", "", show=False),
        Binding("ctrl+g", "joins", "join requests", priority=True),
        Binding("ctrl+x", "stop_run", "stop run", priority=True),
        Binding("ctrl+d", "devices", "devices", priority=True),
        Binding("ctrl+s", "security", "security", priority=True),
        Binding("ctrl+f", "search", "search", priority=True),
        Binding("f3", "next_match", "next match", priority=True),
        Binding("shift+f3", "prev_match", "previous match", priority=True),
        Binding("ctrl+t", "jump_date", "jump to a day", priority=True),
        Binding("ctrl+k", "palette", "commands", priority=True),
        Binding("f1", "help", "help", priority=True),
        Binding("a", "approve", "approve", show=False),
        Binding("r", "reject", "reject", show=False),
        Binding("s", "skip", "skip", show=False),
        Binding("1", "pick(0)", "", show=False),
        Binding("2", "pick(1)", "", show=False),
    ]

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables.update(theme.css_variables(_DEPTH))  # $paper, $accent, $needs ... for the CSS above
        return variables

    def __init__(self, store: RoomStore, room_id: str, me: str,
                 on_approved: Callable[[str], object] | None = None, poll_s: float = 1.0, blobs=None,
                 clipboard: Callable[[Path], Path | None] | None = None,
                 opener: Callable[[Path], object] | None = None,
                 apply_factory: Callable[[str], Callable[[str], object]] | None = None, marks=None, resume=None,
                 ask_limit: int = ASK_LIMIT_PER_HOUR) -> None:
        super().__init__()
        self.ask_limit = ask_limit
        self._sent_asks: list[float] = []
        self.apply_factory = apply_factory
        self.info = None
        self._polls = 0
        self._known_needs = None
        self.store, self.room_id, self.me, self.on_approved, self.poll_s = store, room_id, me, on_approved, poll_s
        self.blobs = blobs
        self._clipboard = clipboard or read_clipboard_image
        self._opener = opener or open_in_viewer
        self._clip_dir = Path(tempfile.mkdtemp(prefix="council-clip-"))
        self.tail = RoomTail(store, room_id, window=TAIL_WINDOW)
        self._projector: Projector | None = None  # the room's state, kept up to date one event at a time (never rebuilt from the whole log)
        self._pending: list = []
        self.host = ""
        self.state = RoomState()
        self.rows: list[AgentRow] = []
        self.needs: list[Need] = []
        self.skipped: set[str] = set()
        self.frame = 0
        self.connected = True
        self._widgets: dict[int, Static] = {}
        self.resume = resume
        self.motion = theme.motion_enabled(os.environ)  # COUNCIL_REDUCED_MOTION=1 or NO_COLOR: nothing animates
        self.watcher, self._watching, self._drafts = tui_rooms.RoomWatcher(store, me, resume), False, {}
        self._left_seq, self._left_marked, self._tick_n = 0, False, 0
        self._window, self._start_seq, self._fold, self._items = tui_nav.WINDOW, None, None, []
        self._search, self._matches, self._match_i, self._pins_mem = "", [], -1, {}
        self._pin_rows: tuple[int, list[int]] = (0, [])
        self.joins: list[dict] = []
        self._reconciled_once = False
        self._budget: dict | None = None
        self._run_card, self._stop_asked = None, 0.0
        self.monitor = ConnectionMonitor(retry_s=poll_s)
        self.marks = marks if marks is not None else VerifiedMarks(Path(os.environ.get("COUNCIL_HOME") or Path.home() / ".aletheore-council") / "verified.json")
        self._away_s = 0
        self._announced: set[str] = set()
        self._last_day, self._unseen, self._first_sync_done = "", 0, False
        self._last_author: tuple[str, str] | None = None
        self._acting = False
        self._button_gen = 0
        self._sidebar_forced: bool | None = None

    # -- layout ----------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static(id="too-small", markup=False)
        yield Static(id="top", markup=False)
        yield Static(id="banner", markup=False)
        with Horizontal(id="body"):
            yield Static(id="rooms", markup=False)
            yield VerticalScroll(id="stream")
            yield Static(id="strip", markup=False)
            yield Static(id="sidebar", markup=False)
        yield Static(id="queued", markup=False)
        yield Static(id="new-marker", markup=False)
        with Vertical(id="footer"):
            with Vertical(id="action"):
                yield Static(id="action-text", markup=False)
                yield Horizontal(id="buttons")
            yield Composer(placeholder="Ask the room to do something…   @all or @agent to ask   @image.png to attach   /say to chat", id="composer",
                           soft_wrap=True, highlight_cursor_line=False, show_line_numbers=False, tab_behavior="focus")
            yield Static(id="status", markup=False)

    def on_mount(self) -> None:
        self.title = "council"
        self.set_interval(self.poll_s, self._poll)
        self.set_interval(0.12, self._tick)
        self.set_interval(4.0, self._watch)
        self.set_timer(0.3, self._watch)
        self._poll()
        self._apply_layout(self.size.width, self.size.height)
        composer = self.query_one("#composer", Composer)
        if self.resume is not None:
            self.resume.set_last(self.room_id)
            self._left_seq = self.resume.read_seq(self.room_id)
            composer.load_text(self.resume.draft(self.room_id))  # a message left half-written last time
        composer.focus()

    def on_resize(self, event: events.Resize) -> None:
        self._apply_layout(event.size.width, event.size.height)

    def _apply_layout(self, width: int, height: int) -> None:
        """Panes follow the window: see tui_shell.layout_mode for the rules."""
        mode = layout_mode(width, height)
        screen = self.screen
        for name in ("full", "no-panel", "stream-only", "too-small"):
            screen.set_class(mode == name, f"mode-{name}")
        screen.set_class(mode == "too-small", "too-small")
        if mode == "too-small":
            self.query_one("#too-small", Static).update(too_small_text(width, height))
        self._fit_sidebar(width)
        self._draw_chrome()

    def clipboard_image(self) -> Path | None:
        return self._clipboard(self._clip_dir)

    def open_attachment(self, ref: dict) -> None:
        if self.blobs is None:
            self.notify("this room has no image storage", severity="warning")
            return

        def run() -> None:
            try:
                path = fetch_attachment(self.blobs, self.room_id, ref, self._clip_dir / "viewed")
                self._opener(path)
            except (ValueError, OSError, ConnectionError) as exc:
                self.call_from_thread(self.notify, f"cannot open the image: {exc}", severity="error")

        self.run_worker(run, thread=True, group="open")

    def on_unmount(self) -> None:
        self._remember()
        shutil.rmtree(self._clip_dir, ignore_errors=True)

    def _remember(self) -> None:
        """Write down where we are: the draft, and how far this room has been read."""
        if self.resume is None:
            return
        self.resume.set_last(self.room_id)
        self.resume.save()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if self.resume is not None and event.text_area.id == "composer":
            self.resume.set_draft(self.room_id, event.text_area.text)  # in memory now; written out every couple of seconds and on exit

    def _fit_sidebar(self, width: int) -> None:
        want = self._sidebar_forced if self._sidebar_forced is not None else width >= NARROW
        self.query_one("#sidebar").set_class(not want, "hidden")

    def _watch(self) -> None:
        """Refresh the summaries of every room in the background; one small read per round."""
        if self._watching:
            return
        self._watching = True

        def run() -> None:
            try:
                self.watcher.round(self.room_id, self.state)
                self._fetch_joins()
                self._fetch_budget()
                self._reconcile()
                self.call_from_thread(self._joins_changed)
            except (ConnectionError, OSError, PermissionError, RuntimeError, KeyError):
                pass
            finally:
                self._watching = False

        self.run_worker(run, thread=True, group="watch", exclusive=True)

    def action_devices(self) -> None:
        if len(self.screen_stack) > 1:
            return
        if not hasattr(self.store, "my_devices"):
            self.notify("devices belong to an account on a room server; this room does not use one", severity="warning")
            return
        self.push_screen(tui_devices.DevicesScreen(self.store.my_devices, self._link_device, self._revoke_device))

    def action_security(self) -> None:
        if len(self.screen_stack) == 1:
            self.push_screen(tui_security.SecurityScreen(self._security_data, self._compare_member, self._grant_history))

    def _security_data(self) -> dict:
        from aletheore.council.identity import safety_words

        info = self.info
        sealed = bool(info is not None and info.crypto == "e2e-v1")
        store_for = getattr(self.store, "store_for", None)
        sealed_store = store_for(self.room_id) if (sealed and store_for) else self.store
        facts = tui_panel.security_facts(sealed_store) if sealed else {}
        try:
            devices = self.store.room_devices(self.room_id) if sealed else {}
        except (ConnectionError, OSError, PermissionError, AttributeError):
            devices = {}
        host = self.state.host or self.host
        members = []
        for name in sorted({self.me, host, *(info.members if info is not None else [])} - {None, ""}):
            devs = devices.get(name, [])
            ids = [d.device_id for d in devs]
            members.append({"name": name, "host": name == host, "you": name == self.me, "devices": len(devs), "device_ids": ids,
                            "words": [safety_words(d.fingerprint) for d in devs],
                            "status": self.marks.status(name, ids) if ids else "none"})
        epoch_of = getattr(self.store, "current_epoch", None)
        return {"sealed": sealed, "epoch": epoch_of(self.room_id) if (sealed and epoch_of) else None, "members": members,
                "host_is_me": self.me == host,
                "counters": {"verified": len(self.tail.all), "rejected": facts.get("rejected", 0),
                             "unreadable": facts.get("unreadable", 0), "gaps": facts.get("gaps", 0)}}

    def _compare_member(self, member: dict) -> None:
        for device_id in member.get("device_ids", []):
            self.marks.mark(member["name"], device_id)  # a local note only; nothing is sent
        self.notify(f"marked {member['name']}'s devices as compared (stored on this machine)")

    def _grant_history(self, member: dict) -> None:
        keys_for = getattr(self.store, "keys_for", None)
        keys = keys_for(self.room_id) if keys_for else None
        held = getattr(self.store, "held_epochs", lambda r: [])(self.room_id)
        if keys is None or len(held) < 2:
            self.notify("there is no older history to grant", severity="warning")
            return
        try:
            keys.grant_to(member["name"], epochs=held[:-1])  # recorded in the room's log as history_granted
            self.notify(f"{member['name']} can now read the older history ({len(held) - 1} earlier key(s))")
        except (PermissionError, ValueError, KeyError, ConnectionError, OSError) as exc:
            self.notify(f"could not grant: {exc}", severity="error")

    def _hosted_sealed(self):
        """Rooms this person hosts that are sealed, with their key handlers (only those can be re-keyed from here)."""
        keys_for = getattr(self.store, "keys_for", None)
        if keys_for is None:
            return []
        out = []
        try:
            for r in self.store.list_rooms(self.me):
                if r.host == self.me and r.crypto == "e2e-v1":
                    k = keys_for(r.room_id)
                    if k is not None:
                        out.append(k)
        except (ConnectionError, OSError, PermissionError):
            pass
        return out

    def _link_device(self, req: dict) -> None:
        try:
            self.store.approve_link(req["request_id"], req["words"])
            for keys in self._hosted_sealed():
                keys.grant_to(self.me)  # the new device gets the current key of every sealed room this person hosts
            self.notify("device linked; hosts of sealed rooms give it the key the next time their Council is open")
        except (ConnectionError, OSError, PermissionError, ValueError, KeyError) as exc:
            self.notify(f"could not link: {exc}", severity="error")

    def _revoke_device(self, device: dict) -> None:
        try:
            self.store.revoke_device(device["device_id"])
            for keys in self._hosted_sealed():
                keys.rotate()  # the revoked device is no longer listed, so it is left out of the new key
            self.notify("device revoked; sealed rooms you host moved to a new key")
        except (ConnectionError, OSError, PermissionError, ValueError, KeyError) as exc:
            self.notify(f"could not revoke: {exc}", severity="error")

    def _flush_outbox(self) -> None:
        """The host answered: send what was written while it was away, oldest first, and say if anything was refused."""
        flush = getattr(self.store, "flush", None)
        if flush is None or not getattr(self.store, "queued", None) or not self.store.queued():
            return
        sent, refused = flush()
        if refused:
            reasons = "; ".join(sorted({r["reason"] for r in self.store.outbox.rejected[-refused:]}))
            self.call_from_thread(self.notify, f"{refused} message(s) could not be sent after the host returned: {reasons}", severity="warning")

    def _queued_lines(self) -> None:
        queued = getattr(self.store, "queued", None)
        box = self.query_one("#queued", Static)
        waiting = queued(self.room_id) if queued else []
        self.monitor.queued = len(waiting)
        box.display = bool(waiting)
        if waiting:
            t = Text()
            for d in waiting[-4:]:
                t.append("┄ waiting to send  ", style=theme.rich_color("faint", _DEPTH))
                t.append((str(d.body.get("text", d.body.get("subtype", "")))).strip().replace("\n", " ")[:70] + "\n", style=MUTED)
            box.update(t)

    def _is_host(self) -> bool:
        return bool(self.me) and self.me == (self.state.host or self.host)

    def _fetch_budget(self) -> None:
        getter = getattr(self.store, "transfer_budget", None)
        if getter is None:
            return
        try:
            self._budget = getter()
        except (ConnectionError, OSError, PermissionError, KeyError, AttributeError):
            pass

    def _reconcile(self) -> None:
        """Host of a sealed room: hand the key to members' newly linked devices, and re-key after a revoked one."""
        recon = getattr(self.store, "reconcile_devices", None)
        info = self.info
        if recon is None or not self._is_host() or info is None or info.crypto != "e2e-v1":
            return
        try:
            done = recon(self.room_id)
        except (ConnectionError, OSError, PermissionError, KeyError, ValueError):
            return
        if done.get("rotated"):
            self.call_from_thread(self.notify, f"a device was revoked: the room moved to key {done['rotated']}")
        elif done.get("granted") and self._reconciled_once:
            self.call_from_thread(self.notify, "a new device of " + ", ".join(done["granted"]) + " was given the room key")
        self._reconciled_once = True

    def _fetch_joins(self) -> None:
        lister = getattr(self.store, "list_join_requests", None)
        if lister is None or not self._is_host():
            self.joins = []  # only the host ever sees who is asking
            return
        try:
            self.joins = list(lister(self.room_id))
        except (ConnectionError, OSError, PermissionError, KeyError, AttributeError):
            pass

    def _joins_changed(self) -> None:
        self._draw_chrome()
        fresh = [j for j in self.joins if j.get("request_id") not in self._announced]
        if fresh and len(self.screen_stack) == 1:
            self._announced.add(fresh[0].get("request_id"))
            self._open_join(fresh[0])

    def action_joins(self) -> None:
        if self.joins and len(self.screen_stack) == 1:
            self._open_join(self.joins[0])

    def _open_join(self, req: dict) -> None:
        info = self.info
        sealed = bool(info is not None and info.crypto == "e2e-v1")
        epoch_of = getattr(self.store, "current_epoch", None)
        epoch = epoch_of(self.room_id) if (sealed and epoch_of) else None

        def decided(choice: str | None) -> None:
            if choice is None:
                return
            self._decide_join(req, choice == "approve")

        self.push_screen(tui_join.JoinModal(req, (info.title if info is not None else "") or self.room_id, epoch, sealed), decided)

    def _decide_join(self, req: dict, approve: bool) -> None:
        room = self.room_id

        def run() -> None:
            try:
                if approve:
                    done = self.store.approve_join(room, req["request_id"])
                    if hasattr(self.store, "grant_member"):
                        self.store.grant_member(room, done["handle"])  # a sealed room: hand over the key now, while the host is here
                    msg = f"{done['handle']} is now in the room"
                else:
                    done = self.store.reject_join(room, req["request_id"])
                    msg = f"turned down {done['handle']}; their invitation link stops working"
                self.joins = [j for j in self.joins if j.get("request_id") != req["request_id"]]
                self.call_from_thread(self.notify, msg)
            except (ConnectionError, OSError, PermissionError, KeyError, ValueError) as exc:
                self.call_from_thread(self.notify, f"could not finish: {exc}", severity="error")
            self.call_from_thread(self._draw_chrome)

        self.run_worker(run, thread=True, group="join")

    def on_click(self, event: events.Click) -> None:
        widget = getattr(event, "widget", None)
        if widget is not None and widget.id == "rooms":
            offset = event.get_content_offset(widget)  # the row inside the list, after the pane's padding
            if offset is None:
                return
            pinned = tui_nav.pin_click_target(self._pin_rows, offset.y)
            if pinned is not None:
                self._goto_seq(pinned)
                return
            target = tui_rooms.room_at_row(self.watcher.ordered(), offset.y)
            if target:
                self.switch_room(target)

    def action_rooms(self) -> None:
        def load() -> None:
            try:
                rooms = self.store.list_rooms(self.me)
            except (ConnectionError, OSError, PermissionError) as exc:
                self.call_from_thread(self.notify, f"cannot list rooms: {exc}", severity="error")
                return
            self.call_from_thread(self._show_rooms, rooms)

        self.run_worker(load, thread=True, group="rooms")

    def _show_rooms(self, rooms: list) -> None:
        if not rooms:
            self.notify("you are not in any rooms", severity="warning")
            return
        self.push_screen(RoomPicker(rooms, self.room_id, dict(self.watcher.summaries)), self.switch_room)

    def switch_room(self, room_id: str | None) -> None:
        """Point the whole view at another room: new log, new state, empty stream."""
        if not room_id or room_id == self.room_id:
            self.query_one("#composer", Composer).focus()
            return
        composer = self.query_one("#composer", Composer)
        self._drafts[self.room_id] = composer.text  # what was typed stays with its room
        self._remember()
        self.room_id = room_id
        if self.resume is not None:
            self.resume.set_last(room_id)
            self._left_seq, self._left_marked = self.resume.read_seq(room_id), False
            self._drafts.setdefault(room_id, self.resume.draft(room_id))
        self.tail = RoomTail(self.store, room_id, window=TAIL_WINDOW)
        self._projector, self._pending = None, []
        self.host, self.info, self._polls = "", None, 0
        self.state, self.rows, self.needs = RoomState(), [], []
        self.skipped, self._widgets, self._last_author, self._known_needs = set(), {}, None, None
        self._run_card = None
        self._window, self._start_seq, self._fold, self._items = tui_nav.WINDOW, None, None, []
        self._search, self._matches, self._match_i = "", [], -1
        self._last_day, self._unseen, self._first_sync_done = "", 0, False
        if self.apply_factory is not None:
            self.on_approved = self.apply_factory(room_id)
        self.query_one("#stream", VerticalScroll).remove_children()
        composer.load_text(self._drafts.get(room_id, ""))
        composer.focus()
        self._poll()
        self._watch()

    def action_toggle_sidebar(self) -> None:
        if len(self.screen_stack) > 1:  # inside a modal, tab moves between its controls as usual
            self.screen.focus_next()
            return
        shown = not self.query_one("#sidebar").has_class("hidden")
        self._sidebar_forced = not shown
        self._fit_sidebar(self.size.width)

    # -- data ------------------------------------------------------------------
    def _poll(self) -> None:
        self.run_worker(self._fetch, thread=True, exclusive=True, group="poll")

    def _fetch(self) -> None:
        try:
            if not self.host:
                self.host = self.store.host_of(self.room_id)
            if self._projector is None:  # first look: the snapshot plus the events after it, not the whole log
                state0, seq0 = load_state_and_seq(self.store, self.room_id)
                self._projector = Projector(host=self.host or None, state=state0, last_seq=seq0)
            fresh = self.tail.poll()
            self._pending.extend(fresh)
            self._flush_outbox()
            if self.info is None or self._polls % 10 == 0:
                try:
                    self.info = self.store.room_info(self.room_id)
                except (KeyError, PermissionError, AttributeError):
                    pass
            self._polls += 1
            ok = True
            self.monitor.ok()
        except (ConnectionError, OSError):
            fresh, ok = [], False
            self.monitor.failed("network")
        try:
            self.call_from_thread(self._apply, bool(fresh) or ok != self.connected, ok)
        except RuntimeError:
            pass  # the app closed while this poll was in flight

    def _apply(self, changed: bool, ok: bool) -> None:
        try:
            self._apply_unchecked(changed, ok)
        except NoMatches:
            pass

    def _apply_unchecked(self, changed: bool, ok: bool) -> None:
        self.connected = ok
        if not changed and self._widgets:
            return
        now = datetime.now(timezone.utc)
        if self._projector is not None:
            pending, self._pending = self._pending, []
            for e in pending:
                self._projector.apply(e)  # events at or before the snapshot's seq are ignored by the projector
            self.state = self._projector.state
        self.rows = agent_rows(self.tail.all, self.state, now)
        self.needs = [n for n in needs_me(self.state, self.me) if n.verdict_id not in self.skipped]
        self._alert_if_new_needs()
        self._sync_stream(now)
        self._sync_action()
        self._draw_chrome()

    def _alert_if_new_needs(self) -> None:
        """Ring the bell (and, if this window is not in front, post a desktop notification) when something new needs you.
        What was already waiting when the window opened is not news."""
        count = len(self.needs)
        if self._known_needs is not None and count > self._known_needs:
            self.bell()
            if sys.platform == "darwin" and not getattr(self, "app_focus", True):
                try:
                    subprocess.Popen(["osascript", "-e", f'display notification "{count} waiting on you" with title "council"'],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except OSError:
                    pass
        self._known_needs = count
        self.title = f"council ({count} waiting)" if count else "council"

    def _sync_stream(self, now: datetime) -> None:
        stream = self.query_one("#stream", VerticalScroll)
        at_bottom = stream.scroll_y >= stream.max_scroll_y - 2
        mounted, fresh = False, 0
        items = build_stream(self.tail.all, self.state, now)
        self._items = items
        if self._start_seq is None:
            self._start_seq, hidden = tui_nav.fold_start(items, self._window)
            hidden += self.tail.hidden_before()  # older events that are not even loaded
            if hidden:
                self._fold = FoldRow(hidden)
                stream.mount(self._fold)
        elif self._fold is not None:
            self._fold.set_hidden(sum(1 for i in items if i.seq < self._start_seq) + self.tail.hidden_before())
        for item in items:
            widget = self._widgets.get(item.seq)
            if widget is None and item.seq < (self._start_seq or 0):
                continue  # folded: counted on the fold row, not on screen
            if widget is None:
                if self._left_seq and not self._left_marked and item.seq > self._left_seq:
                    self._left_marked = True
                    count = sum(1 for i in items if i.seq > self._left_seq)
                    stream.mount(DayDivider(f"{count} new since you left"))
                label = tui_cards.day_label(item.created_at, now)
                if label and label != self._last_day:
                    self._last_day = label
                    stream.mount(DayDivider(label, item.seq))
                if item.kind != "notice":
                    if item.thread != "room" and (self._last_author is None or self._last_author[1] != item.thread):
                        stream.mount(ThreadHeader(item.thread))
                    show_author = item.kind in ("verdict", "plan") or self._last_author != (item.author + (item.agent or ""), item.thread)
                    self._last_author = (item.author + (item.agent or ""), item.thread)
                    widget = MessageView(item, show_author)
                    widget.highlight = self._search
                    if self._is_pinned(item.seq):
                        widget.add_class("pinned")
                else:
                    widget = Notice(item)
                self._widgets[item.seq] = widget
                stream.mount(widget)
                mounted = True
                fresh += 1
            if item.kind == "verdict" and isinstance(widget, MessageView):
                v = self.state.verdicts.get(item.verdict_id)
                widget.set_status(v.status if v else "")
            elif item.kind == "plan" and isinstance(widget, MessageView):
                p = self.state.plans.get(item.plan_id)
                widget.set_status(p.status if p else "")
        self._sync_run_card(stream)
        if at_bottom and self.resume is not None and self.tail.all:
            self.resume.set_read(self.room_id, max(e.seq for e in self.tail.all))  # reading moves forward only
        if mounted and at_bottom:
            self._unseen = 0
            self.call_after_refresh(stream.scroll_end, animate=False)
        elif mounted and self._first_sync_done:
            self._unseen += fresh
        self._first_sync_done = True
        self._show_new_marker()

    # -- long rooms: fold, search, jump, pin --------------------------------------------------------------------------
    def expand_history(self, everything: bool = False) -> None:
        """Show the next batch of older history (or all of it): the stream is rebuilt from the log with a bigger window."""
        self.tail.load_older(None if everything else tui_nav.WINDOW)  # fetch earlier events from the server when they are not loaded yet
        self._window = len(build_stream(self.tail.all, self.state, datetime.now(timezone.utc))) + 1 if everything else self._window + tui_nav.WINDOW
        stream = self.query_one("#stream", VerticalScroll)
        stream.remove_children()
        self._widgets, self._last_author, self._last_day = {}, None, ""
        self._start_seq, self._fold, self._run_card = None, None, None
        self._left_marked = True  # the "new since you left" line has been shown once already
        self._sync_stream(datetime.now(timezone.utc))
        stream.scroll_home(animate=False)

    def _is_pinned(self, seq: int) -> bool:
        pins = self.resume.pins(self.room_id) if self.resume is not None else self._pins_mem.get(self.room_id, [])
        return seq in pins

    def toggle_pin(self, item) -> None:
        if self.resume is not None:
            pinned = self.resume.toggle_pin(self.room_id, item.seq)
        else:
            pins = self._pins_mem.setdefault(self.room_id, [])
            pinned = item.seq not in pins
            pins.append(item.seq) if pinned else pins.remove(item.seq)
        widget = self._widgets.get(item.seq)
        if widget is not None:
            widget.set_class(pinned, "pinned")
        self.notify("pinned in the left panel" if pinned else "unpinned")
        self._draw_chrome()

    def _pin_seqs(self) -> list[int]:
        pins = self.resume.pins(self.room_id) if self.resume is not None else self._pins_mem.get(self.room_id, [])
        by_seq = {i.seq: i for i in self._items}
        return [q for q in pins if q in by_seq and by_seq[q].text.strip()]

    def _pin_lines(self) -> list[str]:
        by_seq = {i.seq: i for i in self._items}
        return [by_seq[q].text.strip().splitlines()[0] for q in self._pin_seqs()]

    def _goto_seq(self, seq: int) -> None:
        if seq not in self._widgets:
            self.expand_history(everything=True)
        widget = self._widgets.get(seq)
        if widget is not None:
            self.query_one("#stream", VerticalScroll).scroll_to_widget(widget, animate=False, top=True)
            if widget.can_focus:
                widget.focus()

    def _draw_strip(self) -> None:
        try:
            strip, stream = self.query_one("#strip", Static), self.query_one("#stream", VerticalScroll)
        except NoMatches:
            return
        if not strip.display or not self._items:
            return
        height = max(1, stream.size.height)
        total = max(1, stream.virtual_size.height)
        top = stream.scroll_y / total
        bottom = (stream.scroll_y + stream.size.height) / total
        strip.update(tui_nav.strip_text(tui_nav.strip_rows(self._items, height, top, bottom)))

    def action_palette(self) -> None:
        if len(self.screen_stack) == 1:
            def run(action: str | None) -> None:
                if action:
                    self.call_after_refresh(self.run_action, action)
                elif action == "":
                    self.notify("focus a message first, then press p", severity="information")

            self.push_screen(tui_palette.PaletteScreen(), run)

    def action_help(self) -> None:
        if len(self.screen_stack) == 1:
            self.push_screen(tui_palette.HelpScreen())

    def action_search(self) -> None:
        if len(self.screen_stack) == 1:
            self.push_screen(AskLine("Search this room", "words to find", self._search), self._run_search)

    def _run_search(self, term: str | None) -> None:
        if term is None:
            return
        if term.strip() and term.strip() == self._search and self._matches:
            self.action_next_match()  # enter on the same term again moves on
            return
        self._search = term.strip()
        self._matches = tui_nav.find_matches(self._items, self._search)
        self._match_i = -1
        for widget in self._widgets.values():
            if isinstance(widget, MessageView):
                widget.highlight = self._search
                widget.draw()
        self._step_match(1)

    def action_next_match(self) -> None:
        self._step_match(1)

    def action_prev_match(self) -> None:
        self._step_match(-1)

    def _step_match(self, step: int) -> None:
        if not self._search:
            return
        self._match_i = tui_nav.next_index(self._match_i, len(self._matches), step)
        if self._match_i >= 0:
            seq = self._matches[self._match_i]
            if seq not in self._widgets:
                self.expand_history(everything=True)  # the match is in folded history: open it all
            widget = self._widgets.get(seq)
            if widget is not None:
                self.query_one("#stream", VerticalScroll).scroll_to_widget(widget, animate=False, top=True)
                if widget.can_focus:
                    widget.focus()
        self._draw_chrome()

    def action_clear_search(self) -> None:
        if self._search:
            self._search, self._matches, self._match_i = "", [], -1
            for widget in self._widgets.values():
                if isinstance(widget, MessageView):
                    widget.highlight = ""
                    widget.draw()
            self.query_one("#composer", Composer).focus()
            self._draw_chrome()

    def action_jump_date(self) -> None:
        if len(self.screen_stack) == 1:
            self.push_screen(AskLine("Jump to a day", "today, yesterday, 2026-11-09 or 9 Nov"), self._run_jump)

    def _run_jump(self, text: str | None) -> None:
        if not text:
            return
        day = tui_nav.parse_day(text, datetime.now(timezone.utc))
        if day is None:
            self.notify("try: today, yesterday, 2026-11-09 or 9 Nov", severity="warning")
            return
        seq = tui_nav.first_item_on_or_after(self._items, day)
        if seq is None and self.tail.hidden_before():
            self.tail.load_older(None)  # the day may be older than what is loaded: bring the earlier history in
            self._items = build_stream(self.tail.all, self.state, datetime.now(timezone.utc))
            seq = tui_nav.first_item_on_or_after(self._items, day)
        if seq is None:
            self.notify("nothing in this room on or after that day", severity="warning")
            return
        if seq not in self._widgets:
            self.expand_history(everything=True)
        widget = self._widgets.get(seq)
        if widget is not None:
            self.query_one("#stream", VerticalScroll).scroll_to_widget(widget, animate=False, top=True)

    def _sync_run_card(self, stream) -> None:
        tl = tui_run.run_timeline(self.tail.all, self.state) if (self.info is not None and self.info.mode == "autonomous") else None
        if tl is None:
            return
        if self._run_card is None or not self._run_card.is_attached:
            self._run_card = RunCard()
            stream.mount(self._run_card, before=0)
        self._run_card.styles.border_left = ("heavy", WARN if not tl.finished else MUTED)
        self._run_card.update(tui_run.run_card_text(tl, max(20, (stream.size.width or 60) - 6), self._is_host()))

    def action_stop_run(self) -> None:
        """Host only. The first press asks, a second within five seconds stops the run (and turns autonomy off)."""
        if not self._is_host() or self.info is None or self.info.mode != "autonomous" or self.state.finished:
            self.notify("only the host can stop a run that is still going", severity="warning")
            return
        now = time.monotonic()
        if now - self._stop_asked > 5:
            self._stop_asked = now
            self.notify("press ctrl+x again to stop the run; pending work then needs a person again", severity="warning")
            return
        self._stop_asked = 0.0
        from aletheore.council.conductor import stop_room

        self._act(lambda: stop_room(self.store, self.room_id, self.me), "run stopped")

    def _show_new_marker(self) -> None:
        marker = self.query_one("#new-marker", Static)
        marker.display = self._unseen > 0
        if self._unseen > 0:
            marker.update(tui_cards.new_marker(self._unseen))


    # -- actions ---------------------------------------------------------------
    def _sync_action(self) -> None:
        panel = self.query_one("#action")
        need = self.needs[0] if self.needs else None
        panel.set_class(need is not None, "show")
        composer = self.query_one("#composer", Composer)
        had_focus_gap = not composer.display
        composer.display = need is None
        if need is None:
            if had_focus_gap:
                composer.focus()
            return
        verb = {"choose": "Choose the winner for", "plan": "Approve plan"}.get(need.kind, "Approve")
        t = Text()
        t.append("needs you  ", style=f"bold {WARN}")
        t.append(f"{verb} {need.verdict_id}", style="bold")
        if len(self.needs) > 1:
            t.append(f"  (+{len(self.needs) - 1} more)", style=MUTED)
        t.append(f"\n{need.summary}", style=TEXT)
        self.query_one("#action-text", Static).update(t)
        buttons = self.query_one("#buttons", Horizontal)
        wanted = ([(f"{i + 1}  {name}", f"pick{i}", "primary") for i, name in enumerate(need.options)]
                  if need.kind == "choose" else [("a  Approve", "approve", "primary"), ("r  Reject", "reject", "default")])
        wanted.append(("s  Skip", "skip", "default"))
        if [str(b.label) for b in buttons.query(Button)] != [w[0] for w in wanted]:
            buttons.remove_children()
            self._button_gen += 1
            for label, ident, variant in wanted:
                buttons.mount(Button(label, id=f"{ident}-{self._button_gen}", variant=variant))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        ident = (event.button.id or "").rsplit("-", 1)[0]
        if ident == "approve":
            self.action_approve()
        elif ident == "reject":
            self.action_reject()
        elif ident == "skip":
            self.action_skip()
        elif ident.startswith("pick"):
            self.action_pick(int(ident[4:]))

    def _current(self, *kinds: str) -> Need | None:
        need = self.needs[0] if self.needs else None
        return need if need is not None and need.kind in kinds else None

    def action_approve(self) -> None:
        if (n := self._current("plan")):
            self._act(lambda: decide_plan(self.store, self.room_id, n.verdict_id, self.me, True), f"approved {n.verdict_id}: tasks go out now")
        elif (n := self._current("approve")):
            self._act(lambda: self._do_approve(n), f"approved {n.verdict_id}")

    def action_reject(self) -> None:
        if (n := self._current("plan")):
            self._act(lambda: decide_plan(self.store, self.room_id, n.verdict_id, self.me, False), f"rejected {n.verdict_id}")
        elif (n := self._current("approve")):
            self._act(lambda: reject(self.store, self.room_id, n.verdict_id, self.me), f"rejected {n.verdict_id}")

    def on_composer_submitted(self, event: Composer.Submitted) -> None:
        text = event.text.strip()
        if not text:
            return
        event.composer.clear()
        chat = False
        if text.startswith("/say "):
            text, chat = text[5:].strip(), True
        elif text.startswith("/debate "):
            parts = text.split(None, 3)
            if len(parts) < 4 or parts[1] not in self.state.areas or parts[2] not in self.state.areas or parts[1] == parts[2]:
                self.notify("usage: /debate <area> <other area> <what to settle>  (areas: " + ", ".join(self.state.areas) + ")",
                            severity="warning")
                return
            a, b, subject = parts[1], parts[2], parts[3]
            self._act(lambda: send_debate(self.store, self.room_id, self.me, a, b, subject), "debate requested; the host approves it", single=False)
            return
        elif text == "/rooms":
            self.action_rooms()
            return
        elif text.startswith("/discuss "):
            self._ask("all", text[9:].strip())
            return
        elif text.startswith("/task "):
            parts = text.split(None, 2)
            if len(parts) < 3 or parts[1] not in self.state.areas:
                self.notify("usage: /task <area> <what to do>  (areas: " + ", ".join(self.state.areas) + ")", severity="warning")
                return
            area, instruction = parts[1], parts[2]
            self._act(lambda: send_task(self.store, self.room_id, self.me, area, instruction), "task proposed; the host approves it", single=False)
            return
        elif text.startswith("@"):
            m = re.match(r"^@(\S+)\s+(.+)$", text, re.S)
            if m and self._is_target(m.group(1)):
                self._ask(m.group(1), m.group(2).strip())
                return
        elif text == "/paste":
            if not event.composer.attach_clipboard_image():
                self.notify("no image on the clipboard", severity="warning")
            return
        elif text.startswith("/"):
            self.notify("commands: /say <text> chat · /discuss · /debate · /task · /rooms · /paste", severity="warning")
            return
        text, images = parse_composer(text)
        self._act(lambda: send_message(self.store, self.room_id, self.me, text, chat=chat, images=images, blobs=self.blobs),
                  ("sent" if chat else "sent to the planner") + (f" with {len(images)} image(s)" if images else ""), single=False)

    def _is_target(self, name: str) -> bool:
        areas = self.state.areas.values()
        return name == "all" or any(a.agent_id == name or a.owner_participant == name for a in areas)

    def _ask(self, to: str, question: str) -> None:
        """A read-only question for the room's agents. Your own hourly limit is checked here so you hear about it at once;
        the host's worker enforces the same rule from the log."""
        now = time.time()
        self._sent_asks = [t for t in self._sent_asks if now - t < 3600]
        # the log may not show this session's latest questions yet, so count the ones sent from here too
        if max(asks_in_window(self.tail.all, self.me, now), len(self._sent_asks)) >= self.ask_limit:
            self.notify(f"you have asked {self.ask_limit} questions in the last hour; wait a little before asking more",
                        severity="warning")
            return
        self._sent_asks.append(time.time())
        self._act(lambda: send_ask(self.store, self.room_id, self.me, to, question), f"question sent to @{to}", single=False)

    def action_pick(self, index: int) -> None:
        n = self._current("choose")
        if n and index < len(n.options):
            self._act(lambda: choose_winner(self.store, self.room_id, n.verdict_id, self.me, n.options[index]),
                      f"{n.options[index]} chosen; it still needs approval")

    def action_skip(self) -> None:
        if self.needs:
            self.skipped.add(self.needs[0].verdict_id)
            self.needs = self.needs[1:]
            self._sync_action()
            self._draw_chrome()

    def _do_approve(self, need: Need) -> None:
        approve(self.store, self.room_id, need.verdict_id, self.me)
        if self.on_approved:
            outcome = self.on_approved(need.verdict_id)
            if isinstance(outcome, str):
                self.call_from_thread(self.notify, outcome, severity="error" if outcome.startswith("not applied") else "information")

    def _act(self, fn: Callable[[], None], done: str, single: bool = True) -> None:
        """Run an action off the screen thread. Decisions (approve, reject, choose) are single-flight so a double click cannot
        decide twice; sending messages is not, so quick messages are never dropped (each carries its own unique id)."""
        if single:
            if self._acting:
                return
            self._acting = True

        def run() -> None:
            try:
                fn()
                self.call_from_thread(self.notify, done)
            except (PermissionError, ValueError, ConnectionError) as exc:
                self.call_from_thread(self.notify, str(exc), severity="error")
            finally:
                if single:
                    self._acting = False
                try:
                    self.call_from_thread(self._poll)
                except RuntimeError:
                    pass  # the app closed while this was running

        self.run_worker(run, thread=True, group="act" if single else "send")

    # -- chrome ----------------------------------------------------------------
    def action_jump_end(self) -> None:
        stream = self.query_one("#stream", VerticalScroll)
        stream.scroll_end(animate=False)
        self._unseen = 0
        self._show_new_marker()

    def _tick(self) -> None:
        try:
            stream = self.query_one("#stream", VerticalScroll)
            if self._unseen and stream.scroll_y >= stream.max_scroll_y - 2:
                self._unseen = 0
                self._show_new_marker()
        except NoMatches:
            pass
        if self.motion:
            self.frame = (self.frame + 1) % len(SPINNER)
        self._tick_n += 1
        if self.resume is not None and self._tick_n % 16 == 0:  # about every two seconds: cheap, and nothing is lost on a crash
            self._remember()
        self._draw_chrome()

    def _draw_chrome(self) -> None:
        try:
            self._draw_chrome_unchecked()
        except NoMatches:
            pass  # a timer fired while the screen was still starting or already closing

    def _draw_chrome_unchecked(self) -> None:
        spin = SPINNER[self.frame] if self.motion else "◐"
        info = self.info
        width = max(20, self.size.width - 4)  # the top bar has two columns of padding each side
        top = top_bar_text(
            width, title=(info.title if info is not None else ""), room_id=self.room_id,
            sealed=bool(info is not None and info.crypto == "e2e-v1"), mode=(info.mode if info is not None else "interactive"),
            round_no=len(self.state.plans) if (info is not None and info.mode == "autonomous") else 0,
            closed=bool(info is not None and info.status == "closed"), finished=self.state.finished or "",
            host=self.state.host or self.host, me=self.me, autonomy=bool(self.state.autonomy), connected=self.connected,
            pulse=((self.frame // 6) % 2 == 0) if self.motion else True)
        self.query_one("#top", Static).update(top)
        summaries = self.watcher.ordered()
        if len(summaries) > 1 or (summaries and summaries[0].needs):
            rooms_view = tui_rooms.room_list_text(summaries, self.room_id, 23)
        else:
            rooms_view = rooms_text(
                title=(info.title if info is not None else ""), room_id=self.room_id, status=(info.status if info is not None else "open"),
                mode=(info.mode if info is not None else "interactive"),
                state_line=" · ".join(sorted(set([self.me, *(info.members if info is not None else [])]))), needs=len(self.needs))
        base_rows = rooms_view.plain.count("\n")
        pin_seqs = self._pin_seqs()
        rooms_view.append_text(tui_nav.pins_text(self._pin_lines(), 23))
        self._pin_rows = (base_rows + 3, pin_seqs[:5])  # the blank line, the dashed rule and the heading come first
        self.query_one("#rooms", Static).update(rooms_view)
        self._draw_strip()

        now = datetime.now(timezone.utc)
        pw = max(16, (self.query_one("#sidebar").size.width or 30) - 4)
        seen: dict[str, str] = {}
        for e in self.tail.all:
            seen[e.sender_participant] = e.created_at
        names = sorted({self.me, self.state.host or self.host, *(info.members if info is not None else [])} - {None, ""})
        side = tui_panel.agents_section(self.rows or [], list(self.state.tasks.values()), pw, self.frame if self.motion else None, spin)
        side.append("\n")
        side.append_text(tui_panel.members_section(names, self.state.host or self.host, self.me, seen, now, pw))
        side.append_text(tui_panel.budget_section(self._budget, pw))
        side.append("\n")
        side.append_text(tui_panel.security_section(bool(info is not None and info.crypto == "e2e-v1"),
                                                    tui_panel.security_facts(self.store), len(self.tail.all)))
        self._queued_lines()
        conn = self.monitor.state()
        self._away_s = max(self._away_s, conn.since) if conn.kind != "live" else self._away_s
        banner = tui_connection.banner_text(conn, self.monitor.reason)
        box = self.query_one("#banner", Static)
        box.display = banner is not None and not self.screen.has_class("too-small")
        if banner is not None:
            box.update(banner)
            side.append("\n")
            side.append_text(tui_connection.works_text(conn))
        if self.monitor.recovered:
            self.monitor.recovered = False
            self.query_one("#stream", VerticalScroll).mount(Static(tui_connection.returned_text(self._away_s), markup=False))
            self._away_s = 0
        side.append("\n§ TASKS\n", style=f"bold {MUTED}")
        icons = {"done": ("✓", GOOD), "running": (spin, WARN), "failed": ("✕", BAD), "blocked": ("!", BAD), "assigned": ("○", MUTED)}
        for task in list(self.state.tasks.values())[-4:]:
            icon, col = icons.get(task.status, ("○", MUTED))
            side.append(f"{icon} ", style=col)
            side.append(f"{task.area}  {task.status}\n", style=TEXT if task.status == "running" else MUTED)
        side.append("\n§ VERDICTS\n", style=f"bold {MUTED}")
        for v in list(self.state.verdicts.values())[-3:]:
            col = {"applied": GOOD, "approved": GOOD, "rejected": BAD}.get(v.status, WARN)
            side.append("● ", style=col)
            side.append(f"{v.verdict_id[:24]}\n  {v.status}", style=TEXT)
            if v.status == "pending":
                side.append(f" · {self._owes(v)}", style=MUTED)
            side.append("\n")
        self.query_one("#sidebar", Static).update(side)

        left = Text()
        working = [r for r in self.rows if r.status == "working"]
        if self._search:
            left.append_text(tui_nav.match_note(self._search, self._match_i, len(self._matches)))
        elif self.joins:
            left.append(f"● {len(self.joins)} waiting to join (ctrl+g)", style=f"bold {WARN}")
        elif self.needs:
            left.append(f"● waiting on you ({len(self.needs)})", style=f"bold {WARN}")
        elif working:
            left.append(f"{spin} ", style=WARN)
            left.append(", ".join(f"{r.agent_id} {r.detail}" for r in working[:2]), style=TEXT)
        else:
            left.append("● all quiet", style=MUTED)
        self.query_one("#status", Static).update(status_line(left, len(self.needs), max(20, self.size.width - 4)))

    def _owes(self, v) -> str:
        area = self.state.areas.get(v.affected_area)
        return f"{area.owner_participant if area else self.state.host} to approve"
