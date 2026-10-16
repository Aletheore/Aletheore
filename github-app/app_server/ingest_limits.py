"""Body-size caps for inbound ingestion endpoints that accept a
caller-supplied payload the server would otherwise parse into memory before
any of its own validation runs.

The cap is enforced in middleware rather than inside the handler because by
the time a handler runs, FastAPI has already read and parsed the body - a
check there rejects a payload the process has fully materialized, which is
the cost the cap exists to avoid.

Caps are per-path and deliberately generous relative to real traffic: the
point is to make a hostile body impossible, not to police a legitimate one.
"""
import json

from fastapi import HTTPException, Request
from pydantic import BaseModel, ValidationError

# A Sentry-compatible error event carries a stack trace, so it is legitimately
# large - but parse_sentry_event only reads exception.values[].stacktrace.
# frames[] and request.url/method, so a payload past this size is carrying
# data this endpoint has no use for.
RUNTIME_EVENT_MAX_BODY_BYTES = 256 * 1024

# 256 chunks at the 8,000-char ceiling embeddings_api enforces per text,
# plus JSON overhead. The handler's own per-text and per-list limits are the
# precise ones; this is the coarse guard that runs before the body is read
# at all, so a caller cannot make the server parse megabytes to be told no.
EMBEDDINGS_MAX_BODY_BYTES = 3 * 1024 * 1024

MANAGED_AUDIT_MAX_BODY_BYTES = 26 * 1024 * 1024

# GitHub's own platform cap on a webhook delivery's payload size - a value
# above this is already something GitHub itself would never send, so
# rejecting it early costs us nothing a real delivery could ever need.
# Unlike the three caps above, /webhook's HMAC check (not an auth token)
# runs *after* the body is buffered, so this was the only thing standing
# between an unauthenticated POST and unbounded memory use before this fix.
GITHUB_WEBHOOK_MAX_BODY_BYTES = 25 * 1024 * 1024

# Paddle's own webhook payloads are small, flat JSON (an event name plus one
# resource object) - real traffic never approaches six figures of bytes.
# Generous relative to that, same reasoning as the caps above: /webhooks/paddle
# verifies an HMAC signature, not a token, which also runs after buffering.
PADDLE_WEBHOOK_MAX_BODY_BYTES = 1 * 1024 * 1024

MAX_BODY_BYTES_BY_PATH = {
    "/v1/runtime-events": RUNTIME_EVENT_MAX_BODY_BYTES,
    "/v1/embeddings": EMBEDDINGS_MAX_BODY_BYTES,
    "/v1/managed-audit": MANAGED_AUDIT_MAX_BODY_BYTES,
    "/webhook": GITHUB_WEBHOOK_MAX_BODY_BYTES,
    "/webhooks/paddle": PADDLE_WEBHOOK_MAX_BODY_BYTES,
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


async def parse_validated_body_after_auth(request: Request, model: type[BaseModel]) -> BaseModel:
    """Parses and validates `request`'s JSON body into `model`, meant to be
    called AFTER a route's own authentication check, not taken as a
    `body: Model` parameter the way FastAPI normally binds one.

    A `body: Model` parameter is bound before a handler's first line ever
    runs, same ordering problem as this module's own size-cap docstring
    describes: real audit found 3 endpoints (runtime-events, embeddings,
    managed-audit) whose handlers called their own auth check first, but
    whose signature still bound the body as a parameter - so an
    unauthenticated caller already paid for JSON parsing and Pydantic
    validation up to this module's own size cap, every time, before
    authentication ever ran. Calling this explicitly, after auth, is what
    actually defers that cost to an authenticated caller.
    """
    try:
        raw = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail="invalid JSON body") from exc
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
