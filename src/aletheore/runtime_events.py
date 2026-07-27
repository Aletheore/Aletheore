"""Parses Sentry-compatible runtime error events into the fields this
codebase's existing failed-endpoint correlation chain
(scan_worker.jobs._attach_recent_commit_for_failure) needs: which file
and line actually failed, and, when available, which HTTP request it
was handling.

Deliberately not a full implementation of Sentry's wire format - just
the well-known subset (exception.values[].stacktrace.frames[],
request.url/method) that Sentry itself, and Sentry-SDK-compatible
tools, already produce. Proves one real, narrow ingestion path well
rather than building a universal adapter for every monitoring platform.
"""
from urllib.parse import urlparse


def _last_frame(exception_values: list[dict]) -> dict | None:
    """The most specific frame available for the most recent exception in
    the chain - Sentry's own convention for "where the interesting code
    is" is the last in_app frame (deepest into the application's own
    code, past any framework/library frames); falls back to the last
    frame at all when nothing is marked in_app."""
    for value in reversed(exception_values):
        frames = value.get("stacktrace", {}).get("frames") or []
        in_app_frames = [f for f in frames if f.get("in_app")]
        if in_app_frames:
            return in_app_frames[-1]
        if frames:
            return frames[-1]
    return None


def parse_sentry_event(payload: dict) -> dict | None:
    """Returns None when there's no usable stack frame to resolve against
    - never a partially-filled result a caller might mistake for real
    data."""
    exception_values = payload.get("exception", {}).get("values") or []
    frame = _last_frame(exception_values)
    if frame is None or not frame.get("filename") or frame.get("lineno") is None:
        return None

    last_exception = exception_values[-1] if exception_values else {}
    request = payload.get("request") or {}
    url = request.get("url", "")

    return {
        "exception_type": last_exception.get("type", "Error"),
        "exception_value": last_exception.get("value", ""),
        "file": frame["filename"],
        "line": frame["lineno"],
        "function": frame.get("function"),
        "method": (request.get("method") or "").upper(),
        "path": urlparse(url).path if url else "",
    }
