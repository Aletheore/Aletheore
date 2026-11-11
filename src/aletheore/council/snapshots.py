"""Signed-by-the-host snapshots of a room's state, so opening a long room means reading a snapshot and its tail, not the whole log.

A snapshot is an ordinary host event: {"subtype": "snapshot", "covers_seq": S, "hash": ..., "state_z": ...}. `state_z` is the projected
RoomState as JSON, compressed and base64 encoded. A reader trusts a snapshot only if its sender is the room's host, its hash matches the
state it carries, and it covers events that really came before it. Anything else is ignored and the reader replays from the start, so
a bad or missing snapshot can slow a reader down but never change what it sees: `snapshot + tail == full replay`."""

from __future__ import annotations

import base64
import hashlib
import json
import zlib
from dataclasses import asdict

from .protocol import Event, EventDraft, EventType, MAX_BODY_BYTES
from .state import Area, Plan, Projector, RoomState, TaskState, Verdict, project
from .store import read_all

SNAPSHOT_EVERY = 1000  # events between snapshots


def state_to_json(state: RoomState) -> dict:
    return asdict(state)


def state_from_json(raw: dict) -> RoomState:
    return RoomState(
        host=raw["host"], areas={k: Area(**v) for k, v in raw["areas"].items()}, tasks={k: TaskState(**v) for k, v in raw["tasks"].items()},
        verdicts={k: Verdict(**v) for k, v in raw["verdicts"].items()}, autonomy=raw["autonomy"], plans={k: Plan(**v) for k, v in raw["plans"].items()},
        prompts=dict(raw["prompts"]), finished=raw["finished"], finish_note=raw["finish_note"],
        goal=raw.get("goal", ""), goal_thread=raw.get("goal_thread", ""))


def _canonical(raw: dict) -> bytes:
    return json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def state_hash(state: RoomState) -> str:
    return hashlib.sha256(_canonical(state_to_json(state))).hexdigest()


def make_snapshot_body(state: RoomState, covers_seq: int) -> dict | None:
    """The event body for a snapshot, or None when the state is too large to carry (readers then simply replay from the start)."""
    raw = _canonical(state_to_json(state))
    body = {"subtype": "snapshot", "covers_seq": covers_seq, "hash": hashlib.sha256(raw).hexdigest(),
            "state_z": base64.b64encode(zlib.compress(raw, 6)).decode("ascii")}
    return body if len(json.dumps(body)) < MAX_BODY_BYTES - 512 else None


def read_snapshot(e: Event, host: str) -> tuple[int, RoomState] | None:
    """(covers_seq, state) if this event is a snapshot a reader may trust, else None. Never raises."""
    b = e.body
    try:
        if e.type is not EventType.SYSTEM or not isinstance(b, dict) or b.get("subtype") != "snapshot" or e.sender_participant != host:
            return None
        covers = b["covers_seq"]
        if not isinstance(covers, int) or covers < 0 or covers >= e.seq:
            return None  # a snapshot cannot cover events that come after it
        raw = zlib.decompress(base64.b64decode(b["state_z"]), wbits=15, bufsize=1 << 20)
        if len(raw) > 32 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != b["hash"]:
            return None
        state = state_from_json(json.loads(raw))
        if state.host != host:
            return None
        return covers, state
    except Exception:  # noqa: BLE001 - any damage means "not a usable snapshot", never an error
        return None


def _read_range(store, room_id: str, after: int, upto: int) -> list[Event]:
    """Events with after < seq <= upto, paged, stopping as soon as the range is covered."""
    out: list[Event] = []
    while after < upto:
        page = store.read_after(room_id, after, limit=500)
        if not page:
            break
        out.extend(e for e in page if e.seq <= upto)
        if page[-1].seq >= upto:
            break
        after = page[-1].seq
    return out


def latest_snapshot(store, room_id: str, host: str, last_seq: int | None = None) -> tuple[int, RoomState] | None:
    """The newest trustworthy snapshot, found by looking back from the end in chunks that double in size (so a recent snapshot costs a
    small read, and an old or missing one costs at most about one pass over the log)."""
    last = store.last_seq(room_id) if last_seq is None else last_seq
    end, size = last, 200
    while end > 0:
        start = max(0, end - size)
        found = [e for e in _read_range(store, room_id, start, end) if e.type is EventType.SYSTEM and isinstance(e.body, dict)
                 and e.body.get("subtype") == "snapshot"]
        for e in sorted(found, key=lambda x: x.seq, reverse=True):
            got = read_snapshot(e, host)
            if got is not None and got[0] <= last:
                return got
        end, size = start, size * 2
    return None


def load_state_and_seq(store, room_id: str) -> tuple[RoomState, int]:
    """The room's state and the last event seq folded into it: the newest verifying snapshot plus only the events after it, or a full
    replay when there is none. A live reader keeps a Projector going from exactly this seq."""
    host = store.host_of(room_id)
    snap = latest_snapshot(store, room_id, host)
    proj = Projector(host=host, state=snap[1] if snap else None, last_seq=snap[0] if snap else 0)
    proj.apply_all(read_all(store, room_id, proj.last_seq))
    return proj.state, proj.last_seq


def load_state_fast(store, room_id: str) -> RoomState:
    return load_state_and_seq(store, room_id)[0]


def maybe_snapshot(store, room_id: str, host: str, every: int = SNAPSHOT_EVERY) -> int | None:
    """Host only: if `every` events have passed since the last snapshot, write a new one. Returns the seq it covers, or None."""
    last = store.last_seq(room_id)
    prior = latest_snapshot(store, room_id, host, last)
    base = prior[0] if prior else 0
    if last - base < every:
        return None
    # project exactly up to `last` (events that arrive while this runs are simply after the snapshot)
    state = Projector(host=host, state=prior[1] if prior else None, last_seq=base).apply_all(
        e for e in read_all(store, room_id, base) if e.seq <= last).state
    body = make_snapshot_body(state, last)
    if body is None:
        return None
    store.append(EventDraft(room_id, f"snapshot:{last}", "room", EventType.SYSTEM, host, None, None, body))
    return last
