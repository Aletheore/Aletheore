import functools
import time

import httpx

# A real, confirmed gap (overnight audit, fifth pass): no GitHub rate-limit
# awareness existed anywhere in github_api.py or this shared client - a
# 403 rate-limit was treated identically to any other HTTP error, with no
# graceful degradation once an installation's token was throttled. Capped
# so a single stuck request can't sleep a job past its own job_timeout.
_MAX_RATE_LIMIT_BACKOFF_SECONDS = 60


def _rate_limit_wait_seconds(headers: httpx.Headers) -> float | None:
    """How long to wait before retrying a response that may be a GitHub
    rate limit, or None if it isn't one (a genuine permission 403/429
    must pass through unchanged, not be retried).

    Checked in the order GitHub's own docs describe: Retry-After first
    (secondary rate limits - abuse detection, concurrent-request limits -
    carry this regardless of the primary limit's own remaining count),
    then X-RateLimit-Remaining/X-RateLimit-Reset (the primary REST API
    rate limit, only meaningful once Remaining has actually hit 0 - a
    present-but-nonzero Remaining header means this request wasn't
    rejected for rate-limit reasons at all).
    """
    retry_after = headers.get("retry-after")
    if retry_after is not None:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            return None

    if headers.get("x-ratelimit-remaining") != "0":
        return None
    reset_at = headers.get("x-ratelimit-reset")
    if reset_at is None:
        return None
    try:
        return max(0.0, float(reset_at) - time.time())
    except ValueError:
        return None


class _RateLimitRetryTransport(httpx.BaseTransport):
    """Wraps a real transport: on a GitHub rate-limit response (403 or
    429 carrying rate-limit headers), sleeps until the limit resets
    (capped at _MAX_RATE_LIMIT_BACKOFF_SECONDS) and retries the request
    exactly once - not a loop, so one stuck request can't sleep a job
    forever if the retry is also rate-limited. A response with no
    rate-limit headers (a genuine permission error, or any other status)
    passes through unchanged, same as before this existed.
    """

    def __init__(self, transport: httpx.BaseTransport) -> None:
        self._transport = transport

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._transport.handle_request(request)
        if response.status_code not in (403, 429):
            return response

        wait_seconds = _rate_limit_wait_seconds(response.headers)
        if wait_seconds is None:
            return response

        response.close()
        time.sleep(min(wait_seconds, _MAX_RATE_LIMIT_BACKOFF_SECONDS))
        return self._transport.handle_request(request)

    def close(self) -> None:
        self._transport.close()


@functools.lru_cache(maxsize=1)
def get_github_api_client() -> httpx.Client:
    """Process-wide pooled client for https://api.github.com.

    Every GitHub API call across app_server and scan_worker used to
    construct its own httpx.Client(base_url=...) per request or per job -
    9 separate call sites in scan_worker/jobs.py alone - each opening a
    fresh connection pool and, in every case but one, never explicitly
    closing it. lru_cache makes this a singleton exactly once per process,
    reused across every caller instead.

    Wrapped in _RateLimitRetryTransport (not the OAuth/generic clients
    below - this is specifically about github_api.py's REST calls, per
    the audit finding above) for graceful degradation under GitHub's rate
    limits instead of treating a 403/429 identically to any other error.
    """
    return httpx.Client(
        base_url="https://api.github.com",
        transport=_RateLimitRetryTransport(httpx.HTTPTransport()),
    )


@functools.lru_cache(maxsize=1)
def get_github_oauth_client() -> httpx.Client:
    """Process-wide pooled client for https://github.com (OAuth endpoints -
    distinct host from the API client above, so it needs its own pool)."""
    return httpx.Client(base_url="https://github.com")


@functools.lru_cache(maxsize=1)
def get_generic_http_client() -> httpx.Client:
    """Process-wide pooled client with no base_url, for one-off requests to
    an arbitrary external host (a customer's Slack webhook, Resend's API) -
    reused across calls instead of each one opening its own connection.
    Per-call timeout overrides (client.post(url, ..., timeout=X)) still
    work normally against a shared client.
    """
    return httpx.Client()
