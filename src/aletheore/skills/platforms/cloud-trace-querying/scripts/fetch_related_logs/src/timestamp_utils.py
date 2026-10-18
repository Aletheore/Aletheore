"""Shared timestamp utility functions for trace querying scripts."""

from datetime import datetime
from typing import Optional


def parse_iso8601(ts_str: str) -> datetime:
  """Parses an ISO 8601 timestamp string (supporting Z suffix) into a datetime."""
  normalized = ts_str.replace("Z", "+00:00")
  return datetime.fromisoformat(normalized)


def validate_iso8601(ts_str: Optional[str]) -> Optional[str]:
  if not ts_str:
    return None
  try:
    parse_iso8601(ts_str)
    return ts_str
  except ValueError:
    raise ValueError(f"Invalid ISO 8601 timestamp: {ts_str}")
