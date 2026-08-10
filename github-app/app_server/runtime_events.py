"""Inbound webhook for Phase 3 - runtime-to-code evidence: lets a
customer's monitoring tool (Sentry, or anything producing
Sentry-compatible error events) report a real production failure. The
event gets resolved through the same failed-endpoint correlation chain
already proven for HTTP health checks (see
scan_worker.jobs.run_runtime_event_job) - one "zero-hop debugging" flow,
proven for a second real trigger rather than reimplemented.

Deliberately not an attempt to replace Sentry/every monitoring platform:
this ingests the one well-known event shape (exception.values[].
stacktrace.frames[], request.url/method - see aletheore.runtime_events)
and does one thing with it well.
"""
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from aletheore.runtime_events import parse_sentry_event
from app_server.config import get_settings
from app_server.db import touch_api_token
from app_server.managed_audit_api import _authenticate_token, _get_queue
from app_server.rate_limit import is_rate_limited

runtime_events_router = APIRouter()
logger = logging.getLogger(__name__)

# Every accepted event enqueues a job onto "scans" - the same queue that runs
# Flash reviews, AIRview builds, and managed audits. So an uncapped caller
# here doesn't just cost storage, it delays billed LLM work for that
# installation and every other one sharing the worker. The limit is keyed by
# installation rather than IP because that is the unit that owns the queue
# pressure, and it is the only identity an authenticated caller can't change.
#
# A production incident is bursty by nature, so this is set well above a
# realistic error spike: it exists to stop a runaway or a leaked token, not to
# shape normal traffic.
RUNTIME_EVENT_RATE_LIMIT = 300
RUNTIME_EVENT_RATE_LIMIT_WINDOW_SECONDS = 3600

# owner + "/" + repo, both capped by GitHub well below this.
_MAX_REPO_FULL_NAME = 255


class RuntimeEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_full_name: str = Field(min_length=1, max_length=_MAX_REPO_FULL_NAME)
    # Deliberately still an open dict: this ingests whatever a
    # Sentry-compatible sender emits and parse_sentry_event picks out the few
    # fields it needs. The bound on it is the body-size cap in
    # app_server/ingest_limits.py, not a field schema that would reject real
    # senders for carrying their own extra keys.
    event: dict


def _enforce_runtime_event_rate_limit(installation_id: int) -> None:
    from redis import Redis

    settings = get_settings()
    try:
        rate_limited = is_rate_limited(
            Redis.from_url(settings.redis_url),
            f"ratelimit:runtime-events:{installation_id}",
            RUNTIME_EVENT_RATE_LIMIT,
            RUNTIME_EVENT_RATE_LIMIT_WINDOW_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        # Fail open, matching every other rate limit in this service. Note the
        # blast radius differs here: a Redis outage that lets this through
        # also means the queue this protects is itself unavailable, so the
        # enqueue below would fail regardless.
        logger.warning("runtime event rate limit check failed (%s); allowing request", exc)
        rate_limited = False

    if rate_limited:
        raise HTTPException(
            status_code=429,
            detail="too many runtime events for this installation",
            headers={"Retry-After": str(RUNTIME_EVENT_RATE_LIMIT_WINDOW_SECONDS)},
        )


@runtime_events_router.post("/v1/runtime-events")
async def report_runtime_event(request: Request, body: RuntimeEventRequest):
    installation, token_hash = await _authenticate_token(request)
    if installation["plan"] == "free":
        raise HTTPException(status_code=402, detail="runtime event correlation requires a paid plan")

    _enforce_runtime_event_rate_limit(installation["installation_id"])

    parsed = parse_sentry_event(body.event)
    if parsed is None:
        raise HTTPException(status_code=400, detail="event has no usable stack frame to resolve")

    await touch_api_token(request.app.state.db_pool, token_hash)
    job = _get_queue(get_settings().redis_url).enqueue(
        "scan_worker.jobs.run_runtime_event_job",
        job_timeout=120,
        installation_id=installation["installation_id"],
        repo_full_name=body.repo_full_name,
        exception_type=parsed["exception_type"],
        exception_value=parsed["exception_value"],
        source_file=parsed["file"],
        source_line=parsed["line"],
        method=parsed["method"],
        path=parsed["path"],
    )
    return {"job_id": job.id}
