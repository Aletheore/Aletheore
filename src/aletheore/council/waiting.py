"""Block until something addressed to this participant (or one of its agents) arrives in a room.

This is how an interactive agent, a Claude Code or Codex session in a terminal, stays in a room without a person typing to it
every turn: it runs `council wait` as a command that returns when there is something to do, answers, and runs it again. While
it is waiting it uses no tokens. A background command that finishes is what wakes such a session.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Callable

from aletheore.council.protocol import Event, EventType
from aletheore.council.state import load_state
from aletheore.council.store import RoomStore, read_all
from aletheore.council.wake import mentions

_CHAT = ("chat", "ask", "prompt")
_RESULT = ("finished", "failed", "blocked")
_PLAN_TASK = re.compile(r"plan-(.+?)-\d+(?:-re\d+)*$")      # plan-<thread>-<position>, and -reN once `council handover` has given the task to another area


class Cursor:
    """Where this reader stopped: the last event seq it has been given or has passed over. A damaged or missing file means
    'not known', never an exception and never a replay of the whole room."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def get(self) -> int | None:
        try:
            seq = json.loads(self.path.read_text()).get("seq")
        except (OSError, ValueError, AttributeError):
            return None
        return seq if isinstance(seq, int) and not isinstance(seq, bool) and seq >= 0 else None

    def joins(self) -> set[str]:
        """Join requests already reported to this reader."""
        try:
            ids = json.loads(self.path.read_text()).get("joins", [])
        except (OSError, ValueError, AttributeError):
            return set()
        return {i for i in ids if isinstance(i, str)} if isinstance(ids, list) else set()

    def set(self, seq: int | None = None, joins: set[str] | None = None) -> None:
        """Save the position and/or the reported join requests; whichever is left out keeps its saved value."""
        seq = self.get() if seq is None else seq
        keep = self.joins() if joins is None else joins
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"seq": seq, "joins": sorted(keep)[-200:]}))   # no known position stays null: 0 would mean "replay the room"
        os.replace(tmp, self.path)


def addressed(e: Event, me: str, names: set[str], my_areas: set[str], include_all: bool = False, *, host: str = "", autonomy: bool = False,
              prompts: dict[str, str] | None = None) -> bool:
    """Is this event for me: a mention of my handle or one of my agents, a task for one of my areas, a debate turn for me, or, for the
    host, the result of work it set going (a task that finished, failed or was blocked) and a plan waiting for its approval. A verdict is
    for everyone but whoever wrote it."""
    if e.type is EventType.SYSTEM and e.body.get("subtype") == "task_stalled":
        return me == host  # the host's own worker wrote it, so the sender check below would hide it
    if e.type is EventType.PROPOSAL and (prompts or {}).get(e.thread_id) == me:
        return True  # the plan answers what this person typed (it may be written by the host's own planner, hence before the sender check)
    if e.type is EventType.PROPOSAL and me == host and e.body.get("tasks") and isinstance(e.body.get("plan_id"), str):
        # the host's own planner wrote it, so the sender check below would hide it; in an autonomous room nobody has to approve
        return include_all or not autonomy
    if not include_all:
        # What a worker on this machine says about the work is for whoever waits as this participant, even though the worker posts as them:
        # on a host that owns an agent, the agent's results and the worker's notices all carry the host's own name.
        sub = e.body.get("subtype") if e.type is EventType.SYSTEM else None
        if sub == "worker_error":
            return me == host
        if sub in ("wake_skipped", "wake_failed"):
            return e.body.get("to") == me
        if e.type is EventType.TASK and e.body.get("op") in _RESULT and e.sender_agent is not None:
            return me == host or (prompts or {}).get(_prompt_of(str(e.body.get("task_id", "")))) == me
    if e.sender_participant == me:
        return False
    if include_all:
        return True
    if e.type is EventType.QUESTION and e.body.get("kind") in _CHAT and isinstance(e.body.get("text"), str):
        return bool(mentions(e.body["text"], names))
    if e.type is EventType.DECISION:
        return True
    if e.type is EventType.SYSTEM and e.body.get("subtype") in ("wake_skipped", "wake_failed"):
        return e.body.get("to") == me  # the person whose mention was not answered
    if e.type is EventType.SYSTEM and e.body.get("subtype") == "worker_error":
        return me == host  # a worker that hit an error says so in the room; the host is the one who can act on it
    if e.type is EventType.TASK:
        op = e.body.get("op")
        if op == "request":
            return e.body.get("area") in my_areas
        if op == "debate_turn":
            return e.body.get("participant") == me
        if op == "cancelled":
            return e.body.get("area") in my_areas  # the owner learns their task was taken away
        if op in _RESULT:
            return me == host or (prompts or {}).get(_prompt_of(str(e.body.get("task_id", "")))) == me
    return False


def _prompt_of(task_id: str) -> str:
    """Tasks made from a plan are named plan-<prompt thread>-<n>: the thread of the prompt they came from ('' for any other task)."""
    m = _PLAN_TASK.match(task_id)
    return m.group(1) if m else ""


def _join_events(pending: list[dict], seen: set[str], seq: int, room_id: str) -> list[Event]:
    """People waiting to join are held by the server, not in the room log: show each, once, as an event in the shape the CLI prints."""
    out = []
    for p in pending:
        rid = p.get("request_id") if isinstance(p, dict) else None
        if not isinstance(rid, str) or rid in seen:
            continue
        seen.add(rid)
        words = str(p.get("words", ""))
        handle = str(p.get("handle", "?"))
        out.append(Event(room_id=room_id, client_event_id=f"join:{rid}", thread_id="room", type=EventType.SYSTEM, sender_participant=handle,
                         sender_agent=None, reply_to=None, event_id=f"join:{rid}", seq=seq, created_at="",
                         body={"subtype": "join_request_pending", "request_id": rid, "handle": handle, "words": words,
                               "text": f"{handle} is waiting to join (check the safety words {words!r} with them, then approve-join {rid})"}))
    return out


def wait_for(
    store: RoomStore, room_id: str, me: str, *, since: int | None = None, cursor: Cursor | None = None, timeout_s: float = 300.0,
    poll_s: float = 1.0, include_all: bool = False, agents: set[str] | None = None, peek: bool = False,
    joins: Callable[[], list[dict]] | None = None,
    clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
) -> list[Event]:
    """Events addressed to `me` after the starting point, or [] when `timeout_s` passes first.

    The starting point is `since` if given, else the cursor file, else the end of the log (a first wait never replays history).
    The cursor moves past everything scanned, so nothing comes back twice; `peek` leaves it where it was.

    `joins` lists the people waiting to join (the server holds them, they are not in the log). Only the host is told about them, each once;
    a server that cannot answer just means none this time."""
    if since is not None:
        pos = max(0, since)
    else:
        known = cursor.get() if cursor is not None else None
        if known is None:
            log = read_all(store, room_id, 0)
            known = log[-1].seq if log else 0
            if cursor is not None and not peek:
                cursor.set(known)  # remember where the log ended now, or a message sent between two waits would be skipped
        pos = known
    seen_joins = cursor.joins() if cursor is not None else set()
    deadline = clock() + timeout_s
    while True:
        fresh = read_all(store, room_id, pos)
        hits: list[Event] = []
        if fresh:
            pos = fresh[-1].seq
            state = load_state(store, room_id)
            mine = {a.name for a in state.areas.values() if a.owner_participant == me}
            names = {me} | {a.agent_id for a in state.areas.values() if a.owner_participant == me} | set(agents or ())
            hits = [e for e in fresh if addressed(e, me, names, mine, include_all, host=state.host, autonomy=state.autonomy, prompts=state.prompts)]
        reported: set[str] = set()
        if joins is not None and _is_host(store, room_id, me):
            try:
                pending = joins()
            except (ConnectionError, OSError):
                pending = []
            before = set(seen_joins)
            hits += _join_events(pending, seen_joins, pos, room_id)
            reported = seen_joins - before
        if cursor is not None and not peek:
            if fresh:
                cursor.set(pos)
            if reported:
                cursor.set(joins=seen_joins)
        elif peek:
            seen_joins -= reported  # a peek leaves what it showed to be reported again
        if hits:
            return hits
        if clock() >= deadline:
            return []
        sleep(poll_s)


def _is_host(store: RoomStore, room_id: str, me: str) -> bool:
    try:
        return store.host_of(room_id) == me
    except (KeyError, ConnectionError, OSError):
        return False
