"""Anonymous CLI usage telemetry - see migrations/023_cli_telemetry.sql
for the privacy/scope rationale. Public, unauthenticated ingestion (the
CLI has no account/token to authenticate with); internal stats read
requires the same bearer-token gate as metrics.py's queue-stats.

Being unauthenticated makes this the most exposed write path in the
service: every accepted request is an INSERT into a table nothing else
gates. The abuse controls here are therefore the only thing standing
between a stranger and unbounded row growth - a per-IP rate limit, a
body-size cap enforced in middleware (app_server/ingest_limits.py), and
a schema narrow enough that nothing but the two expected fields is
accepted at all.
"""
import hmac
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app_server.config import get_settings
from app_server.db import count_telemetry_events, record_telemetry_event
from app_server.paddle_ip_allowlist import client_ip_from_forwarded_for
from app_server.rate_limit import is_rate_limited

telemetry_router = APIRouter()
logger = logging.getLogger(__name__)

_VALID_EVENT_TYPES = {"scan"}

# The CLI reports one event per scan. A developer scanning constantly, or a
# CI fleet behind one NAT address, stays far under this; a script trying to
# inflate the table does not. Being rate-limited only drops a usage stat -
# the CLI treats reporting as fire-and-forget - so this is a cheap ceiling
# with no user-visible cost when it trips.
TELEMETRY_RATE_LIMIT = 120
TELEMETRY_RATE_LIMIT_WINDOW_SECONDS = 3600


class TelemetryEvent(BaseModel):
    # Rejecting unknown keys keeps the accepted shape exactly the two fields
    # below, so this endpoint can never become an accidental store for
    # whatever a caller decides to attach.
    model_config = ConfigDict(extra="forbid")

    # Bounded even though the value is checked against _VALID_EVENT_TYPES
    # below: without a max_length the check happens only after Pydantic has
    # already materialized whatever string arrived.
    event: str = Field(min_length=1, max_length=32)
    anonymous_id: str = Field(min_length=8, max_length=64)


def _enforce_telemetry_rate_limit(request: Request) -> None:
    from redis import Redis

    settings = get_settings()
    client_ip = client_ip_from_forwarded_for(
        request.headers.get("x-forwarded-for"),
        request.client.host if request.client else "",
    )
    try:
        rate_limited = is_rate_limited(
            Redis.from_url(settings.redis_url),
            f"ratelimit:telemetry:{client_ip}",
            TELEMETRY_RATE_LIMIT,
            TELEMETRY_RATE_LIMIT_WINDOW_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        # Fail open, matching _enforce_auth_rate_limit and the Paddle IP
        # allowlist: a Redis outage should cost abuse protection on a
        # best-effort stats endpoint, not turn into a hard failure.
        logger.warning("telemetry rate limit check failed (%s); allowing request", exc)
        rate_limited = False

    if rate_limited:
        raise HTTPException(
            status_code=429,
            detail="too many requests",
            headers={"Retry-After": str(TELEMETRY_RATE_LIMIT_WINDOW_SECONDS)},
        )


@telemetry_router.post("/v1/telemetry")
async def report_telemetry_event(payload: TelemetryEvent, request: Request):
    if payload.event not in _VALID_EVENT_TYPES:
        raise HTTPException(status_code=400, detail="unknown event type")
    _enforce_telemetry_rate_limit(request)
    await record_telemetry_event(request.app.state.db_pool, payload.event, payload.anonymous_id)
    return {"ok": True}


@telemetry_router.get("/v1/internal/telemetry-stats")
async def telemetry_stats(request: Request, event: str = "scan"):
    settings = get_settings()
    if not settings.internal_metrics_token:
        raise HTTPException(status_code=404, detail="not found")

    auth_header = request.headers.get("Authorization", "")
    if not hmac.compare_digest(auth_header, f"Bearer {settings.internal_metrics_token}"):
        raise HTTPException(status_code=401, detail="missing or invalid token")

    return await count_telemetry_events(request.app.state.db_pool, event)
