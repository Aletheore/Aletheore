from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app_server.db import insert_repo_history, upsert_installation
from app_server.main import app


@pytest.mark.asyncio
async def test_dashboard_returns_404_for_unknown_repo(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/app/octocat/nonexistent")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_dashboard_returns_data_for_known_repo(pool):
    await upsert_installation(pool, 1, "octocat")
    await insert_repo_history(
        pool,
        1,
        "octocat/hello-world",
        datetime.now(timezone.utc),
        {"repository": {"modules": []}},
    )
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/app/octocat/hello-world")
    assert response.status_code == 200
    body = response.json()
    assert body["repo_full_name"] == "octocat/hello-world"
    assert len(body["history"]) == 1


@pytest.mark.asyncio
async def test_public_health_returns_latest_per_endpoint(pool):
    await upsert_installation(pool, 500, "octocat")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO endpoint_health
                (installation_id, repo_full_name, endpoint_method, endpoint_path,
                 reachable, status_code, latency_ms, checked_at)
            VALUES
                (500, 'octocat/hello-world', 'GET', '/api/users', true, 200, 90.5, now() - interval '1 minute'),
                (500, 'octocat/hello-world', 'GET', '/api/users', true, 200, 88.0, now()),
                (500, 'octocat/hello-world', 'GET', '/api/orders', false, NULL, 5000.0, now())
            """
        )

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/health/octocat/hello-world")

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    body = response.json()
    assert body["repo_full_name"] == "octocat/hello-world"
    endpoints = {(endpoint["method"], endpoint["path"]): endpoint for endpoint in body["endpoints"]}
    assert len(endpoints) == 2
    assert endpoints[("GET", "/api/users")]["latency_ms"] == 88.0
    assert endpoints[("GET", "/api/orders")]["reachable"] is False
    assert endpoints[("GET", "/api/orders")]["status_code"] is None


@pytest.mark.asyncio
async def test_public_health_404s_with_no_data(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/health/octocat/no-such-repo")
    assert response.status_code == 404
    assert response.headers["access-control-allow-origin"] == "*"
