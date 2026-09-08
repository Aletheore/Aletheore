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


def _last_frame_and_source(exception_values: list[dict]) -> tuple[dict, dict] | None:
    """The most specific frame available for the most recent exception in
    the chain that actually HAS one, paired with the exception it came
    from - Sentry's own convention for "where the interesting code is" is
    the last in_app frame (deepest into the application's own code, past
    any framework/library frames); falls back to the last frame at all
    when nothing is marked in_app.

    Real bug found via audit: this used to return only the frame, while
    parse_sentry_event separately took exception_values[-1] (the last
    exception in the chain, unconditionally) for the exception_type/
    exception_value fields. When the outermost exception in a chain has
    no stacktrace frames of its own (a real, plausible shape - a wrapped/
    re-raised exception with no captured traceback) but an earlier cause
    does, this walk would fall through to that earlier exception's frame
    while the caller still paired it with the OUTER exception's message -
    attributing one exception's message to a different exception's
    file:line. Returning the (frame, exception) pair together means the
    exception fields reported always describe the SAME exception the
    frame actually came from.
    """
    for value in reversed(exception_values):
        frames = value.get("stacktrace", {}).get("frames") or []
        in_app_frames = [f for f in frames if f.get("in_app")]
        if in_app_frames:
            return in_app_frames[-1], value
        if frames:
            return frames[-1], value
    return None


def parse_sentry_event(payload: dict) -> dict | None:
    """Returns None when there's no usable stack frame to resolve against
    - never a partially-filled result a caller might mistake for real
    data."""
    exception_values = payload.get("exception", {}).get("values") or []
    result = _last_frame_and_source(exception_values)
    if result is None:
        return None
    frame, source_exception = result
    if not frame.get("filename") or frame.get("lineno") is None:
        return None

    request = payload.get("request") or {}
    url = request.get("url", "")

    return {
        "exception_type": source_exception.get("type", "Error"),
        "exception_value": source_exception.get("value", ""),
        "file": frame["filename"],
        "line": frame["lineno"],
        "function": frame.get("function"),
        "method": (request.get("method") or "").upper(),
        "path": urlparse(url).path if url else "",
    }
