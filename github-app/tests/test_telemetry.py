import pytest
from httpx import ASGITransport, AsyncClient

from app_server.main import app


@pytest.mark.asyncio
async def test_report_telemetry_event_records_a_scan_event(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/telemetry", json={"event": "scan", "anonymous_id": "machine-abc-123"}
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_report_telemetry_event_rejects_unknown_event_type(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/telemetry", json={"event": "not-a-real-event", "anonymous_id": "machine-abc-123"}
        )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_report_telemetry_event_rejects_missing_anonymous_id(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/v1/telemetry", json={"event": "scan"})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_telemetry_stats_returns_404_when_token_not_configured(monkeypatch, pool):
    monkeypatch.delenv("INTERNAL_METRICS_TOKEN", raising=False)
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/internal/telemetry-stats")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_telemetry_stats_requires_bearer_token(monkeypatch, pool):
    monkeypatch.setenv("INTERNAL_METRICS_TOKEN", "secret-token")
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/internal/telemetry-stats")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_telemetry_stats_rejects_non_ascii_token_without_500(monkeypatch, pool):
    monkeypatch.setenv("INTERNAL_METRICS_TOKEN", "secret-token")
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/internal/telemetry-stats",
            headers=[(b"authorization", b"Bearer caf\xe9")],
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_telemetry_stats_returns_real_counts_for_valid_token(monkeypatch, pool):
    monkeypatch.setenv("INTERNAL_METRICS_TOKEN", "secret-token")
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/v1/telemetry", json={"event": "scan", "anonymous_id": "machine-a"})
        await client.post("/v1/telemetry", json={"event": "scan", "anonymous_id": "machine-a"})
        await client.post("/v1/telemetry", json={"event": "scan", "anonymous_id": "machine-b"})

        response = await client.get(
            "/v1/internal/telemetry-stats", headers={"Authorization": "Bearer secret-token"}
        )

    assert response.status_code == 200
    assert response.json() == {"total": 3, "unique_machines": 2}
