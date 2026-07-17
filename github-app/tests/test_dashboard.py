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
