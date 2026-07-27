"""Anonymous CLI usage telemetry - see migrations/023_cli_telemetry.sql
for the privacy/scope rationale. Public, unauthenticated ingestion (the
CLI has no account/token to authenticate with); internal stats read
requires the same bearer-token gate as metrics.py's queue-stats."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app_server.config import get_settings
from app_server.db import count_telemetry_events, record_telemetry_event

telemetry_router = APIRouter()

_VALID_EVENT_TYPES = {"scan"}


class TelemetryEvent(BaseModel):
    event: str
    anonymous_id: str = Field(min_length=8, max_length=64)


@telemetry_router.post("/v1/telemetry")
async def report_telemetry_event(payload: TelemetryEvent, request: Request):
    if payload.event not in _VALID_EVENT_TYPES:
        raise HTTPException(status_code=400, detail="unknown event type")
    await record_telemetry_event(request.app.state.db_pool, payload.event, payload.anonymous_id)
    return {"ok": True}


@telemetry_router.get("/v1/internal/telemetry-stats")
async def telemetry_stats(request: Request, event: str = "scan"):
    settings = get_settings()
    if not settings.internal_metrics_token:
        raise HTTPException(status_code=404, detail="not found")

    auth_header = request.headers.get("Authorization", "")
    if auth_header != f"Bearer {settings.internal_metrics_token}":
        raise HTTPException(status_code=401, detail="missing or invalid token")

    return await count_telemetry_events(request.app.state.db_pool, event)
