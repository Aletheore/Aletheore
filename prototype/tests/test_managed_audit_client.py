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


def test_unauthorized_raises_managed_audit_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "invalid or revoked token"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://aletheore.com")
    with pytest.raises(ManagedAuditError, match="invalid or revoked token"):
        run_managed_audit_request({"scanned_at": "x"}, "bad-token", http_client=client)


def test_failed_job_raises_managed_audit_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"job_id": "job-1"})
        return httpx.Response(200, json={"status": "failed"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://aletheore.com")
    with pytest.raises(ManagedAuditError, match="failed"):
        run_managed_audit_request({"scanned_at": "x"}, "real-token", http_client=client, poll_interval=0)
