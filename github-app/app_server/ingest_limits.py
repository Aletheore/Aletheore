"""Body-size caps for the two inbound ingestion endpoints.

/v1/telemetry is public and unauthenticated; /v1/runtime-events is
authenticated but accepts a caller-supplied dict. Neither had any bound on
request size, so either could be handed an arbitrarily large body that the
server parses into memory before any of its own validation runs.

The cap is enforced in middleware rather than inside the handler because by
the time a handler runs, FastAPI has already read and parsed the body - a
check there rejects a payload the process has fully materialized, which is
the cost the cap exists to avoid.

Caps are per-path and deliberately generous relative to real traffic: the
point is to make a hostile body impossible, not to police a legitimate one.
"""

# {"event": "scan", "anonymous_id": "<=64 chars"} is ~100 bytes. 2 KiB leaves
# room for a future field without leaving room for an attack.
TELEMETRY_MAX_BODY_BYTES = 2 * 1024

# A Sentry-compatible error event carries a stack trace, so it is legitimately
# much larger than a telemetry ping - but parse_sentry_event only reads
# exception.values[].stacktrace.frames[] and request.url/method, so a payload
# past this size is carrying data this endpoint has no use for.
RUNTIME_EVENT_MAX_BODY_BYTES = 256 * 1024

# 256 chunks at the 8,000-char ceiling embeddings_api enforces per text,
# plus JSON overhead. The handler's own per-text and per-list limits are the
# precise ones; this is the coarse guard that runs before the body is read
# at all, so a caller cannot make the server parse megabytes to be told no.
EMBEDDINGS_MAX_BODY_BYTES = 3 * 1024 * 1024

MAX_BODY_BYTES_BY_PATH = {
    "/v1/telemetry": TELEMETRY_MAX_BODY_BYTES,
    "/v1/runtime-events": RUNTIME_EVENT_MAX_BODY_BYTES,
    "/v1/embeddings": EMBEDDINGS_MAX_BODY_BYTES,
}


class BodyTooLargeError(Exception):
    def __init__(self, limit: int) -> None:
        super().__init__(f"request body exceeds the {limit} byte limit for this endpoint")
        self.limit = limit


class MissingContentLengthError(Exception):
    pass


def check_declared_body_size(path: str, method: str, content_length: str | None) -> None:
    """Raise if this request declares a body too large for `path`.

    A missing Content-Length is rejected rather than waved through: without it
    the cap is unenforceable before reading, and both clients that legitimately
    call these endpoints (the Aletheore CLI via httpx, and Sentry-style
    webhook senders) always send one for a JSON body. Accepting a chunked
    request here would leave exactly the hole this module exists to close.
    """
    if method != "POST":
        return
    limit = MAX_BODY_BYTES_BY_PATH.get(path)
    if limit is None:
        return
    if content_length is None:
        raise MissingContentLengthError
    try:
        declared = int(content_length)
    except ValueError as exc:
        raise MissingContentLengthError from exc
    if declared > limit:
        raise BodyTooLargeError(limit)
