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

from app_server.config import get_settings
from app_server.email_client import send_transactional_email
from app_server.redis_client import get_redis_client

logger = logging.getLogger("app_server.error_alerts")

# Rate-limited per (source, exception type), not globally - a runaway loop
# hitting the same bug shouldn't flood the inbox, but an unrelated second
# bug during the same window should still get its own alert.
#
# 6 hours, matching ops_monitor's OPS_ALERT_COOLDOWN_SECONDS - same
# policy in both places: alert once, then only remind every 6 hours for
# as long as the same issue keeps recurring, not on every occurrence.
_ALERT_COOLDOWN_SECONDS = 6 * 60 * 60
_ALERT_COOLDOWN_KEY_PREFIX = "error_alerts:cooldown:"


def _should_alert(key: str) -> bool:
    # Real bug found live in production (2026-09-21): this used to be a
    # plain process-local dict, with a comment claiming "process-local and
    # reset on restart is fine." It wasn't - scan_worker.worker runs RQ's
    # default Worker class, which forks a fresh child process for every
    # single job. Each fork's write to that dict died with the fork, so the
    # cooldown never actually survived past the one job that set it - every
    # tick of a periodically-scheduled job (e.g. run_health_sweep_staleness_
    # check_job, every 180s) re-alerted from scratch, for as long as the
    # underlying condition stayed true. A health-sweep-stale condition that
    # persisted for 12+ hours sent an email roughly every 3 minutes instead
    # of once. Same root cause redis_client.py's own record_webhook_5xx
    # docstring already flagged as a known, separate weakness in this exact
    # module - fixed the same way that function already was: a Redis key
    # with a TTL, atomic across every forked job process and both
    # scan-worker replicas, instead of memory local to whichever process
    # happens to run this particular job execution.
    try:
        return bool(
            get_redis_client().set(
                _ALERT_COOLDOWN_KEY_PREFIX + key, "1", nx=True, ex=_ALERT_COOLDOWN_SECONDS
            )
        )
    except Exception:  # noqa: BLE001
        # Redis unreachable must never be the reason a real alert never
        # sends - fail open (alert) rather than silently suppressing every
        # error notification along with it.
        logger.warning("alert cooldown check failed for %s; alerting anyway", key, exc_info=True)
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
