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
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from aletheore.runtime_events import parse_sentry_event
from app_server.config import get_settings
from app_server.db import touch_api_token
from app_server.managed_audit_api import _authenticate_token, _get_queue

runtime_events_router = APIRouter()


class RuntimeEventRequest(BaseModel):
    repo_full_name: str = Field(min_length=1)
    event: dict


@runtime_events_router.post("/v1/runtime-events")
async def report_runtime_event(request: Request, body: RuntimeEventRequest):
    installation, token_hash = await _authenticate_token(request)
    if installation["plan"] == "free":
        raise HTTPException(status_code=402, detail="runtime event correlation requires a paid plan")

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
