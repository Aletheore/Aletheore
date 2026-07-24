from unittest.mock import MagicMock

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app_server.main import app


def _mock_github_response(monkeypatch, status_code: int, size_kb: int = 100):
    def fake_get(url, headers=None, timeout=None):
        return httpx.Response(status_code, json={"size": size_kb}, request=httpx.Request("GET", url))

    monkeypatch.setattr("app_server.demo_scan_api.httpx.get", fake_get)


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
    )
    monkeypatch.setattr("app_server.demo_scan_api._fetch_job", lambda job_id, redis_url: fake_job)
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/demo-scan/demo-job-123")
    assert response.status_code == 200
    assert response.json()["status"] == "failed"


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
