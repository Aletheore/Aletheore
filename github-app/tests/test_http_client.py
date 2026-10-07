import httpx

from app_server.http_client import (
    _RateLimitRetryTransport,
    _rate_limit_wait_seconds,
    get_generic_http_client,
    get_github_api_client,
    get_github_oauth_client,
)


def test_get_github_api_client_returns_the_same_instance_across_calls():
    assert get_github_api_client() is get_github_api_client()


def test_get_github_api_client_has_the_right_base_url():
    assert str(get_github_api_client().base_url) == "https://api.github.com"


def test_get_github_oauth_client_returns_the_same_instance_across_calls():
    assert get_github_oauth_client() is get_github_oauth_client()


def test_get_github_oauth_client_has_the_right_base_url():
    assert str(get_github_oauth_client().base_url) == "https://github.com"


def test_get_github_api_client_and_oauth_client_are_distinct():
    # Different hosts need different pools - conflating them would mean a
    # relative-path request meant for one host silently resolving against
    # the other's base_url.
    assert get_github_api_client() is not get_github_oauth_client()


def test_get_generic_http_client_returns_the_same_instance_across_calls():
    assert get_generic_http_client() is get_generic_http_client()


def test_get_generic_http_client_has_no_base_url():
    assert str(get_generic_http_client().base_url) == ""


def test_get_github_api_client_wraps_a_rate_limit_retry_transport():
    # Scoped to the GitHub API client specifically - the audit's own
    # finding was about github_api.py's REST calls (check-run creation,
    # comment posting, etc.) hitting GitHub's primary/secondary rate
    # limits with no retry anywhere; the OAuth/generic clients aren't
    # part of that finding and are deliberately left untouched.
    assert isinstance(get_github_api_client()._transport, _RateLimitRetryTransport)


# ── _rate_limit_wait_seconds ─────────────────────────────────────────────


def test_rate_limit_wait_seconds_prefers_retry_after_when_present():
    # GitHub's documented secondary-rate-limit shape: a Retry-After
    # header in seconds, independent of the primary limit's reset time.
    headers = httpx.Headers({"retry-after": "30"})
    assert _rate_limit_wait_seconds(headers) == 30.0


def test_rate_limit_wait_seconds_uses_reset_time_when_remaining_is_zero(monkeypatch):
    import app_server.http_client as http_client_module

    monkeypatch.setattr(http_client_module.time, "time", lambda: 1000.0)
    headers = httpx.Headers({"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1045"})
    assert _rate_limit_wait_seconds(headers) == 45.0


def test_rate_limit_wait_seconds_is_none_when_remaining_is_not_zero():
    # The primary signal that this 403 is a rate limit, not a permission
    # error: x-ratelimit-remaining == 0. Anything else present but
    # nonzero means this request simply wasn't rejected for rate-limit
    # reasons.
    headers = httpx.Headers({"x-ratelimit-remaining": "42", "x-ratelimit-reset": "1045"})
    assert _rate_limit_wait_seconds(headers) is None


def test_rate_limit_wait_seconds_is_none_when_no_rate_limit_headers_present():
    # A genuine permission 403 (no rate-limit headers at all) must not be
    # mistaken for a rate limit and retried.
    assert _rate_limit_wait_seconds(httpx.Headers({})) is None


def test_rate_limit_wait_seconds_never_negative(monkeypatch):
    # A reset timestamp already in the past (clock drift, or this call
    # itself raced the reset) must floor at 0, not return a negative
    # sleep duration.
    import app_server.http_client as http_client_module

    monkeypatch.setattr(http_client_module.time, "time", lambda: 2000.0)
    headers = httpx.Headers({"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1000"})
    assert _rate_limit_wait_seconds(headers) == 0.0


# ── _RateLimitRetryTransport ─────────────────────────────────────────────


def test_rate_limit_retry_transport_passes_through_a_normal_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    transport = _RateLimitRetryTransport(httpx.MockTransport(handler))
    client = httpx.Client(transport=transport, base_url="https://api.github.com")

    response = client.get("/repos/octocat/hello-world")

    assert response.status_code == 200


def test_rate_limit_retry_transport_passes_through_a_non_rate_limit_403():
    # A real permission error (e.g. the token lacks the scope) must not
    # be retried or delayed - only a 403 carrying rate-limit headers is.
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(403, json={"message": "Resource not accessible"})

    transport = _RateLimitRetryTransport(httpx.MockTransport(handler))
    client = httpx.Client(transport=transport, base_url="https://api.github.com")

    response = client.get("/repos/octocat/hello-world")

    assert response.status_code == 403
    assert len(calls) == 1


def test_rate_limit_retry_transport_retries_once_after_waiting(monkeypatch):
    import app_server.http_client as http_client_module

    sleeps = []
    monkeypatch.setattr(http_client_module.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(http_client_module.time, "time", lambda: 1000.0)

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                403,
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1010"},
                json={"message": "API rate limit exceeded"},
            )
        return httpx.Response(200, json={"ok": True})

    transport = _RateLimitRetryTransport(httpx.MockTransport(handler))
    client = httpx.Client(transport=transport, base_url="https://api.github.com")

    response = client.get("/repos/octocat/hello-world")

    assert response.status_code == 200
    assert len(calls) == 2
    assert sleeps == [10.0]


def test_rate_limit_retry_transport_caps_the_wait_at_the_max_backoff(monkeypatch):
    import app_server.http_client as http_client_module

    sleeps = []
    monkeypatch.setattr(http_client_module.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(http_client_module.time, "time", lambda: 0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "99999"},
            json={"message": "API rate limit exceeded"},
        )

    transport = _RateLimitRetryTransport(httpx.MockTransport(handler))
    client = httpx.Client(transport=transport, base_url="https://api.github.com")

    client.get("/repos/octocat/hello-world")

    assert sleeps == [http_client_module._MAX_RATE_LIMIT_BACKOFF_SECONDS]


def test_rate_limit_retry_transport_only_retries_once():
    # If the retry ALSO comes back rate-limited, give up rather than
    # looping - a single job run should not be able to sleep forever.
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            403,
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "0"},
            json={"message": "API rate limit exceeded"},
        )

    transport = _RateLimitRetryTransport(httpx.MockTransport(handler))
    client = httpx.Client(transport=transport, base_url="https://api.github.com")

    response = client.get("/repos/octocat/hello-world")

    assert response.status_code == 403
    assert len(calls) == 2
