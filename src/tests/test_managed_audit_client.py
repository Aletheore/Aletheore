import httpx
import pytest

from aletheore.managed_audit_client import ManagedAuditError, run_managed_audit_request


def test_successful_request_returns_report():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/v1/managed-audit" and request.method == "POST":
            return httpx.Response(202, json={"job_id": "job-1"})
        if request.url.path == "/v1/managed-audit/job-1":
            return httpx.Response(200, json={"status": "finished", "result": "# Report"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://aletheore.com")
    report = run_managed_audit_request({"scanned_at": "x"}, "real-token", http_client=client, poll_interval=0)

    assert report == "# Report"
    assert calls[0].headers["Authorization"] == "Bearer real-token"


def test_pending_then_finished_polls_until_done():
    responses = iter(
        [
            httpx.Response(200, json={"status": "pending"}),
            httpx.Response(200, json={"status": "finished", "result": "done"}),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"job_id": "job-1"})
        return next(responses)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://aletheore.com")
    report = run_managed_audit_request(
        {"scanned_at": "x"}, "real-token", http_client=client, poll_interval=0
    )
    assert report == "done"


def test_encoding_failure_raises_managed_audit_error_not_a_raw_exception(monkeypatch):
    def _boom(_data):
        from aletheore.toon_encoding import ToonEncodingError

        raise ToonEncodingError("simulated failure")

    monkeypatch.setattr("aletheore.managed_audit_client.to_toon", _boom)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should never reach the network on an encoding failure")

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://aletheore.com")
    with pytest.raises(ManagedAuditError, match="could not encode evidence"):
        run_managed_audit_request({"scanned_at": "x"}, "real-token", http_client=client)


def test_unauthorized_raises_managed_audit_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "invalid or revoked token"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://aletheore.com")
    with pytest.raises(ManagedAuditError, match="invalid or revoked token"):
        run_managed_audit_request({"scanned_at": "x"}, "bad-token", http_client=client)


def test_rate_limited_raises_managed_audit_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"detail": "managed audit rate limit: try again later"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://aletheore.com")
    with pytest.raises(ManagedAuditError, match="rate limit"):
        run_managed_audit_request({"scanned_at": "x"}, "real-token", http_client=client)


def test_request_includes_repo_full_name():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            captured["body"] = request.read()
            return httpx.Response(202, json={"job_id": "job-1"})
        return httpx.Response(200, json={"status": "finished", "result": "# Report"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://aletheore.com")
    run_managed_audit_request(
        {"scanned_at": "x"},
        "real-token",
        repo_full_name="acme/widgets",
        http_client=client,
        poll_interval=0,
    )

    import json

    assert json.loads(captured["body"])["repo_full_name"] == "acme/widgets"


def test_failed_job_raises_managed_audit_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"job_id": "job-1"})
        return httpx.Response(200, json={"status": "failed"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://aletheore.com")
    with pytest.raises(ManagedAuditError, match="failed"):
        run_managed_audit_request({"scanned_at": "x"}, "real-token", http_client=client, poll_interval=0)


def test_no_http_client_passed_closes_the_client_it_creates_on_success(monkeypatch):
    # Real bug found via audit: run_managed_audit_request created its own
    # httpx.Client whenever a caller didn't pass one in, but never closed
    # it on any path - both real production callers (mcp_server.py's
    # aletheore_managed_audit tool, cli.py's `aletheore audit` command)
    # hit this path, since neither passes http_client. In the long-running
    # MCP server this leaked one connection pool per call.
    created = []

    class TrackingClient(httpx.Client):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, transport=httpx.MockTransport(_handler), **kwargs)
            created.append(self)

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"job_id": "job-1"})
        return httpx.Response(200, json={"status": "finished", "result": "# Report"})

    monkeypatch.setattr("aletheore.managed_audit_client.httpx.Client", TrackingClient)

    report = run_managed_audit_request({"scanned_at": "x"}, "real-token", poll_interval=0)

    assert report == "# Report"
    assert len(created) == 1
    assert created[0].is_closed is True


def test_no_http_client_passed_closes_the_client_it_creates_on_error(monkeypatch):
    created = []

    class TrackingClient(httpx.Client):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, transport=httpx.MockTransport(_handler), **kwargs)
            created.append(self)

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "invalid or revoked token"})

    monkeypatch.setattr("aletheore.managed_audit_client.httpx.Client", TrackingClient)

    with pytest.raises(ManagedAuditError):
        run_managed_audit_request({"scanned_at": "x"}, "bad-token")

    assert len(created) == 1
    assert created[0].is_closed is True


def test_a_falsy_caller_supplied_http_client_is_still_used_and_never_closed():
    # Flash Review finding: client selection used to be `http_client or
    # httpx.Client(...)` (truthiness) while ownership was tracked via
    # `http_client is None` (identity) - a caller-supplied client that's
    # falsy (unusual, but real: any object can define __bool__) would be
    # silently discarded in favor of a freshly created one, while
    # ownership still said "not owned", so that new client leaked -
    # never closed, and the caller's own client silently never used.
    class FalsyClient(httpx.Client):
        def __bool__(self) -> bool:
            return False

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"job_id": "job-1"})
        return httpx.Response(200, json={"status": "finished", "result": "# Report"})

    client = FalsyClient(transport=httpx.MockTransport(handler), base_url="https://aletheore.com")
    report = run_managed_audit_request({"scanned_at": "x"}, "real-token", http_client=client, poll_interval=0)

    assert report == "# Report"  # proves the caller's own falsy client was really used
    assert client.is_closed is False
    client.close()


def test_a_caller_supplied_http_client_is_never_closed():
    # The caller owns the lifecycle of a client it passed in explicitly
    # (e.g. one reused across several managed-audit calls) - this function
    # must not close it out from under the caller.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"job_id": "job-1"})
        return httpx.Response(200, json={"status": "finished", "result": "# Report"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://aletheore.com")
    run_managed_audit_request({"scanned_at": "x"}, "real-token", http_client=client, poll_interval=0)

    assert client.is_closed is False
    client.close()
