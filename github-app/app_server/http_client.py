import functools

import httpx


@functools.lru_cache(maxsize=1)
def get_github_api_client() -> httpx.Client:
    """Process-wide pooled client for https://api.github.com.

    Every GitHub API call across app_server and scan_worker used to
    construct its own httpx.Client(base_url=...) per request or per job -
    9 separate call sites in scan_worker/jobs.py alone - each opening a
    fresh connection pool and, in every case but one, never explicitly
    closing it. lru_cache makes this a singleton exactly once per process,
    reused across every caller instead.
    """
    return httpx.Client(base_url="https://api.github.com")


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
