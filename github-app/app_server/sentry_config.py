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
from starlette.requests import ClientDisconnect

from app_server.config import get_settings

logger = logging.getLogger("app_server.sentry_config")


def _scrub_event(event: dict, hint: dict) -> dict:
    """Strip request data, stack-frame local variables, and breadcrumbs
    before an event leaves the process, consistent with Aletheore's
    existing no-telemetry privacy stance applied to this separate category
    of operational error data.

    Breadcrumbs matter here as much as the exception itself:
    LoggingIntegration attaches up to 100 recent INFO+ log records
    (fully-formatted messages and their `extra` dicts) to every event -
    confirmed live, an unrelated earlier log line leaks into a later,
    unrelated error event's breadcrumbs otherwise.
    """
    event.pop("request", None)
    event.pop("breadcrumbs", None)
    for exc_value in event.get("exception", {}).get("values", []):
        for frame in exc_value.get("stacktrace", {}).get("frames", []):
            frame.pop("vars", None)
    return event


def init_sentry(service_name: str) -> None:
    """No-op when SENTRY_DSN is unset - local dev, tests, and CI need zero
    Sentry configuration. Call once near process startup.

    Never raises: a malformed SENTRY_DSN (a typo in an optional
    observability setting) must not be the reason a process fails to
    start - logged and skipped instead, same "never let the alerting
    mechanism itself become the outage" principle error_alerts.py
    already follows.
    """
    settings = get_settings()
    if not settings.sentry_dsn:
        return
    try:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.sentry_environment,
            send_default_pii=False,
            # Belt-and-suspenders alongside before_send's own frame.vars
            # stripping below - stops local variables from being collected
            # in the first place rather than only scrubbing them after.
            include_local_variables=False,
            before_send=_scrub_event,
            traces_sample_rate=0,
            # ClientDisconnect is explicitly not a bug anywhere it's
            # raised (see app_server/main.py's own handler comment: "This
            # is neither a bug alert nor a 5xx for the webhook counter").
            # LoggingIntegration's event_level=ERROR keeps it out of the
            # logging path, but sentry_sdk's auto-enabled Starlette/FastAPI
            # integration separately captures ANY exception that
            # propagates through the ASGI app - confirmed live, it reached
            # Sentry despite the logging-level guard. ignore_errors is
            # what actually suppresses it, regardless of capture path.
            ignore_errors=[ClientDisconnect],
            integrations=[
                LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)
            ],
        )
    except Exception:  # noqa: BLE001
        logger.warning("Sentry initialization failed; continuing without it", exc_info=True)
        return
    # On the global scope, not the isolation scope sentry_sdk.set_tag()
    # writes to by default - confirmed live, a tag set there is missing
    # from events reported by a thread started via _thread.start_new_thread
    # (as opposed to threading.Thread, which inherits it). The global scope
    # is the one surface every code path in this process - any thread,
    # any request - actually reads from.
    sentry_sdk.get_global_scope().set_tag("service", service_name)
