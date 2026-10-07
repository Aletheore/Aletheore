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
import os
import platform
from pathlib import Path

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

from aletheore.preferences import is_crash_reporting_enabled

_CLI_SENTRY_DSN = (
    "https://2db08ff31f604202c9bccb356383f072@"
    "o4512209917444096.ingest.de.sentry.io/4512209960173648"
)

def _default_home() -> str:
    try:
        return str(Path.home())
    except RuntimeError:
        # Path.home() itself can raise - not just resolve to something
        # unwritable - when there is no HOME env var and no passwd entry
        # (e.g. a process running as an arbitrary UID with neither, a
        # real scenario this repo's own CI smoke suite exercises). This
        # runs at *module import time* (_HOME below), so an unguarded
        # call here would crash `import aletheore.cli` itself. An empty
        # _HOME makes _redact_home a safe no-op (guarded below) - there
        # is no meaningful home path to redact when one can't be
        # determined in the first place.
        return ""


_HOME = _default_home()
# Anchored on a real path separator so a bare prefix match can never land
# mid-name - /Users/ari must never match inside /Users/arijit/... (a real
# finding from final review: a plain str.startswith/.replace on _HOME
# alone has exactly this false-positive).
_HOME_PREFIX = _HOME + os.sep if _HOME else ""


def _redact_home(text: object) -> object:
    """Replace every occurrence of the real home directory - anchored on
    the trailing path separator - with ~, anywhere in a string: a frame's
    filename, but also free text like an exception message ("No such file
    or directory: '/Users/alice/project/x.py'") or a log/breadcrumb
    message, which embed the path mid-string rather than as the whole
    value.

    Matched via os.path.normcase (a no-op on POSIX, lowercasing on
    Windows): Windows filesystems are case-preserving but
    case-insensitive, so a traceback frame's casing - set by however the
    module happened to be imported - can differ from Path.home()'s own
    casing, and a plain case-sensitive match would silently fail to
    redact it there.
    """
    if not isinstance(text, str) or not text or not _HOME:
        # not _HOME: no home directory could be determined at all (see
        # _default_home) - there's nothing to redact, and critically, an
        # empty _HOME_PREFIX would make `needle` below an empty string,
        # which is a substring of everything and would corrupt every
        # string this function touches.
        return text

    haystack = os.path.normcase(text)
    if haystack == os.path.normcase(_HOME):
        return "~"

    needle = os.path.normcase(_HOME_PREFIX)
    if needle not in haystack:
        return text

    pieces = []
    pos = 0
    while True:
        idx = haystack.find(needle, pos)
        if idx == -1:
            pieces.append(text[pos:])
            break
        pieces.append(text[pos:idx])
        pieces.append("~" + os.sep)
        pos = idx + len(needle)
    return "".join(pieces)


def _scrub_event(event: dict, hint: dict) -> dict:
    """Strip anything that identifies the user or their machine, while
    keeping the OS/Python-version signal this feature exists to collect.
    """
    event.pop("request", None)
    event.pop("server_name", None)
    # The default ArgvIntegration attaches the full argv - including
    # absolute paths, repo names, and any free-text query arguments - to
    # every event. Dropping it entirely (rather than trying to redact
    # each argument) is the only way to not leak whatever a user typed.
    event.get("extra", {}).pop("sys.argv", None)

    device = event.get("contexts", {}).get("device")
    if isinstance(device, dict):
        device.pop("name", None)

    message = event.get("message")
    if isinstance(message, str):
        event["message"] = _redact_home(message)

    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        for field in ("formatted", "message"):
            if isinstance(logentry.get(field), str):
                logentry[field] = _redact_home(logentry[field])

    for crumb in event.get("breadcrumbs", {}).get("values", []):
        if isinstance(crumb.get("message"), str):
            crumb["message"] = _redact_home(crumb["message"])

    for exc_value in event.get("exception", {}).get("values", []):
        if isinstance(exc_value.get("value"), str):
            exc_value["value"] = _redact_home(exc_value["value"])
        for frame in exc_value.get("stacktrace", {}).get("frames", []):
            frame.pop("vars", None)
            for path_field in ("filename", "abs_path"):
                if isinstance(frame.get(path_field), str):
                    frame[path_field] = _redact_home(frame[path_field])

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
    # Final-review finding: the SDK does not reliably attach an os context
    # on its own in this configuration - the OS/Python-version signal is
    # the entire point of this feature, so set it explicitly rather than
    # trust an SDK default that was empirically found missing.
    sentry_sdk.set_context(
        "os", {"name": platform.system(), "version": platform.release()}
    )
