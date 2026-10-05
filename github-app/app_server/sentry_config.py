"""Shared Sentry setup for app_server and scan_worker (the two processes
that already depend on app_server.config). jina_embed/server.py does NOT
use this module - it's built into a separate Docker image that never has
app_server on its path (see Dockerfile.jina-embed) - and keeps its own,
deliberately duplicated, self-contained init block instead.

A single init_sentry() call per process is enough for broad coverage:
sentry_sdk's LoggingIntegration hooks the logging module itself, so any
logger.exception(...)/logger.error(..., exc_info=True) call anywhere in
that process - present or future - becomes a Sentry event with no
per-call-site code change. See
docs/superpowers/specs/2026-10-05-sentry-error-tracking-design.md for why
that, rather than touching every except block, is the real mechanism here.
"""
import logging

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

from app_server.config import get_settings


def _scrub_event(event: dict, hint: dict) -> dict:
    """Strip request data and stack-frame local variables before an event
    leaves the process, consistent with Aletheore's existing no-telemetry
    privacy stance applied to this separate category of operational error
    data.
    """
    event.pop("request", None)
    for exc_value in event.get("exception", {}).get("values", []):
        for frame in exc_value.get("stacktrace", {}).get("frames", []):
            frame.pop("vars", None)
    return event


def init_sentry(service_name: str) -> None:
    """No-op when SENTRY_DSN is unset - local dev, tests, and CI need zero
    Sentry configuration. Call once near process startup.
    """
    settings = get_settings()
    if not settings.sentry_dsn:
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        send_default_pii=False,
        before_send=_scrub_event,
        traces_sample_rate=0,
        integrations=[
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)
        ],
    )
    sentry_sdk.set_tag("service", service_name)
