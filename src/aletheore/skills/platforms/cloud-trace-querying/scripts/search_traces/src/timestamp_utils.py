"""Shared timestamp utility functions for trace querying scripts."""

from datetime import datetime, timezone
from typing import Any, Optional
from google.protobuf.timestamp_pb2 import Timestamp


def parse_iso8601(ts_str: str) -> datetime:
  """Parses an ISO 8601 timestamp string (supporting Z suffix) into a datetime."""
  normalized = ts_str.replace("Z", "+00:00")
  return datetime.fromisoformat(normalized)


def parse_timestamp(ts_str: Optional[str]) -> Optional[Timestamp]:
  """Parses ISO8601 string and converts to protobuf Timestamp."""
  if not ts_str:
    return None
  try:
    dt = parse_iso8601(ts_str)
    ts = Timestamp()
    epoch_seconds = dt.timestamp()
    ts.seconds = int(epoch_seconds)
    ts.nanos = int((epoch_seconds - ts.seconds) * 1e9)
    return ts
  except Exception as e:
    raise ValueError(
        f"Invalid timestamp '{ts_str}'. Must be ISO 8601 (e.g."
        f" 2026-06-25T13:00:00Z). Error: {e}"
    )


def to_datetime(val: Any) -> datetime:
  """Converts string, datetime or Timestamp dict to timezone-aware datetime."""
  if isinstance(val, datetime):
    if val.tzinfo is None:
      return val.replace(tzinfo=timezone.utc)
    return val
  if isinstance(val, str):
    return parse_iso8601(val)
  if isinstance(val, dict):
    seconds = val.get("seconds")
    nanos = val.get("nanos", 0)
    if seconds is not None:
      return datetime.fromtimestamp(
          int(seconds) + int(nanos) * 1e-9, tz=timezone.utc
      )
  raise ValueError(f"Unsupported timestamp format: {val}")
