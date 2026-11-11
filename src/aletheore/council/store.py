"""RoomStore: the one interface the rest of Council uses to read and write a room's event log.

Implementations assign a gap-free per-room `seq` starting at 1 and are
idempotent on (room_id, client_event_id). SQLite is the first implementation;
a Postgres implementation must pass the same contract tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from aletheore.council.protocol import Event, EventDraft


ROOM_MODES = ("interactive", "autonomous")
CRYPTO_MODES = ("none", "e2e-v1")  # fixed when the room is created: none = readable by the server, e2e-v1 = signed encrypted envelopes
MAX_TITLE = 80


@dataclass
class RoomInfo:
    room_id: str
    host: str
    title: str = ""
    mode: str = "interactive"  # interactive: people take part; autonomous: the agents run it on a goal
    status: str = "open"  # open | closed
    members: list[str] = field(default_factory=list)
    last_seq: int = 0
    last_activity: str = ""
    crypto: str = "none"


def read_all(store: "RoomStore", room_id: str, after_seq: int = 0) -> list[Event]:
    """The whole log after `after_seq`, paged. Never rely on one read_after call returning everything:
    a remote store caps each page."""
    out: list[Event] = []
    while True:
        page = store.read_after(room_id, after_seq, limit=500)
        if not page:
            return out
        out.extend(page)
        after_seq = page[-1].seq


class RoomStore(Protocol):
    def create_room(self, room_id: str, host_participant: str, title: str = "", mode: str = "interactive", crypto: str = "none") -> None: ...

    def room_info(self, room_id: str) -> RoomInfo: ...

    def members_of(self, room_id: str) -> list[str]: ...

    def add_member(self, room_id: str, participant: str) -> None: ...

    def remove_member(self, room_id: str, participant: str) -> None: ...

    def close_room(self, room_id: str) -> None: ...

    def list_rooms(self, participant: str) -> list[RoomInfo]: ...

    def host_of(self, room_id: str) -> str: ...

    def append(self, draft: EventDraft) -> Event: ...

    def read_after(self, room_id: str, after_seq: int, limit: int = 500) -> list[Event]: ...

    def last_seq(self, room_id: str) -> int: ...
