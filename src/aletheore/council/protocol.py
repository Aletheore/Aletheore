"""Council event protocol: typed events, validation, evidence references.

Identity (participant, agent) is supplied by Council code that authenticated
the caller, never read from the raw payload. Ordering (`seq`) and timestamps
are assigned by the store, never by clients.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum

MAX_BODY_BYTES = 65536
MAX_ENVELOPE_BYTES = 128 * 1024  # a sealed event: the encrypted payload (up to MAX_BODY_BYTES) plus encoding and signature
MAX_THREAD_ID_LEN = 128
MAX_BODY_DEPTH = 8
_SHA_RE = re.compile(r"^[0-9a-f]{40}\Z")
_THREAD_RE = re.compile(r"^[A-Za-z0-9._:-]+\Z")
MAX_AGENT_TEXT = 8000
HORIZON_SENDER = "council-server"  # the sender of the marker a store returns in place of events it has archived
MAX_BUNDLE_BYTES = 20 * 1024 * 1024


class ProtocolError(ValueError):
    pass


class EventType(str, Enum):
    QUESTION = "question"
    CLAIM = "claim"
    CHALLENGE = "challenge"
    EVIDENCE = "evidence"
    PROPOSAL = "proposal"
    TASK = "task"
    RESULT = "result"
    DECISION = "decision"
    APPROVAL_REQUEST = "approval_request"
    SYSTEM = "system"
    ENVELOPE = "envelope"  # an end-to-end encrypted event: the server cannot read its real type, thread, agent or body


@dataclass(frozen=True)
class EventDraft:
    room_id: str
    client_event_id: str
    thread_id: str
    type: EventType
    sender_participant: str
    sender_agent: str | None
    reply_to: str | None
    body: dict


@dataclass(frozen=True)
class Event(EventDraft):
    event_id: str
    seq: int
    created_at: str


def _depth(value: object, level: int = 1) -> int:
    if isinstance(value, dict):
        return max([level] + [_depth(v, level + 1) for v in value.values()])
    if isinstance(value, list):
        return max([level] + [_depth(v, level + 1) for v in value])
    return level


def clip_text(text: str, limit: int = MAX_AGENT_TEXT) -> str:
    """Bound agent-written text before it is stored or passed on to another agent."""
    return text if len(text) <= limit else text[:limit] + " ...[truncated]"


def check_draft(draft: "EventDraft") -> None:
    """What every store checks before it writes: an envelope may be larger than a plain body (it carries an encrypted one)."""
    check_body(draft.body, MAX_ENVELOPE_BYTES if draft.type is EventType.ENVELOPE else MAX_BODY_BYTES)


def check_body(body: object, limit: int = MAX_BODY_BYTES) -> None:
    """Size, shape and depth limits for an event body. Raises ProtocolError."""
    if not isinstance(body, dict):
        raise ProtocolError("body must be an object")
    try:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        too_deep = _depth(body) > MAX_BODY_DEPTH
    except RecursionError as exc:  # nested far past the limit: refuse it, never let the interpreter's own limit escape
        raise ProtocolError(f"body nesting exceeds {MAX_BODY_DEPTH} levels") from exc
    except (TypeError, ValueError) as exc:
        raise ProtocolError("body must be JSON-serializable") from exc
    if len(encoded) > limit:
        raise ProtocolError(f"body exceeds {limit} bytes")
    if too_deep:
        raise ProtocolError(f"body nesting exceeds {MAX_BODY_DEPTH} levels")


def validate_draft(raw: dict, *, room_id: str, sender_participant: str, sender_agent: str | None) -> EventDraft:
    if not isinstance(raw, dict):
        raise ProtocolError("event must be an object")
    try:
        etype = EventType(raw.get("type"))
    except ValueError as exc:
        raise ProtocolError(f"unknown event type: {raw.get('type')!r}") from exc
    client_event_id = raw.get("client_event_id")
    if not isinstance(client_event_id, str) or not client_event_id.strip():
        raise ProtocolError("client_event_id is required")
    thread_id = raw.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id or len(thread_id) > MAX_THREAD_ID_LEN or not _THREAD_RE.match(thread_id):
        raise ProtocolError("thread_id must be 1 to 128 characters of letters, digits, . _ : -")
    reply_to = raw.get("reply_to")
    if reply_to is not None and not isinstance(reply_to, str):
        raise ProtocolError("reply_to must be a string or null")
    body = raw.get("body")
    check_body(body)
    return EventDraft(
        room_id=room_id,
        client_event_id=client_event_id,
        thread_id=thread_id,
        type=etype,
        sender_participant=sender_participant,
        sender_agent=sender_agent,
        reply_to=reply_to,
        body=body,
    )


def evidence_ref(commit_sha: str, path: str, start_line: int, end_line: int, symbol: str | None = None) -> dict:
    if not isinstance(commit_sha, str) or not _SHA_RE.match(commit_sha):
        raise ProtocolError("evidence requires a full 40-character lowercase commit sha")
    if end_line < start_line or start_line < 1:
        raise ProtocolError("evidence line range is invalid")
    return {"commit_sha": commit_sha, "path": path, "start_line": start_line, "end_line": end_line, "symbol": symbol}


HOST_ONLY_SYSTEM = frozenset({"room_created", "area_defined", "autonomy_set", "verdict_applied", "apply_blocked", "winner_chosen",
                              "plan_approved", "plan_rejected", "plan_dispatched", "room_done", "conductor_stopped",
                              "branch_integrated", "ask_started", "ask_done", "ask_limited", "key_rotated", "member_removed", "history_granted"})
HOST_ONLY_TASK_OPS = frozenset({"request", "assign", "debate_turn"})


def is_host_only(etype: EventType, body: dict) -> bool:
    """Events that only the room's host may post: they define the room, give work, issue verdicts or change mode."""
    if etype is EventType.DECISION:
        return True
    if etype is EventType.SYSTEM:
        return body.get("subtype") in HOST_ONLY_SYSTEM
    if etype is EventType.TASK:
        return body.get("op") in HOST_ONLY_TASK_OPS
    return False


def event_to_json(e: Event) -> dict:
    return {
        "room_id": e.room_id, "client_event_id": e.client_event_id, "thread_id": e.thread_id, "type": e.type.value,
        "sender_participant": e.sender_participant, "sender_agent": e.sender_agent, "reply_to": e.reply_to,
        "body": e.body, "event_id": e.event_id, "seq": e.seq, "created_at": e.created_at,
    }


def event_from_json(d: dict) -> Event:
    return Event(
        room_id=d["room_id"], client_event_id=d["client_event_id"], thread_id=d["thread_id"], type=EventType(d["type"]),
        sender_participant=d["sender_participant"], sender_agent=d.get("sender_agent"), reply_to=d.get("reply_to"),
        body=d["body"], event_id=d["event_id"], seq=d["seq"], created_at=d["created_at"],
    )


def horizon_marker(room_id: str, horizon_seq: int) -> Event:
    """What a store returns at the start of a read that begins below its archive horizon: the events up to `horizon_seq` have moved
    to cold storage, so a reader takes the room's latest snapshot instead (and can download the archive if it wants the old text)."""
    return Event(room_id, "horizon", "room", EventType.SYSTEM, HORIZON_SENDER, None, None,
                 {"subtype": "horizon", "horizon_seq": horizon_seq}, event_id=f"horizon-{horizon_seq}", seq=horizon_seq, created_at="")


SERVER_NOTICE_SUBTYPES = frozenset({"horizon", "host_leaving", "host_returned"})


def server_notice(room_id: str, subtype: str, client_event_id: str, **body) -> EventDraft:
    """A note the host's SERVER writes into a room on its own (it is leaving, it is back). The server holds no room keys, so in a sealed room
    this cannot be sealed: it is written unsealed, from the reserved sender below, and readers show it as coming from the server (it can only
    make a notice appear, never change what the room says or who may do what)."""
    return EventDraft(room_id, client_event_id, "room", EventType.SYSTEM, HORIZON_SENDER, None, None, {"subtype": subtype, **body})


def is_server_notice(e: Event) -> bool:
    return (e.type is EventType.SYSTEM and e.sender_participant == HORIZON_SENDER and isinstance(e.body, dict)
            and e.body.get("subtype") in SERVER_NOTICE_SUBTYPES)


def is_horizon(e: Event) -> bool:
    return e.type is EventType.SYSTEM and e.sender_participant == HORIZON_SENDER and isinstance(e.body, dict) and e.body.get("subtype") == "horizon"
