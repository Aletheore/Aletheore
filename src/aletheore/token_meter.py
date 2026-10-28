"""Local, content-free event storage for the token savings meter."""

from __future__ import annotations

import json
import math
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

READ_GUARD_BYTES_PER_TOKEN = 4
MCP_CHARS_PER_TOKEN = 4
_STATE_DIRNAME = "token_meter"
_EVENTS_DIRNAME = "events"
_SCHEMA_VERSION = 1
_MAX_EVENT_FILE_BYTES = 4096


@dataclass(frozen=True)
class MeterEvent:
    source: str
    occurred_at: datetime
    size_bytes: int = 0
    original_chars: int = 0
    delivered_chars: int = 0


@dataclass(frozen=True)
class PeriodTotals:
    read_denials: int = 0
    read_bytes: int = 0
    read_tokens_estimated: int = 0
    mcp_trims: int = 0
    mcp_omitted_chars_measured: int = 0
    mcp_tokens_estimated: int = 0


@dataclass(frozen=True)
class MeterSummary:
    today: PeriodTotals
    this_week: PeriodTotals
    all_time: PeriodTotals
    corrupt_events: int = 0


def _events_dir(repo_path: Path) -> Path | None:
    state_root = Path(repo_path) / ".aletheore"
    if state_root.is_symlink() or not state_root.is_dir():
        return None
    meter_dir = state_root / _STATE_DIRNAME
    if meter_dir.is_symlink():
        return None
    events_dir = meter_dir / _EVENTS_DIRNAME
    return None if events_dir.is_symlink() else events_dir


def _valid_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _make_event(
    source: str,
    *,
    size_bytes: int = 0,
    original_chars: int = 0,
    delivered_chars: int = 0,
    occurred_at: datetime | None = None,
) -> dict[str, Any] | None:
    if source == "read_guard":
        if not _valid_nonnegative_int(size_bytes):
            return None
        facts = {"size_bytes": size_bytes}
    elif source == "mcp_budget":
        if (
            not _valid_nonnegative_int(original_chars)
            or not _valid_nonnegative_int(delivered_chars)
            or delivered_chars >= original_chars
        ):
            return None
        facts = {"original_chars": original_chars, "delivered_chars": delivered_chars}
    else:
        return None
    timestamp = occurred_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.astimezone()
    return {
        "schema": _SCHEMA_VERSION,
        "source": source,
        "occurred_at": timestamp.astimezone(timezone.utc).isoformat(),
        **facts,
    }


def record_read_guard(
    repo_path: Path, size_bytes: int, *, occurred_at: datetime | None = None
) -> bool:
    """Record an actual unchanged full-read denial; never create `.aletheore/` on its own."""
    return _record(
        repo_path,
        _make_event("read_guard", size_bytes=size_bytes, occurred_at=occurred_at),
    )


def record_mcp_trim(
    repo_path: Path,
    original_chars: int,
    delivered_chars: int,
    *,
    occurred_at: datetime | None = None,
) -> bool:
    """Record a real result-size reduction in serialized MCP output characters."""
    return _record(
        repo_path,
        _make_event(
            "mcp_budget",
            original_chars=original_chars,
            delivered_chars=delivered_chars,
            occurred_at=occurred_at,
        ),
    )


def _record(repo_path: Path, event: dict[str, Any] | None) -> bool:
    if event is None:
        return False
    events_dir = _events_dir(repo_path)
    if events_dir is None:
        return False
    event_id = uuid.uuid4().hex
    temporary = events_dir / f".{event_id}.tmp"
    destination = events_dir / f"{event_id}.json"
    try:
        events_dir.parent.mkdir(mode=0o700, exist_ok=True)
        events_dir.mkdir(mode=0o700, exist_ok=True)
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(event, stream, separators=(",", ":"), sort_keys=True)
            # The rename makes complete records visible atomically; avoiding
            # fsync keeps this bookkeeping off the hook's critical path.
            stream.flush()
        os.replace(temporary, destination)
        return True
    except (OSError, ValueError):
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _decode_event(data: Any) -> MeterEvent:
    if not isinstance(data, dict) or data.get("schema") != _SCHEMA_VERSION:
        raise ValueError("unsupported meter event")
    source = data.get("source")
    try:
        occurred_at = datetime.fromisoformat(data["occurred_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("invalid event timestamp") from exc
    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise ValueError("event timestamp must include a timezone")
    if source == "read_guard":
        size_bytes = data.get("size_bytes")
        if not _valid_nonnegative_int(size_bytes):
            raise ValueError("invalid read size")
        return MeterEvent(source, occurred_at, size_bytes=size_bytes)
    if source == "mcp_budget":
        original = data.get("original_chars")
        delivered = data.get("delivered_chars")
        if (
            not _valid_nonnegative_int(original)
            or not _valid_nonnegative_int(delivered)
            or delivered >= original
        ):
            raise ValueError("invalid MCP size facts")
        return MeterEvent(
            source, occurred_at, original_chars=original, delivered_chars=delivered
        )
    raise ValueError("unknown meter event source")


def load_events(repo_path: Path) -> tuple[list[MeterEvent], int]:
    """Return valid events and the count of corrupt event files skipped."""
    state_root = Path(repo_path) / ".aletheore"
    if state_root.is_symlink():
        return [], 1
    if not state_root.is_dir():
        return [], 0
    state_dir = state_root / _STATE_DIRNAME
    if state_dir.is_symlink() or (state_dir.exists() and not state_dir.is_dir()):
        return [], 1
    events_dir = _events_dir(repo_path)
    if events_dir is None:
        return [], 1 if (state_dir / _EVENTS_DIRNAME).is_symlink() else 0
    if not events_dir.exists():
        return [], 0
    events: list[MeterEvent] = []
    corrupt = 0
    if events_dir.exists() and not events_dir.is_dir():
        return [], 1
    try:
        paths = tuple(events_dir.glob("*.json"))
    except OSError:
        return [], 1
    for path in paths:
        try:
            if path.is_symlink() or path.stat().st_size > _MAX_EVENT_FILE_BYTES:
                corrupt += 1
                continue
            events.append(_decode_event(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, UnicodeError, ValueError, TypeError):
            corrupt += 1
    return events, corrupt


def _estimate_tokens(size: int, ratio: int) -> int:
    return math.ceil(size / ratio) if size else 0


def _sum_events(events: list[MeterEvent]) -> PeriodTotals:
    read = [event for event in events if event.source == "read_guard"]
    mcp = [event for event in events if event.source == "mcp_budget"]
    read_bytes = sum(event.size_bytes for event in read)
    omitted = sum(event.original_chars - event.delivered_chars for event in mcp)
    return PeriodTotals(
        read_denials=len(read),
        read_bytes=read_bytes,
        read_tokens_estimated=_estimate_tokens(read_bytes, READ_GUARD_BYTES_PER_TOKEN),
        mcp_trims=len(mcp),
        mcp_omitted_chars_measured=omitted,
        mcp_tokens_estimated=_estimate_tokens(omitted, MCP_CHARS_PER_TOKEN),
    )


def summarize(repo_path: Path, *, now: datetime | None = None) -> MeterSummary:
    events, corrupt = load_events(repo_path)
    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.astimezone()
    local_today = current.date()
    week_start = local_today - timedelta(days=local_today.weekday())
    local_events = [
        (event, event.occurred_at.astimezone(current.tzinfo).date()) for event in events
    ]
    today_events = [
        event for event, event_date in local_events if event_date == local_today
    ]
    week_events = [
        event
        for event, event_date in local_events
        if week_start <= event_date <= local_today
    ]
    return MeterSummary(
        today=_sum_events(today_events),
        this_week=_sum_events(week_events),
        all_time=_sum_events(events),
        corrupt_events=corrupt,
    )
