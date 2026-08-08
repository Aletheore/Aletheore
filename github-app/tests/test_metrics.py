from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app_server.main import app


@pytest.mark.asyncio
async def test_queue_stats_returns_404_when_token_not_configured(monkeypatch):
    monkeypatch.delenv("INTERNAL_METRICS_TOKEN", raising=False)
    app.state.db_pool = object()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/internal/queue-stats")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_queue_stats_requires_bearer_token(monkeypatch):
    monkeypatch.setenv("INTERNAL_METRICS_TOKEN", "secret-token")
    app.state.db_pool = object()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/internal/queue-stats")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_queue_stats_rejects_wrong_token(monkeypatch):
    monkeypatch.setenv("INTERNAL_METRICS_TOKEN", "secret-token")
    app.state.db_pool = object()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/internal/queue-stats", headers={"Authorization": "Bearer wrong-token"}
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_queue_stats_returns_counts_for_valid_token(monkeypatch):
    monkeypatch.setenv("INTERNAL_METRICS_TOKEN", "secret-token")

    # "scans" and "health" are separate queues (see scan_worker/scheduler.py)
    # - this test uses distinct counts per queue to prove the response
    # actually breaks stats down per queue rather than reporting "scans"
    # twice under two names.
    fake_queues = {}

    def _fake_queue(name, **kwargs):
        q = MagicMock()
        q.count = {"scans": 3, "health": 1}[name]
        fake_queues[name] = q
        return q

    def _by_queue(counts):
        def _factory(*, queue):
            name = next(n for n, q in fake_queues.items() if q is queue)
            return MagicMock(count=counts[name])

        return _factory

    monkeypatch.setattr("app_server.metrics.Redis.from_url", lambda url: MagicMock())
    monkeypatch.setattr("app_server.metrics.Queue", _fake_queue)
    monkeypatch.setattr(
        "app_server.metrics.StartedJobRegistry", _by_queue({"scans": 1, "health": 0})
    )
    monkeypatch.setattr(
        "app_server.metrics.FailedJobRegistry", _by_queue({"scans": 2, "health": 0})
    )
    monkeypatch.setattr(
        "app_server.metrics.FinishedJobRegistry", _by_queue({"scans": 4, "health": 2})
    )

    def _worker_count(connection=None, queue=None):
        if queue is None:
            return 6
        name = next(n for n, q in fake_queues.items() if q is queue)
        return {"scans": 5, "health": 1}[name]

    fake_worker = MagicMock()
    fake_worker.count = _worker_count
    monkeypatch.setattr("app_server.metrics.Worker", fake_worker)

    app.state.db_pool = object()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/internal/queue-stats", headers={"Authorization": "Bearer secret-token"}
        )

    assert response.status_code == 200
    assert response.json() == {
        "queues": {
            "scans": {
                "queue_depth": 3,
                "started_count": 1,
                "failed_count": 2,
                "finished_count": 4,
                "worker_count": 5,
            },
            "health": {
                "queue_depth": 1,
                "started_count": 0,
                "failed_count": 0,
                "finished_count": 2,
                "worker_count": 1,
            },
        },
        "worker_count": 6,
    }
