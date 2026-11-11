"""What the live terminal view shows, as plain data. No terminal code in here, so it can be tested directly.

Everything from an agent or another participant passes through `clean_text`: terminal control sequences are
removed and markdown decoration is flattened, so a message can neither restyle the screen nor shout in bold.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote
from dataclasses import dataclass, field
from datetime import datetime, timezone

from aletheore.council.protocol import HORIZON_SENDER, Event, EventType
from aletheore.council.state import RoomState
from aletheore.council.terminal import sanitize_text

_LINK = re.compile(r"\[([^\]]*)\]\(<?[^)>]*>?\)")
_BLANKS = re.compile(r"\n{3,}")
_FENCE = re.compile(r"^[ \t]*```\w*[ \t]*$", re.MULTILINE)
_MESSAGE_TYPES = {EventType.QUESTION, EventType.CLAIM, EventType.CHALLENGE, EventType.EVIDENCE,
                  EventType.PROPOSAL, EventType.RESULT}
_FAILURES = {"turn_failed", "debate_run_failed", "publish_failed", "apply_blocked"}


def clean_text(text: object) -> str:
    s = sanitize_text(str(text))
    s = _FENCE.sub("", s)
    s = _LINK.sub(r"\1", s)
    s = s.replace("**", "").replace("`", "")
    return _BLANKS.sub("\n\n", s).strip()


def summarize(text: str, max_lines: int) -> tuple[str, int]:
    """The first `max_lines` lines and how many lines were left out."""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text, 0
    return "\n".join(lines[:max_lines]), len(lines) - max_lines


def relative_time(iso: str, now: datetime) -> str:
    try:
        then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return ""
    secs = int((now - then).total_seconds())
    if secs < 45:
        return "now"
    if secs < 3600:
        return f"{max(1, secs // 60)}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


@dataclass
class StreamItem:
    seq: int
    kind: str  # message | verdict | notice
    author: str
    agent: str | None
    label: str
    text: str
    age: str
    thread: str = "room"
    muted: bool = False
    verdict_id: str = ""
    plan_id: str = ""
    attachments: list = field(default_factory=list)
    created_at: str = ""


@dataclass
class AgentRow:
    agent_id: str
    owner: str
    area: str
    status: str  # working | idle
    detail: str
    last: str = ""


@dataclass
class Need:
    kind: str  # approve | choose
    verdict_id: str
    summary: str
    options: list[str] = field(default_factory=list)


def _notice(e: Event, age: str, text: str, muted: bool = True) -> StreamItem:
    return StreamItem(e.seq, "notice", e.sender_participant, e.sender_agent, "", text, age, e.thread_id, muted)


def build_stream(events: list[Event], state: RoomState, now: datetime) -> list[StreamItem]:
    items: list[StreamItem] = []
    for e in events:
        age = relative_time(e.created_at, now)
        b = e.body if isinstance(e.body, dict) else {}
        if e.type in _MESSAGE_TYPES and not (e.type is EventType.PROPOSAL and isinstance(b.get("plan_id"), str)):
            text = clean_text(b.get("text", ""))
            files = [f"[image] {clean_text(a.get('name', 'image'))[:60]} ({max(1, int(a.get('size', 0)) // 1024)} KB)"
                     for a in b.get("attachments", []) if isinstance(a, dict)][:4] if isinstance(b.get("attachments"), list) else []
            if files:
                text = (text + "\n" if text else "") + "\n".join(files)
            label = e.type.value
            if e.type is EventType.QUESTION and b.get("kind") in ("prompt", "chat", "ask", "task", "debate"):
                label = "says" if b.get("kind") == "chat" else "asks"
            elif b.get("kind") in ("answer", "summary"):
                label = b["kind"]
            if text:
                refs = [a for a in b.get("attachments", []) if isinstance(a, dict)][:4] if isinstance(b.get("attachments"), list) else []
                items.append(StreamItem(e.seq, "message", e.sender_participant, e.sender_agent, label, text, age, e.thread_id,
                                        attachments=refs))
        elif e.type is EventType.PROPOSAL and isinstance(b.get("plan_id"), str):
            if e.sender_participant == state.host and b["plan_id"] in state.plans:
                plan = state.plans[b["plan_id"]]
                lines = [clean_text(plan.reply)] if plan.reply else []
                lines += [(f"→ debate {t['a']} vs {t['b']}: {clean_text(t['subject'])}" if t.get("kind") == "debate"
                           else f"→ {t['area']}: {clean_text(t['instruction'])}") for t in plan.tasks]
                items.append(StreamItem(e.seq, "plan", e.sender_participant, e.sender_agent, "plan", "\n".join(lines), age,
                                        e.thread_id, plan_id=plan.plan_id))
        elif e.type is EventType.DECISION:
            items.append(StreamItem(e.seq, "verdict", e.sender_participant, e.sender_agent, "verdict",
                                    clean_text(b.get("decision", "")), age, e.thread_id, verdict_id=str(b.get("verdict_id", ""))))
        elif e.type is EventType.SYSTEM:
            sub = b.get("subtype")
            who = e.sender_participant
            if sub in _FAILURES:
                items.append(_notice(e, age, f"{who} {sub.replace('_', ' ')}: {clean_text(b.get('note') or b.get('reason') or '')[:160]}"))
            elif sub == "area_defined":
                items.append(_notice(e, age, f"{b.get('name')} is {b.get('owner')}'s ({b.get('agent_id')})"))
            elif sub in ("verdict_approved", "verdict_rejected"):
                items.append(_notice(e, age, f"{who} {sub.split('_')[1]} {b.get('verdict_id')}"))
            elif sub == "verdict_applied":
                items.append(_notice(e, age, f"{b.get('verdict_id')} merged", muted=False))
            elif sub == "winner_chosen":
                items.append(_notice(e, age, f"{who} chose {b.get('winner')} for {b.get('verdict_id')}", muted=False))
            elif sub == "branch_integrated":
                ok = b.get("ok") is True
                items.append(_notice(e, age, (f"merged {b.get('area')} into the room's branch" if ok
                                              else f"could not merge {b.get('area')}: {clean_text(b.get('message', ''))[:200]}"), muted=ok))
            elif sub == "room_done":
                items.append(_notice(e, age, f"room finished ({b.get('outcome')}): {clean_text(b.get('summary', ''))[:300]}", muted=False))
            elif sub == "conductor_stopped":
                items.append(_notice(e, age, f"{who} stopped the autonomous run", muted=False))
            elif sub == "ask_limited":
                items.append(_notice(e, age, f"{b.get('asker')} has reached the limit of {b.get('limit')} questions an hour; "
                                             f"this one was not sent to the agents", muted=False))
            elif sub == "ask_done":
                if b.get("ok") is False:
                    items.append(_notice(e, age, f"question not answered: {clean_text(b.get('note', ''))[:200]}", muted=False))
            elif sub == "host_leaving":
                note = clean_text(b.get("note", ""))[:200]
                name = "the host's server" if e.sender_participant == HORIZON_SENDER else who
                items.append(_notice(e, age, f"{name} is leaving" + (f": {note}" if note else ". You can keep reading; messages wait until they are back."), muted=False))
            elif sub == "host_returned":
                name = "the host's server" if e.sender_participant == HORIZON_SENDER else who
                items.append(_notice(e, age, f"{name} is back: waiting messages are being sent", muted=False))
            elif sub == "autonomy_set":
                items.append(_notice(e, age, f"autonomy {'on' if b.get('on') else 'off'}"))
            elif sub == "branch_published":
                items.append(_notice(e, age, f"{who}'s branch is ready to merge"))
        elif e.type is EventType.TASK:
            op = b.get("op")
            if op == "request":
                first = clean_text(b.get("instruction", "")).splitlines()[:1]
                items.append(_notice(e, age, f"{e.sender_participant} asked {b.get('area')}: {first[0][:120] if first else ''}"))
            elif op in ("finished", "failed", "blocked"):
                task = state.tasks.get(str(b.get("task_id")))
                items.append(_notice(e, age, f"{task.area if task else 'task'} {op}", muted=op == "finished"))
    stamps = {e.seq: e.created_at for e in events}
    for it in items:
        it.created_at = stamps.get(it.seq, "")
    return items


def agent_rows(events: list[Event], state: RoomState, now: datetime) -> list[AgentRow]:
    answered = {str(e.body.get("turn_id")) for e in events if isinstance(e.body, dict) and e.body.get("turn_id")
                and e.type is not EventType.TASK}
    decided = {e.thread_id for e in events if e.type is EventType.DECISION}
    pending_turn: dict[str, str] = {}
    for e in events:
        b = e.body if isinstance(e.body, dict) else {}
        if e.type is EventType.TASK and b.get("op") == "debate_turn" and e.sender_participant == state.host:
            if str(b.get("turn_id")) not in answered and e.thread_id not in decided and isinstance(b.get("agent"), str):
                pending_turn[b["agent"]] = e.thread_id
    last: dict[str, str] = {}
    for e in events:
        if e.sender_agent:
            last[e.sender_agent] = e.created_at
    rows = []
    for area in state.areas.values():
        running = next((t for t in state.tasks.values() if t.area == area.name and t.status == "running"), None)
        if running is not None:
            status, detail = "working", f"building {area.name}"
        elif area.agent_id in pending_turn:
            thread = pending_turn[area.agent_id]
            status, detail = "working", ("answering a question" if thread.startswith("q-") else f"debating in {thread}")
        else:
            status, detail = "idle", ""
        rows.append(AgentRow(area.agent_id, area.owner_participant, area.name, status, detail,
                             relative_time(last.get(area.agent_id, ""), now)))
    return rows


def needs_me(state: RoomState, me: str) -> list[Need]:
    needs: list[Need] = []
    if me == state.host:
        for plan in state.plans.values():
            if plan.status == "pending" and plan.tasks:
                who = f"{plan.requested_by} asked " if plan.requested_by else ""
                needs.append(Need("plan", plan.plan_id, f"{who}{len(plan.tasks)} step(s): " + ", ".join(
                    (f"debate {t['a']}/{t['b']}" if t.get("kind") == "debate" else t["area"]) for t in plan.tasks)))
    for v in state.verdicts.values():
        if v.status != "pending":
            continue
        area = state.areas.get(v.affected_area)
        summary = clean_text(v.decision).splitlines()[0][:140] if v.decision.strip() else v.verdict_id
        if me == state.host and not v.winning_branch and v.candidates:
            needs.append(Need("choose", v.verdict_id, summary, sorted(v.candidates)))
        elif v.winning_branch and (me == state.host or (area is not None and area.owner_participant == me)):
            needs.append(Need("approve", v.verdict_id, summary))
    return needs


_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_AT_TOKEN = re.compile(r'''@(?:"([^"]+)"|'([^']+)'|((?:\\ |\S)+))''')


def _unescape(raw: str) -> str:
    """Undo shell-style backslash escapes in a dropped path (macOS and Linux terminals write `my\\ pic.png`). On Windows
    a backslash is the path separator and paths with spaces arrive in quotes, so nothing is unescaped there."""
    if os.name == "nt":
        return raw
    return re.sub(r"\\(.)", r"\1", raw)


def parse_composer(text: str) -> tuple[str, list[Path]]:
    """Split what was typed into plain text and image paths. `@path`, `@"a path"` and `@a\\ path` attach an image
    when the path exists or looks like an image; anything else (like `@bob`) stays in the text."""
    paths: list[Path] = []

    def take(m: re.Match) -> str:
        raw = m.group(1) or m.group(2) or _unescape(m.group(3))
        p = Path(raw).expanduser()
        if p.is_file() or p.suffix.lower() in _IMAGE_EXT:
            paths.append(p)
            return ""
        return m.group(0)

    cleaned = _AT_TOKEN.sub(take, text)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip(), paths


def normalize_pasted_path(pasted: str) -> str | None:
    """When a file is dropped onto the terminal its path is pasted (quoted, escaped or as a file:// link). If that is a
    single existing image file, return the plain path; otherwise None so the paste is treated as ordinary text."""
    s = pasted.strip()
    if not s or "\n" in s:
        return None
    if s.startswith("file://"):
        s = unquote(s[7:])
    elif len(s) > 1 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1]
    else:
        s = _unescape(s)
    p = Path(s).expanduser()
    return str(p) if p.suffix.lower() in _IMAGE_EXT and p.is_file() else None


# ---- rooms across the whole account, presence, connection ---------------------------------------------------------------
@dataclass
class RoomSummary:
    room_id: str
    title: str
    mode: str
    state: str  # live | waiting | closed
    members: list[str]
    needs: int
    last_activity: str
    progress: tuple[int, int] | None = None  # (done, total) tasks, autonomous rooms only
    need_items: list[Need] = field(default_factory=list)
    unread: int = 0  # messages after the last one this person read here (kept locally)


def summarize_room(info, state: RoomState, me: str) -> RoomSummary:
    """One room as the list shows it. `state` is the room's projected log; a room that is not open stays closed, then waiting, then live."""
    items = needs_me(state, me)
    tasks = list(state.tasks.values())
    progress = (sum(1 for t in tasks if t.status == "done"), len(tasks)) if info.mode == "autonomous" and tasks else None
    kind = "closed" if info.status != "open" else ("waiting" if items else "live")
    return RoomSummary(info.room_id, info.title, info.mode, kind, list(info.members), len(items), info.last_activity, progress, items)


def cross_room_needs(summaries: list[RoomSummary]) -> list[tuple[RoomSummary, Need]]:
    """Everything waiting on this person, oldest room activity first, each paired with the room it belongs to."""
    out = [(s, n) for s in summaries for n in s.need_items]
    return sorted(out, key=lambda pair: (pair[0].last_activity or "~", pair[0].room_id))


@dataclass
class Presence:
    person: str
    state: str  # active | away
    last_seen: str


def presence(events: list[Event], now: datetime, window: int = 300) -> list[Presence]:
    """Council only sees what people do in the room. 'active' means they did something in the last `window` seconds."""
    last: dict[str, str] = {}
    for e in events:
        last[e.sender_participant] = e.created_at
    out = []
    for person, stamp in sorted(last.items()):
        try:
            secs = (now - datetime.fromisoformat(stamp.replace("Z", "+00:00"))).total_seconds()
        except (ValueError, AttributeError):
            secs = float("inf")
        out.append(Presence(person, "active" if secs < window else "away", stamp))
    return out


@dataclass
class ConnectionState:
    kind: str  # live | reconnecting | unreachable | away
    since: int  # seconds since the host or server last answered
    next_try_s: int
    queued: int


def connection_state(since: int, next_try_s: int = 0, queued: int = 0) -> ConnectionState:
    """The stages the host-offline design promises: reconnecting under 30 s, unreachable to 5 minutes, then away."""
    kind = "live" if since <= 0 else "reconnecting" if since < 30 else "unreachable" if since < 300 else "away"
    return ConnectionState(kind, max(0, since), next_try_s, queued)
