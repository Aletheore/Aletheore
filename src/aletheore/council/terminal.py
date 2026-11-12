"""Live terminal view of a room, and the approval prompt.

Everything that came from an agent or another person is shown as inert text:
control and escape sequences are stripped, and text is rendered through
`rich.text.Text` objects (never parsed as markup), so a message can neither
restyle the screen nor move the cursor, and nothing in this module ever
executes message content.
"""

from __future__ import annotations

import re
import threading
from typing import Callable

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from aletheore.council.approval import approve, can_approve, choose_winner, reject, required_approver
from aletheore.council.capabilities import AgentCapability
from aletheore.council.protocol import Event, EventType
from aletheore.council.state import RoomState, project
from aletheore.council.store import RoomStore

_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?")
_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ESC_OTHER = re.compile(r"\x1b[@-Z\\-_]|\x1b.")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

MESSAGE_TYPES = {EventType.QUESTION, EventType.CLAIM, EventType.CHALLENGE, EventType.EVIDENCE,
                 EventType.PROPOSAL, EventType.RESULT, EventType.DECISION}
MAX_LINE = 300
RECENT_MESSAGES = 15


def sanitize_text(text: str) -> str:
    text = _OSC.sub("", text)
    text = _CSI.sub("", text)
    text = _ESC_OTHER.sub("", text)
    return _CONTROL.sub("", text)


def _t(value: object, limit: int = MAX_LINE) -> Text:
    s = sanitize_text(str(value))
    return Text(s if len(s) <= limit else s[: limit - 1] + "...")


class RoomTail:
    """Follows a room's log from a sequence number. Several tails over one store see identical events.

    With `window`, a long room is opened at its end: only about the last `window` events are loaded (older ones come on request with
    `load_older`), and `all` is trimmed to a bounded size as the room grows, so the screen's memory and work do not grow with the log."""

    def __init__(self, store: RoomStore, room_id: str, after_seq: int = 0, window: int | None = None) -> None:
        self.store, self.room_id, self.after_seq, self.window = store, room_id, after_seq, window
        self.all: list[Event] = []
        self.base_seq = after_seq  # every event after this seq (up to after_seq) is loaded, older ones are not
        self.keep_all = False  # set once older history was loaded on purpose: nothing is trimmed from then on
        self._first = True
        self._lock = threading.Lock()

    def poll(self) -> list[Event]:
        # Polls can overlap (a slow disk or network, or two refresh timers); one at a time, so an event is added once.
        with self._lock:
            if self._first and self.window and self.after_seq == 0:
                last = self.store.last_seq(self.room_id)
                if last > self.window:
                    self.after_seq = self.base_seq = last - self.window
            self._first = False
            new: list[Event] = []
            while True:
                page = self.store.read_after(self.room_id, self.after_seq, limit=500)
                if not page:
                    break
                new.extend(e for e in page if e.seq > self.after_seq)
                self.after_seq = page[-1].seq
            self.all.extend(new)
            if self.window and not self.keep_all and len(self.all) > 2 * self.window:
                self.all = self.all[-self.window:]
                self.base_seq = self.all[0].seq - 1
            return new

    def hidden_before(self) -> int:
        """How many events older than the loaded ones exist (their seqs run 1..base_seq)."""
        return max(0, self.base_seq)

    def load_older(self, count: int | None = None) -> int:
        """Load up to `count` earlier events (all of them when None). Returns how many were added."""
        with self._lock:
            if self.base_seq <= 0:
                return 0
            start = 0 if count is None else max(0, self.base_seq - count)
            older: list[Event] = []
            after = start
            while after < self.base_seq:
                page = self.store.read_after(self.room_id, after, limit=500)
                if not page:
                    break
                older.extend(e for e in page if e.seq <= self.base_seq)
                if page[-1].seq >= self.base_seq:
                    break
                after = page[-1].seq
            self.all = older + self.all
            self.base_seq = start
            self.keep_all = True
            return len(older)


def _table(*cols: str) -> Table:
    t = Table(show_header=True, header_style="bold", expand=True)
    for c in cols:
        t.add_column(c)
    return t


def render_room(state: RoomState, recent: list[Event], capabilities: list[AgentCapability]) -> RenderableType:
    areas = _table("area", "owner", "agent", "paths")
    for a in state.areas.values():
        areas.add_row(_t(a.name), _t(a.owner_participant), _t(a.agent_id), _t(", ".join(a.globs)))

    tasks = _table("task", "area", "status", "branch / note")
    for t in state.tasks.values():
        note = t.branch or ""
        if t.status == "blocked":
            note += f" outside area: {', '.join(t.blocked_files)}" + (" (acknowledged)" if t.acknowledged else "")
        tasks.add_row(_t(t.task_id), _t(t.area), _t(t.status), _t(note))

    lines = []
    for e in [e for e in recent if e.type in MESSAGE_TYPES][-RECENT_MESSAGES:]:
        who = e.sender_participant + (f"/{e.sender_agent}" if e.sender_agent else "")
        line = Text(f"#{e.seq} [{e.type.value}] ")
        line.append(_t(who))
        line.append(": ")
        line.append(_t(e.body.get("text") or e.body.get("decision") or ""))
        lines.append(line)

    verdicts = _table("verdict", "status", "area", "needs approval from", "decision")
    for v in state.verdicts.values():
        verdicts.add_row(_t(v.verdict_id), _t(v.status), _t(v.affected_area),
                         _t(required_approver(state, v.verdict_id)),
                         _t(("[no winner: host must choose] " if not v.winning_branch and v.candidates else "") + v.decision, 160))

    agents = _table("agent", "level")
    for c in capabilities:
        level = {"worker": "worker", "advisor": "advisor, not autonomous", "unavailable": "unavailable"}[c.level]
        agents.add_row(_t(c.agent), _t(level))

    title = f"Council room (host: {sanitize_text(state.host)})" + ("  [autonomy ON]" if state.autonomy else "")
    return Group(
        Panel(areas, title=Text(title)),
        Panel(tasks, title="Tasks"),
        Panel(Group(*lines) if lines else Text("no messages yet"), title="Discussion"),
        Panel(verdicts, title="Verdicts"),
        Panel(agents, title="Agents"),
    )


def run_view(
    store: RoomStore,
    room_id: str,
    me: str,
    poll_interval_s: float = 0.25,
    stop: threading.Event | None = None,
    console: Console | None = None,
    ask: Callable[[str], str] | None = None,
    live: bool = True,
    capabilities: list[AgentCapability] | None = None,
    on_approved: Callable[[str], None] | None = None,
) -> None:
    stop = stop or threading.Event()
    console = console or Console()
    ask = ask or input
    capabilities = capabilities or []
    tail = RoomTail(store, room_id)
    skipped: set[str] = set()
    lv = Live(console=console, auto_refresh=False) if live else None
    if lv:
        lv.start()
    try:
        while not stop.is_set():
            if tail.poll():
                state = project(tail.all)
                view = render_room(state, tail.all, capabilities)
                if lv:
                    lv.update(view, refresh=True)
                else:
                    console.print(view)
                for v in list(state.verdicts.values()):
                    if me == state.host and v.status == "pending" and not v.winning_branch and v.candidates and v.verdict_id not in skipped:
                        if lv:
                            lv.stop()
                        names = sorted(v.candidates)
                        pick = ask(f"Verdict {v.verdict_id}: the planner could not pick a winner. Choose [{'/'.join(names)}] or [s]kip: ").strip()
                        if lv:
                            lv.start()
                        try:
                            if pick in names:
                                choose_winner(store, room_id, v.verdict_id, me, pick)
                            else:
                                skipped.add(v.verdict_id)
                        except (PermissionError, ValueError):
                            skipped.add(v.verdict_id)
                        continue
                    if v.status != "pending" or v.verdict_id in skipped or not can_approve(state, v.verdict_id, me):
                        continue
                    if lv:
                        lv.stop()
                    answer = ask(f"Verdict {v.verdict_id} ({sanitize_text(v.decision)[:200]}): [a]pprove / [r]eject / [s]kip? ").strip().lower()
                    if lv:
                        lv.start()
                    try:
                        if answer.startswith("a"):
                            approve(store, room_id, v.verdict_id, me)
                            if on_approved:
                                on_approved(v.verdict_id)
                        elif answer.startswith("r"):
                            reject(store, room_id, v.verdict_id, me)
                        else:
                            skipped.add(v.verdict_id)
                    except PermissionError:
                        skipped.add(v.verdict_id)
            stop.wait(poll_interval_s)
    finally:
        if lv:
            lv.stop()
