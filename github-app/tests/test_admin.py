from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app_server.auth import encrypt_access_token, sign_session_id
from app_server.db import (
    create_session,
    insert_repo_history,
    set_installation_plan,
    upsert_installation,
)
from app_server.main import app


async def _logged_in_client(pool, monkeypatch, installation_id=100, plan="pro"):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    await upsert_installation(pool, installation_id, "octocat")
    await set_installation_plan(pool, installation_id, plan)
    await insert_repo_history(
        pool,
        installation_id,
        "octocat/hello-world",
        datetime.now(timezone.utc),
        {"scanned_at": "x"},
    )
    await create_session(
        pool,
        "sess-1",
        42,
        "octocat",
        encrypt_access_token("gho_faketoken", "test-session-secret"),
        datetime.now(timezone.utc) + timedelta(hours=1),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"total_count": 1, "installations": [{"id": installation_id}]},
        )

    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )

    app.state.db_pool = pool
    signed = sign_session_id("sess-1", "test-session-secret")
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test", cookies={"session": signed})


@pytest.mark.asyncio
async def test_admin_page_requires_login(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/admin/octocat/hello-world")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_admin_page_rejects_free_plan(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, plan="free")
    async with client:
        response = await client.get("/admin/octocat/hello-world")
    assert response.status_code == 402


@pytest.mark.asyncio
async def test_generate_token_returns_raw_value_once(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.post("/admin/octocat/hello-world/tokens", json={"label": "laptop"})
    assert response.status_code == 200
    assert len(response.json()["token"]) > 20


@pytest.mark.asyncio
async def test_set_webhook_url(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.put(
            "/admin/octocat/hello-world/webhook-url",
            json={"webhook_url": "https://hooks.slack.com/services/x"},
        )
    assert response.status_code == 200
