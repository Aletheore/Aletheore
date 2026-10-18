import httpx
import pytest
import pytest_asyncio
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


@pytest_asyncio.fixture
async def test_app(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _mint_personal_token_header(test_app, monkeypatch, github_login: str, github_user_id: int) -> dict:
    # Mints a real personal token through the real, already-tested
    # POST /v1/personal-tokens flow (see test_personal_token_endpoint_mints_
    # for_new_identity above) rather than inserting a token/identity row
    # directly - this exercises the actual auth path the new endpoints rely
    # on, the same way a real CLI login would.
    _mock_github_user(monkeypatch, github_login=github_login, github_user_id=github_user_id)
    response = await test_app.post(
        "/v1/personal-tokens",
        headers={"Authorization": "Bearer fake-github-token"},
        json={"label": "test"},
    )
    assert response.status_code == 200
    raw_token = response.json()["token"]
    return {"Authorization": f"Bearer {raw_token}"}


@pytest_asyncio.fixture
async def personal_token_header(test_app, monkeypatch):
    return await _mint_personal_token_header(test_app, monkeypatch, github_login="octocat", github_user_id=12345)


@pytest_asyncio.fixture
async def other_personal_token_header(test_app, monkeypatch):
    return await _mint_personal_token_header(test_app, monkeypatch, github_login="other-user", github_user_id=99999)


@pytest.mark.asyncio
async def test_put_then_list_custom_skill(test_app, personal_token_header):
    await test_app.put("/v1/custom-skills/my-review", headers=personal_token_header, json={"content": "steps"})
    response = await test_app.get("/v1/custom-skills", headers=personal_token_header)
    assert response.json()["skills"][0]["name"] == "my-review"


@pytest.mark.asyncio
async def test_put_upserts_same_name(test_app, personal_token_header):
    await test_app.put("/v1/custom-skills/my-review", headers=personal_token_header, json={"content": "v1"})
    await test_app.put("/v1/custom-skills/my-review", headers=personal_token_header, json={"content": "v2"})
    response = await test_app.get("/v1/custom-skills/my-review", headers=personal_token_header)
    assert response.json()["content"] == "v2"


@pytest.mark.asyncio
async def test_put_upserts_same_name_does_not_duplicate_row(test_app, personal_token_header, pool):
    await test_app.put("/v1/custom-skills/my-review", headers=personal_token_header, json={"content": "v1"})
    await test_app.put("/v1/custom-skills/my-review", headers=personal_token_header, json={"content": "v2"})
    count = await pool.fetchval(
        "SELECT count(*) FROM custom_skills WHERE skill_name = 'my-review'"
    )
    assert count == 1


@pytest.mark.asyncio
async def test_get_custom_skill_not_found_404(test_app, personal_token_header):
    response = await test_app.get("/v1/custom-skills/nope", headers=personal_token_header)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_custom_skills_scoped_to_caller_identity(test_app, personal_token_header, other_personal_token_header):
    await test_app.put("/v1/custom-skills/mine", headers=personal_token_header, json={"content": "x"})
    response = await test_app.get("/v1/custom-skills/mine", headers=other_personal_token_header)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_custom_skills_scoped_to_caller_identity(test_app, personal_token_header, other_personal_token_header):
    await test_app.put("/v1/custom-skills/mine", headers=personal_token_header, json={"content": "x"})
    response = await test_app.get("/v1/custom-skills", headers=other_personal_token_header)
    assert response.json()["skills"] == []


@pytest.mark.asyncio
async def test_custom_skills_endpoints_require_bearer_401(test_app):
    list_response = await test_app.get("/v1/custom-skills")
    get_response = await test_app.get("/v1/custom-skills/whatever")
    put_response = await test_app.put("/v1/custom-skills/whatever", json={"content": "x"})

    assert list_response.status_code == 401
    assert get_response.status_code == 401
    assert put_response.status_code == 401
