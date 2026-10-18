import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app_server.main import app


def _mock_github_user(monkeypatch, github_login: str = "octocat", github_user_id: int = 12345):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": github_user_id, "login": github_login})

    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )


@pytest.mark.asyncio
async def test_personal_token_endpoint_mints_for_new_identity(pool, monkeypatch):
    _mock_github_user(monkeypatch)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/personal-tokens",
            headers={"Authorization": "Bearer fake-github-token"},
            json={"label": "my-laptop"},
        )

    assert response.status_code == 200
    assert "token" in response.json()
    row = await pool.fetchrow("SELECT * FROM personal_identities WHERE github_login = 'octocat'")
    assert row is not None


@pytest.mark.asyncio
async def test_personal_token_endpoint_upserts_existing_identity(pool, monkeypatch):
    _mock_github_user(monkeypatch)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/v1/personal-tokens",
            headers={"Authorization": "Bearer fake-github-token"},
            json={"label": "a"},
        )
        await client.post(
            "/v1/personal-tokens",
            headers={"Authorization": "Bearer fake-github-token"},
            json={"label": "b"},
        )

    count = await pool.fetchval("SELECT count(*) FROM personal_identities WHERE github_login = 'octocat'")
    assert count == 1


@pytest.mark.asyncio
async def test_personal_token_endpoint_missing_bearer_401(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/v1/personal-tokens", json={"label": "x"})

    assert response.status_code == 401
