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

    fake_queue = MagicMock()
    fake_queue.count = 3
    monkeypatch.setattr("app_server.metrics.Redis.from_url", lambda url: MagicMock())
    monkeypatch.setattr("app_server.metrics.Queue", lambda *a, **k: fake_queue)
    monkeypatch.setattr(
        "app_server.metrics.StartedJobRegistry", lambda *a, **k: MagicMock(count=1)
    )
    monkeypatch.setattr(
        "app_server.metrics.FailedJobRegistry", lambda *a, **k: MagicMock(count=2)
    )
    monkeypatch.setattr(
        "app_server.metrics.FinishedJobRegistry", lambda *a, **k: MagicMock(count=4)
    )
    fake_worker = MagicMock()
    fake_worker.count = MagicMock(return_value=5)
    monkeypatch.setattr("app_server.metrics.Worker", fake_worker)

    app.state.db_pool = object()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/internal/queue-stats", headers={"Authorization": "Bearer secret-token"}
        )

    assert response.status_code == 200
    assert response.json() == {
        "queue_depth": 3,
        "started_count": 1,
        "failed_count": 2,
        "finished_count": 4,
        "worker_count": 5,
    }
