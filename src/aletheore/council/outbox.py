"""Messages written while the host cannot be reached wait here, in order, and are sent when it returns.

The queue is a file on this machine (readable only by its owner), so it survives a restart. Each entry keeps the idempotency id it
was written with: if a send went through but its answer was lost, sending it again is harmless, because the server returns the event
it already has. Nothing is ever dropped silently: an entry the host refuses on return (the room closed, the person was removed) is
kept in a separate list with the reason, for the screen to show."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable

from .protocol import Event, EventDraft, EventType

_FIELDS = ("room_id", "client_event_id", "thread_id", "sender_participant", "sender_agent", "reply_to", "body")


def _to_json(d: EventDraft) -> dict:
    return {**{f: getattr(d, f) for f in _FIELDS}, "type": d.type.value}


def _from_json(raw: dict) -> EventDraft:
    return EventDraft(raw["room_id"], raw["client_event_id"], raw["thread_id"], EventType(raw["type"]), raw["sender_participant"],
                      raw.get("sender_agent"), raw.get("reply_to"), raw["body"])


class Outbox:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._items: list[dict] = []
        self.rejected: list[dict] = []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._items, self.rejected = list(data.get("queued", [])), list(data.get("rejected", []))
        except (OSError, ValueError, AttributeError):
            pass  # no file, or a damaged one: start empty rather than refuse to run

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"queued": self._items, "rejected": self.rejected}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except OSError:
            pass

    def add(self, draft: EventDraft, queued_at: float) -> None:
        with self._lock:
            if any(i["draft"]["client_event_id"] == draft.client_event_id and i["draft"]["room_id"] == draft.room_id for i in self._items):
                return  # the same message queued twice is one message
            self._items.append({"draft": _to_json(draft), "queued_at": queued_at})
            self._save()

    def pending(self, room_id: str | None = None) -> list[EventDraft]:
        with self._lock:
            return [_from_json(i["draft"]) for i in self._items if room_id is None or i["draft"]["room_id"] == room_id]

    def pop_first(self) -> None:
        with self._lock:
            if self._items:
                self._items.pop(0)
                self._save()

    def reject_first(self, reason: str) -> None:
        with self._lock:
            if self._items:
                item = self._items.pop(0)
                self.rejected.append({**item, "reason": reason})
                self._save()

    def first(self) -> EventDraft | None:
        with self._lock:
            return _from_json(self._items[0]["draft"]) if self._items else None


class OutboxStore:
    """Wraps a store: a send that cannot reach the host is queued instead of lost. Everything else passes straight through."""

    def __init__(self, inner, path: str | Path, clock: Callable[[], float] = time.time) -> None:
        self._inner, self.outbox, self.clock = inner, Outbox(path), clock

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._inner, name)

    def _provisional(self, d: EventDraft) -> Event:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(self.clock()))
        return Event(d.room_id, d.client_event_id, d.thread_id, d.type, d.sender_participant, d.sender_agent, d.reply_to, d.body,
                     event_id=f"queued-{d.client_event_id}", seq=0, created_at=stamp)

    def append(self, draft: EventDraft) -> Event:
        if self.outbox.pending():  # earlier messages are still waiting: this one goes behind them, so order is kept
            self.outbox.add(draft, self.clock())
            self.flush()
            return self._provisional(draft)
        try:
            return self._inner.append(draft)
        except (ConnectionError, TimeoutError):
            self.outbox.add(draft, self.clock())
            return self._provisional(draft)

    def queued(self, room_id: str | None = None) -> list[EventDraft]:
        return self.outbox.pending(room_id)

    def flush(self) -> tuple[int, int]:
        """Send what is waiting, oldest first. Stops at the first send that cannot reach the host. Returns (sent, refused)."""
        sent = refused = 0
        while True:
            draft = self.outbox.first()
            if draft is None:
                return sent, refused
            try:
                self._inner.append(draft)
            except (ConnectionError, TimeoutError):
                return sent, refused
            except (PermissionError, ValueError, KeyError, LookupError) as exc:
                self.outbox.reject_first(str(exc) or type(exc).__name__)  # kept with its reason, and the queue moves on
                refused += 1
                continue
            self.outbox.pop_first()
            sent += 1
