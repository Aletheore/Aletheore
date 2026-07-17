from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app_server.auth import encrypt_access_token, sign_session_id
from app_server.db import (
    create_session,
    get_max_tokens,
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


async def _mock_github_installations(monkeypatch, installation_ids: list[int]):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "total_count": len(installation_ids),
                "installations": [{"id": installation_id} for installation_id in installation_ids],
            },
        )

    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )


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


@pytest.mark.asyncio
async def test_set_health_check_config(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, installation_id=100)
    async with client:
        response = await client.put(
            "/admin/octocat/hello-world/health-check-url",
            json={
                "health_check_base_url": "https://api.example.com",
                "health_check_latency_threshold_ms": 3000,
            },
        )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_set_health_check_config_requires_login(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/admin/octocat/hello-world/health-check-url",
            json={
                "health_check_base_url": "https://api.example.com",
                "health_check_latency_threshold_ms": None,
            },
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_my_installations_returns_only_paid_and_administered(pool, monkeypatch):
    await upsert_installation(pool, 100, "acme")
    await set_installation_plan(pool, 100, "pro")
    await upsert_installation(pool, 200, "free-org")
    await set_installation_plan(pool, 200, "free")
    await upsert_installation(pool, 300, "not-mine")
    await set_installation_plan(pool, 300, "pro")
    await _mock_github_installations(monkeypatch, [100, 200])

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/my-installations",
            headers={"Authorization": "Bearer gho_faketoken"},
        )

    assert response.status_code == 200
    installations = response.json()["installations"]
    assert [installation["installation_id"] for installation in installations] == [100]
    assert installations[0]["account_login"] == "acme"


@pytest.mark.asyncio
async def test_my_installations_requires_bearer_token(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/my-installations")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_cli_token_mints_token_for_administered_paid_installation(pool, monkeypatch):
    await upsert_installation(pool, 100, "acme")
    await set_installation_plan(pool, 100, "pro")
    await _mock_github_installations(monkeypatch, [100])

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/cli-tokens",
            json={"installation_id": 100, "label": "laptop (device flow)"},
            headers={"Authorization": "Bearer gho_faketoken"},
        )

    assert response.status_code == 200
    assert len(response.json()["token"]) > 20


@pytest.mark.asyncio
async def test_create_cli_token_rejects_unadministered_installation(pool, monkeypatch):
    await upsert_installation(pool, 100, "acme")
    await set_installation_plan(pool, 100, "pro")
    await _mock_github_installations(monkeypatch, [999])

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/cli-tokens",
            json={"installation_id": 100, "label": "x"},
            headers={"Authorization": "Bearer gho_faketoken"},
        )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_create_cli_token_rejects_free_plan(pool, monkeypatch):
    await upsert_installation(pool, 100, "acme")
    await set_installation_plan(pool, 100, "free")
    await _mock_github_installations(monkeypatch, [100])

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/cli-tokens",
            json={"installation_id": 100, "label": "x"},
            headers={"Authorization": "Bearer gho_faketoken"},
        )

    assert response.status_code == 402


@pytest.mark.asyncio
async def test_create_cli_token_enforces_seat_cap(pool, monkeypatch):
    await upsert_installation(pool, 100, "acme")
    await set_installation_plan(pool, 100, "pro")
    await _mock_github_installations(monkeypatch, [100])

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    max_tokens = await get_max_tokens(pool, 100)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for i in range(max_tokens):
            response = await client.post(
                "/v1/cli-tokens",
                json={"installation_id": 100, "label": f"token-{i}"},
                headers={"Authorization": "Bearer gho_faketoken"},
            )
            assert response.status_code == 200
        over_limit = await client.post(
            "/v1/cli-tokens",
            json={"installation_id": 100, "label": "one-too-many"},
            headers={"Authorization": "Bearer gho_faketoken"},
        )

    assert over_limit.status_code == 409


@pytest.mark.asyncio
async def test_create_cli_token_requires_bearer_token(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/v1/cli-tokens", json={"installation_id": 100, "label": "x"})
    assert response.status_code == 401
