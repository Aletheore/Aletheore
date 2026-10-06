"""Sentry crash reporting for the local CLI (src/aletheore). Deliberately
separate from the backend's github-app/app_server/sentry_config.py, not a
shared import: different Sentry project (aletheore-cli vs aletheore),
different trust model (this DSN ships inside a public PyPI package, so it
stays a module constant here rather than reading SENTRY_DSN from the
environment the way the backend does), and a different scrub policy - see
docs/superpowers/specs/2026-10-06-cli-crash-reporting-design.md.
"""
import importlib.metadata
import logging
from pathlib import Path

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

from aletheore.preferences import is_crash_reporting_enabled

_CLI_SENTRY_DSN = (
    "https://2db08ff31f604202c9bccb356383f072@"
    "o4512209917444096.ingest.de.sentry.io/4512209960173648"
)

_HOME = str(Path.home())


def _scrub_event(event: dict, hint: dict) -> dict:
    """Strip anything that identifies the user or their machine, while
    keeping the OS/Python-version signal this feature exists to collect.
    """
    event.pop("request", None)
    event.pop("server_name", None)

    device = event.get("contexts", {}).get("device")
    if isinstance(device, dict):
        device.pop("name", None)

    for exc_value in event.get("exception", {}).get("values", []):
        for frame in exc_value.get("stacktrace", {}).get("frames", []):
            frame.pop("vars", None)
            for path_field in ("filename", "abs_path"):
                value = frame.get(path_field)
                if isinstance(value, str) and value.startswith(_HOME):
                    frame[path_field] = "~" + value[len(_HOME) :]

    return event


def init_cli_sentry() -> None:
    """No-op if crash reporting is disabled (preferences.py) - checked
    fresh on every call, not cached, so a mid-session
    `aletheore config crash-reporting off` takes effect on the CLI's next
    invocation with no reinstall needed.
    """
    if not is_crash_reporting_enabled():
        return

    sentry_sdk.init(
        dsn=_CLI_SENTRY_DSN,
        environment="production",
        release=f"aletheore-cli@{importlib.metadata.version('aletheore')}",
        send_default_pii=False,
        before_send=_scrub_event,
        traces_sample_rate=0,
        integrations=[
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)
        ],
    )
