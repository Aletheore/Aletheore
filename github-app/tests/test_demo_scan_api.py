import json
from unittest.mock import MagicMock

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app_server.main import app


@pytest.fixture(autouse=True)
def _disable_demo_abuse_limit(monkeypatch):
    monkeypatch.setattr("app_server.demo_scan_api.get_redis_client", lambda: object())
    monkeypatch.setattr("app_server.demo_scan_api.is_rate_limited", lambda *a, **k: False)


def _mock_github_response(monkeypatch, status_code: int, size_kb: int = 100):
    fake_client = MagicMock()
    fake_client.get.return_value = httpx.Response(
        status_code,
        json={"size": size_kb},
        request=httpx.Request("GET", "https://api.github.com/repos/octocat/Hello-World"),
    )

    monkeypatch.setattr("app_server.demo_scan_api.get_generic_http_client", lambda: fake_client)
    return fake_client


def _mock_queue(monkeypatch, count: int = 0, started_count: int = 0, job_id: str = "demo-job-123"):
    fake_queue = MagicMock()
    fake_queue.count = count
    fake_queue.started_job_registry.count = started_count
    fake_queue.enqueue.return_value = MagicMock(id=job_id)
    monkeypatch.setattr("app_server.demo_scan_api._get_queue", lambda redis_url: fake_queue)
    return fake_queue


@pytest.mark.asyncio
async def test_invalid_repo_url_returns_400(pool):
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/demo-scan", json={"repo_url": "not a url"})
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_unknown_repo_returns_404(pool, monkeypatch):
    _mock_github_response(monkeypatch, 404)
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/demo-scan", json={"repo_url": "https://github.com/octocat/does-not-exist"}
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_repo_too_large_returns_413(pool, monkeypatch):
    _mock_github_response(monkeypatch, 200, size_kb=(400 * 1024) + 1)
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/demo-scan", json={"repo_url": "https://github.com/torvalds/linux"}
        )
    assert response.status_code == 413
    assert "install the aletheore cli" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_github_rate_limited_returns_503(pool, monkeypatch):
    _mock_github_response(monkeypatch, 403)
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/demo-scan", json={"repo_url": "https://github.com/octocat/Hello-World"}
        )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_valid_request_enqueues_job_and_returns_202(pool, monkeypatch):
    _mock_github_response(monkeypatch, 200, size_kb=100)
    fake_queue = _mock_queue(monkeypatch)
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/demo-scan", json={"repo_url": "https://github.com/octocat/Hello-World"}
        )
    assert response.status_code == 202
    assert response.json()["job_id"] == "demo-job-123"
    args, kwargs = fake_queue.enqueue.call_args
    assert args[0] == "scan_worker.demo_scan.run_demo_scan_job"
    assert kwargs["repo_url"] == "https://github.com/octocat/Hello-World.git"


@pytest.mark.asyncio
async def test_demo_scan_abuse_limiter_runs_before_github_api_call(monkeypatch):
    calls = {"rate_limit": 0, "github": 0}

    def fake_is_rate_limited(redis_conn, key, limit, window_seconds):
        calls["rate_limit"] += 1
        assert key == "ratelimit:demo-scan:203.0.113.77"
        return calls["rate_limit"] > 2

    async def fake_check_repo_size(owner, repo, token):
        calls["github"] += 1

    async def fake_reserve(pool, client_ip, cooldown_seconds):
        return True

    monkeypatch.setattr("app_server.demo_scan_api.is_rate_limited", fake_is_rate_limited)
    monkeypatch.setattr("app_server.demo_scan_api._check_repo_size", fake_check_repo_size)
    monkeypatch.setattr("app_server.demo_scan_api.check_and_reserve_demo_scan", fake_reserve)
    _mock_queue(monkeypatch)
    app.state.db_pool = object()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"x-forwarded-for": "203.0.113.77"},
    ) as client:
        first = await client.post(
            "/v1/demo-scan", json={"repo_url": "https://github.com/octocat/Hello-World"}
        )
        second = await client.post(
            "/v1/demo-scan", json={"repo_url": "https://github.com/octocat/Spoon-Knife"}
        )
        limited = await client.post(
            "/v1/demo-scan", json={"repo_url": "https://github.com/octocat/linguist"}
        )

    assert first.status_code == 202
    assert second.status_code == 202
    assert limited.status_code == 429
    assert calls == {"rate_limit": 3, "github": 2}


@pytest.mark.asyncio
async def test_repo_size_check_uses_pooled_http_client(monkeypatch):
    fake_client = _mock_github_response(monkeypatch, 200, size_kb=100)

    from app_server.demo_scan_api import _check_repo_size

    await _check_repo_size("octocat", "Hello-World", token="demo-token")

    fake_client.get.assert_called_once()
    _, kwargs = fake_client.get.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer demo-token"


@pytest.mark.asyncio
async def test_oversized_demo_scan_repo_url_is_rejected_before_github_call(monkeypatch):
    check_repo_size = MagicMock()
    monkeypatch.setattr("app_server.demo_scan_api._check_repo_size", check_repo_size)
    app.state.db_pool = object()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/demo-scan",
            json={"repo_url": "https://github.com/" + ("a" * 350)},
        )

    assert response.status_code == 422
    check_repo_size.assert_not_called()


@pytest.mark.asyncio
async def test_oversized_demo_scan_body_is_rejected_before_github_call(monkeypatch):
    check_repo_size = MagicMock()
    monkeypatch.setattr("app_server.demo_scan_api._check_repo_size", check_repo_size)
    app.state.db_pool = object()
    body = json.dumps(
        {"repo_url": "https://github.com/octocat/Hello-World", "pad": "x" * 5000}
    ).encode()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/demo-scan", content=body, headers={"Content-Type": "application/json"}
        )

    assert response.status_code == 413
    check_repo_size.assert_not_called()


@pytest.mark.asyncio
async def test_rejected_oversized_repo_does_not_consume_rate_limit_slot(pool, monkeypatch):
    # A repo that's too large never reaches a worker - it shouldn't cost
    # the visitor their one scan every 20 minutes either. The next request
    # from the same IP, for a repo that fits, must still be allowed.
    _mock_github_response(monkeypatch, 200, size_kb=(400 * 1024) + 1)
    fake_queue = _mock_queue(monkeypatch)
    app.state.db_pool = pool
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"x-forwarded-for": "203.0.113.55"},
    ) as client:
        too_big = await client.post(
            "/v1/demo-scan", json={"repo_url": "https://github.com/torvalds/linux"}
        )
        assert too_big.status_code == 413

        _mock_github_response(monkeypatch, 200, size_kb=100)
        fits = await client.post(
            "/v1/demo-scan", json={"repo_url": "https://github.com/octocat/Hello-World"}
        )
    assert fits.status_code == 202
    assert fake_queue.enqueue.call_count == 1


@pytest.mark.asyncio
async def test_second_request_from_same_ip_within_cooldown_returns_429(pool, monkeypatch):
    _mock_github_response(monkeypatch, 200, size_kb=100)
    _mock_queue(monkeypatch)
    app.state.db_pool = pool
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"x-forwarded-for": "203.0.113.99"},
    ) as client:
        first = await client.post(
            "/v1/demo-scan", json={"repo_url": "https://github.com/octocat/Hello-World"}
        )
        second = await client.post(
            "/v1/demo-scan", json={"repo_url": "https://github.com/octocat/Spoon-Knife"}
        )
    assert first.status_code == 202
    assert second.status_code == 429


@pytest.mark.asyncio
async def test_different_ips_are_not_rate_limited_together(pool, monkeypatch):
    _mock_github_response(monkeypatch, 200, size_kb=100)
    _mock_queue(monkeypatch)
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/v1/demo-scan",
            json={"repo_url": "https://github.com/octocat/Hello-World"},
            headers={"x-forwarded-for": "203.0.113.1"},
        )
        second = await client.post(
            "/v1/demo-scan",
            json={"repo_url": "https://github.com/octocat/Hello-World"},
            headers={"x-forwarded-for": "203.0.113.2"},
        )
    assert first.status_code == 202
    assert second.status_code == 202


@pytest.mark.asyncio
async def test_queue_at_max_depth_returns_503(pool, monkeypatch):
    _mock_github_response(monkeypatch, 200, size_kb=100)
    _mock_queue(monkeypatch, count=3, started_count=1)
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/demo-scan", json={"repo_url": "https://github.com/octocat/Hello-World"}
        )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_get_status_404s_for_unknown_job(pool, monkeypatch):
    from rq.exceptions import NoSuchJobError

    def _raise(job_id, redis_url):
        raise NoSuchJobError()

    monkeypatch.setattr("app_server.demo_scan_api._fetch_job", _raise)
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/demo-scan/does-not-exist")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_status_404s_for_job_from_a_different_queue(pool, monkeypatch):
    # A public unauthenticated endpoint must not become a way to probe the
    # status/result of unrelated jobs (e.g. managed-audit jobs) sharing the
    # same Redis instance, just by guessing/reusing a job id.
    fake_job = MagicMock(origin="scans", func_name="scan_worker.jobs.run_managed_audit_api_job")
    monkeypatch.setattr("app_server.demo_scan_api._fetch_job", lambda job_id, redis_url: fake_job)
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/demo-scan/some-other-job")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_status_returns_result_when_finished(pool, monkeypatch):
    fake_job = MagicMock(
        origin="demo_scan",
        func_name="scan_worker.demo_scan.run_demo_scan_job",
        is_finished=True,
        is_failed=False,
        result={"summary": {"dead_code_count": 3}},
    )
    monkeypatch.setattr("app_server.demo_scan_api._fetch_job", lambda job_id, redis_url: fake_job)
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/demo-scan/demo-job-123")
    assert response.status_code == 200
    assert response.json() == {"status": "finished", "result": {"summary": {"dead_code_count": 3}}}


@pytest.mark.asyncio
async def test_get_status_returns_failed(pool, monkeypatch):
    fake_job = MagicMock(
        origin="demo_scan",
        func_name="scan_worker.demo_scan.run_demo_scan_job",
        is_finished=False,
        is_failed=True,
        exc_info=None,
    )
    monkeypatch.setattr("app_server.demo_scan_api._fetch_job", lambda job_id, redis_url: fake_job)
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/demo-scan/demo-job-123")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["detail"] == "scan failed - the repo may be invalid, private, or too large"


@pytest.mark.asyncio
async def test_get_status_surfaces_the_demo_scan_error_message(pool, monkeypatch):
    # A public visitor should see WHY their scan failed when it's our own
    # deliberate, user-facing DemoScanError (e.g. "too large for the live
    # demo, install the CLI") - not the generic catch-all message.
    exc_info = (
        "Traceback (most recent call last):\n"
        '  File "job.py", line 1, in run\n'
        "    raise DemoScanError(msg)\n"
        "scan_worker.demo_scan.DemoScanError: this repo needs more memory to scan "
        "than the live demo allows - install the free CLI (`pip install aletheore`)\n"
    )
    fake_job = MagicMock(
        origin="demo_scan",
        func_name="scan_worker.demo_scan.run_demo_scan_job",
        is_finished=False,
        is_failed=True,
        exc_info=exc_info,
    )
    monkeypatch.setattr("app_server.demo_scan_api._fetch_job", lambda job_id, redis_url: fake_job)
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/demo-scan/demo-job-123")
    assert response.status_code == 200
    detail = response.json()["detail"]
    assert "pip install aletheore" in detail
    assert "Traceback" not in detail


@pytest.mark.asyncio
async def test_get_status_never_leaks_an_unexpected_exception_to_the_visitor(pool, monkeypatch):
    # Any OTHER exception type (a real bug, not our own deliberate
    # DemoScanError) must fall back to the generic message - never show
    # internal implementation details on a public, unauthenticated endpoint.
    exc_info = (
        "Traceback (most recent call last):\n"
        '  File "job.py", line 42, in run\n'
        "    conn.execute(query)\n"
        "psycopg.OperationalError: connection to server failed: FATAL: password "
        "authentication failed for user \"internal_svc\"\n"
    )
    fake_job = MagicMock(
        origin="demo_scan",
        func_name="scan_worker.demo_scan.run_demo_scan_job",
        is_finished=False,
        is_failed=True,
        exc_info=exc_info,
    )
    monkeypatch.setattr("app_server.demo_scan_api._fetch_job", lambda job_id, redis_url: fake_job)
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/demo-scan/demo-job-123")
    assert response.status_code == 200
    detail = response.json()["detail"]
    assert detail == "scan failed - the repo may be invalid, private, or too large"
    assert "internal_svc" not in detail
    assert "psycopg" not in detail


@pytest.mark.asyncio
async def test_get_status_returns_pending_status_while_running(pool, monkeypatch):
    fake_job = MagicMock(
        origin="demo_scan",
        func_name="scan_worker.demo_scan.run_demo_scan_job",
        is_finished=False,
        is_failed=False,
    )
    fake_job.get_status.return_value = "started"
    monkeypatch.setattr("app_server.demo_scan_api._fetch_job", lambda job_id, redis_url: fake_job)
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/demo-scan/demo-job-123")
    assert response.status_code == 200
    assert response.json() == {"status": "started"}


@pytest.mark.asyncio
async def test_get_status_includes_queue_position_while_queued(pool, monkeypatch):
    # A visitor waiting behind someone else's scan must be told they're
    # queued (and roughly how long the wait is), not shown the same message
    # as the person actually being scanned.
    fake_job = MagicMock(
        origin="demo_scan",
        func_name="scan_worker.demo_scan.run_demo_scan_job",
        is_finished=False,
        is_failed=False,
    )
    fake_job.get_status.return_value = "queued"
    fake_queue = MagicMock()
    fake_queue.get_job_position.return_value = 2
    monkeypatch.setattr("app_server.demo_scan_api._fetch_job", lambda job_id, redis_url: fake_job)
    monkeypatch.setattr("app_server.demo_scan_api._get_queue", lambda redis_url: fake_queue)
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/demo-scan/demo-job-123")
    assert response.status_code == 200
    assert response.json() == {"status": "queued", "queue_position": 2}
    fake_queue.get_job_position.assert_called_once_with(fake_job.id)
