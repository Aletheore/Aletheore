import functools

from redis import Redis

from app_server.config import get_settings

# Shared between app_server.main's handle_unexpected_exception (writer, the
# only place a /webhook 5xx can originate) and scan_worker.jobs's
# run_ops_monitor_job (reader, via _check_webhook_errors).
# A plain INCR + refreshed EXPIRE rather than a registry: there's no RQ-style
# live count to read for this the way queue depth/failed jobs have one, so
# the count is self-maintained and decays on its own once 5xxs stop for a
# full window, instead of needing an explicit reset anywhere.
WEBHOOK_5XX_COUNT_KEY = "ops_monitor:webhook_5xx:count"
# Real bug found in a backward audit: this used to be 900s, longer than
# jobs.py's OPS_THRESHOLD_DURATION_SECONDS (600s) that _check_webhook_errors
# gates on. _check_threshold_duration's "sustained above threshold" logic
# assumes current_value is a live gauge that drops the instant the real
# condition resolves (true for queue depth/failed jobs), but this counter
# only decays via its own TTL - so one single, already-retried 5xx kept
# current_value > threshold continuously for the full 600s bar regardless of
# whether any further failures ever happened, indistinguishable from a
# genuinely recurring failure. Must stay strictly less than
# OPS_THRESHOLD_DURATION_SECONDS so an isolated failure's counter always
# decays back to 0 (and _check_threshold_duration's own current_value <=
# threshold: delete state_key; return early-return fires) before the 600s
# sustained check can, while a genuinely recurring failure - which keeps
# refreshing this TTL on every new 5xx - still stays elevated continuously
# past 600s and alerts correctly.
WEBHOOK_5XX_WINDOW_SECONDS = 300


def record_webhook_5xx(redis_conn: Redis) -> None:
    """Durable counter for 5xx responses from POST /webhook.

    Real incident (2026-09-18): a synchronous crash in webhook handling
    produced a 500 with no visible signal anywhere - not in ops_monitor,
    and not even the per-request crash email from error_alerts.py, whose
    dedup cooldown key is keyed on (source, exception type) alone with no
    route in it, so an unrelated exception of the same type elsewhere in
    app_server can silently suppress it. This counter is independent of
    that path: durable in Redis rather than a process-local dict, so it
    survives the container restart that recycles the crash logs too.
    """
    redis_conn.incr(WEBHOOK_5XX_COUNT_KEY)
    redis_conn.expire(WEBHOOK_5XX_COUNT_KEY, WEBHOOK_5XX_WINDOW_SECONDS)


@functools.lru_cache(maxsize=1)
def get_redis_client() -> Redis:
    """Process-wide pooled Redis client.

    Every caller across app_server and scan_worker used to construct its
    own Redis.from_url(...) per request or per job - each one opening a
    fresh connection pool and never explicitly closing it, relying on GC to
    eventually close the underlying socket. Under load, connections pile up
    faster than GC reclaims them. lru_cache makes this a singleton exactly
    once per process (same convention as get_settings()), so every caller
    shares the same pooled connection instead.

    Short connect/socket timeouts rather than redis-py's multi-second
    default: every use of this client is a fast, simple operation (a rate-
    limit INCR, an RQ enqueue, a healthcheck ping) that should fail fast
    into a fallback or a 5xx if Redis is slow or unreachable, not hang a
    request behind it. First applied narrowly to admin.py's installation-id
    cache; every other caller needs the exact same property, so it's the
    default here rather than something each caller opts into separately.
    """
    return Redis.from_url(get_settings().redis_url, socket_connect_timeout=0.5, socket_timeout=0.5)
