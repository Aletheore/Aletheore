"""Best-effort alerting for an unexpected, unhandled exception in
Aletheore's OWN backend - app_server routes and scan_worker jobs -
as opposed to runtime_events.py, which only ever ingests exceptions
from a *customer's* app. Before this, the only way to learn about a
crash here was reading logs after the fact (see: the event-loop
freeze bug, found reactively).

Reuses the existing Resend transactional-email infra rather than
standing up a separate error-tracking service - sends to
email_reply_to_address (support@aletheore.com), a real inbox someone
already checks.
"""

import logging
import time

from app_server.config import get_settings
from scan_worker.email import send_transactional_email

logger = logging.getLogger("app_server.error_alerts")

# Rate-limited per (source, exception type), not globally - a runaway loop
# hitting the same bug shouldn't flood the inbox, but an unrelated second
# bug during the same window should still get its own alert. Process-local
# and reset on restart, which is fine: the goal is "don't flood," not a
# durable dedupe ledger.
_ALERT_COOLDOWN_SECONDS = 900
_last_alert_at: dict[str, float] = {}


def _should_alert(key: str) -> bool:
    now = time.monotonic()
    last = _last_alert_at.get(key)
    if last is not None and now - last < _ALERT_COOLDOWN_SECONDS:
        return False
    _last_alert_at[key] = now
    return True


def send_error_alert(source: str, error: BaseException, context: str = "") -> None:
    """source identifies where this came from (e.g. "app_server" or a job
    name like "run_flash_review_job"), context is a short human-readable
    line (e.g. the request path, or the installation/repo being processed).

    Never raises - a failure to send the alert itself must not turn one
    bug into two. Call this from an except block, not instead of logging;
    it's a notification, not a substitute for the structured log entry.
    """
    key = f"{source}:{type(error).__name__}"
    if not _should_alert(key):
        return

    settings = get_settings()
    if not settings.resend_api_key:
        return

    subject = f"[Aletheore alert] {source}: {type(error).__name__}"
    body_text = f"{context}\n\n{type(error).__name__}: {error}".strip()
    try:
        send_transactional_email(
            settings.resend_api_key,
            settings.email_from_address,
            settings.email_reply_to_address,
            settings.email_reply_to_address,
            subject,
            f"<pre>{body_text}</pre>",
            body_text,
        )
    except Exception:  # noqa: BLE001
        logger.warning("failed to send error alert email for %s", key, exc_info=True)
