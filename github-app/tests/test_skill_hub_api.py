import hashlib

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


# --- abuse limits (final Skill Hub review) ---------------------------------


@pytest.mark.asyncio
async def test_personal_token_mint_rotates_oldest_token_on_overflow(test_app, monkeypatch, pool):
    # 2026-10-18 fix: `aletheore login` mints a brand new personal token on
    # every login and nothing ever revokes one (no revoke endpoint exists),
    # so cap-and-reject-at-10 permanently locked out any identity that had
    # logged in MAX_PERSONAL_TOKENS_PER_IDENTITY+ times, with no recovery
    # path. The (limit+1)th mint (and every one after it) must now succeed
    # - never a 409 - by rotating out the single oldest token instead.
    from app_server.db import MAX_PERSONAL_TOKENS_PER_IDENTITY

    _mock_github_user(monkeypatch)
    statuses = []
    for i in range(MAX_PERSONAL_TOKENS_PER_IDENTITY + 1):
        response = await test_app.post(
            "/v1/personal-tokens",
            headers={"Authorization": "Bearer fake-github-token"},
            json={"label": f"machine-{i}"},
        )
        statuses.append(response.status_code)

    assert statuses == [200] * (MAX_PERSONAL_TOKENS_PER_IDENTITY + 1)
    count = await pool.fetchval("SELECT count(*) FROM personal_api_tokens WHERE revoked_at IS NULL")
    assert count == MAX_PERSONAL_TOKENS_PER_IDENTITY


@pytest.mark.asyncio
async def test_personal_token_relogin_below_the_cap_still_works(test_app, monkeypatch, pool):
    from app_server.db import MAX_PERSONAL_TOKENS_PER_IDENTITY

    _mock_github_user(monkeypatch)
    for i in range(MAX_PERSONAL_TOKENS_PER_IDENTITY - 1):
        await test_app.post(
            "/v1/personal-tokens",
            headers={"Authorization": "Bearer fake-github-token"},
            json={"label": f"machine-{i}"},
        )

    response = await test_app.post(
        "/v1/personal-tokens",
        headers={"Authorization": "Bearer fake-github-token"},
        json={"label": "one-more-login"},
    )

    assert response.status_code == 200
    # And the freshly minted token actually authenticates.
    list_response = await test_app.get(
        "/v1/custom-skills", headers={"Authorization": f"Bearer {response.json()['token']}"}
    )
    assert list_response.status_code == 200


@pytest.mark.asyncio
async def test_personal_token_evicted_on_rotation_no_longer_authenticates(test_app, monkeypatch, pool):
    from app_server.db import MAX_PERSONAL_TOKENS_PER_IDENTITY

    _mock_github_user(monkeypatch)
    first_response = await test_app.post(
        "/v1/personal-tokens",
        headers={"Authorization": "Bearer fake-github-token"},
        json={"label": "machine-0"},
    )
    oldest_raw_token = first_response.json()["token"]
    oldest_token_hash = hashlib.sha256(oldest_raw_token.encode()).hexdigest()

    # Backdate the first-minted token so it is unambiguously the oldest by
    # created_at, independent of how close together these requests land.
    await pool.execute(
        "UPDATE personal_api_tokens SET created_at = now() - interval '1 hour' WHERE token_hash = $1",
        oldest_token_hash,
    )

    # It authenticates fine before the overflow mint.
    still_valid = await test_app.get(
        "/v1/custom-skills", headers={"Authorization": f"Bearer {oldest_raw_token}"}
    )
    assert still_valid.status_code == 200

    for i in range(1, MAX_PERSONAL_TOKENS_PER_IDENTITY):
        await test_app.post(
            "/v1/personal-tokens",
            headers={"Authorization": "Bearer fake-github-token"},
            json={"label": f"machine-{i}"},
        )

    # This is the (limit+1)th mint for this identity; it must rotate out the
    # backdated oldest token to make room.
    overflow_response = await test_app.post(
        "/v1/personal-tokens",
        headers={"Authorization": "Bearer fake-github-token"},
        json={"label": "machine-overflow"},
    )
    assert overflow_response.status_code == 200

    response = await test_app.get(
        "/v1/custom-skills", headers={"Authorization": f"Bearer {oldest_raw_token}"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_personal_token_cap_is_per_identity_not_global(test_app, monkeypatch):
    from app_server.db import MAX_PERSONAL_TOKENS_PER_IDENTITY

    _mock_github_user(monkeypatch, github_login="octocat", github_user_id=12345)
    for _ in range(MAX_PERSONAL_TOKENS_PER_IDENTITY):
        await test_app.post(
            "/v1/personal-tokens", headers={"Authorization": "Bearer fake-github-token"}, json={"label": "x"}
        )

    _mock_github_user(monkeypatch, github_login="other-user", github_user_id=99999)
    response = await test_app.post(
        "/v1/personal-tokens", headers={"Authorization": "Bearer fake-github-token"}, json={"label": "x"}
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_personal_token_mint_rate_limited_per_github_account(test_app, monkeypatch, redis_conn):
    # Mirrors test_auth.py's test_login_rate_limits_after_threshold: swap the
    # real limiter back in (conftest stubs it out suite-wide), point it at
    # the test Redis, and shrink the limit.
    import app_server.skill_hub_api as skill_hub_api_module
    from app_server.rate_limit import is_rate_limited as real_is_rate_limited

    monkeypatch.setattr(skill_hub_api_module, "is_rate_limited", real_is_rate_limited)
    monkeypatch.setattr(skill_hub_api_module, "PERSONAL_TOKEN_RATE_LIMIT", 2)
    monkeypatch.setattr(skill_hub_api_module, "get_redis_client", lambda: redis_conn)

    _mock_github_user(monkeypatch)
    statuses = []
    for _ in range(3):
        response = await test_app.post(
            "/v1/personal-tokens", headers={"Authorization": "Bearer fake-github-token"}, json={"label": "x"}
        )
        statuses.append(response.status_code)

    assert statuses == [200, 200, 429]

    # Keyed on the GitHub user id, not shared across accounts.
    _mock_github_user(monkeypatch, github_login="other-user", github_user_id=99999)
    other = await test_app.post(
        "/v1/personal-tokens", headers={"Authorization": "Bearer fake-github-token"}, json={"label": "x"}
    )
    assert other.status_code == 200


@pytest.mark.asyncio
async def test_put_custom_skill_rejects_oversized_content(test_app, personal_token_header, pool):
    from app_server.skill_hub_api import MAX_CUSTOM_SKILL_CONTENT_CHARS

    response = await test_app.put(
        "/v1/custom-skills/big",
        headers=personal_token_header,
        json={"content": "x" * (MAX_CUSTOM_SKILL_CONTENT_CHARS + 1)},
    )

    assert response.status_code == 422
    assert await pool.fetchval("SELECT count(*) FROM custom_skills") == 0


@pytest.mark.asyncio
async def test_put_custom_skill_accepts_content_at_the_cap(test_app, personal_token_header):
    from app_server.skill_hub_api import MAX_CUSTOM_SKILL_CONTENT_CHARS

    response = await test_app.put(
        "/v1/custom-skills/big",
        headers=personal_token_header,
        json={"content": "x" * MAX_CUSTOM_SKILL_CONTENT_CHARS},
    )

    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_name",
    # A literal ".." segment is normalized away by the HTTP client before it
    # is ever sent; "%2e%2e" is how one actually reaches the route.
    ["has.dot", "white space", "semi;colon", "a" * 129, "%2e%2e", "caf%C3%A9"],
)
async def test_put_custom_skill_rejects_invalid_name(test_app, personal_token_header, pool, bad_name):
    response = await test_app.put(
        f"/v1/custom-skills/{bad_name}", headers=personal_token_header, json={"content": "x"}
    )

    assert response.status_code == 400
    assert await pool.fetchval("SELECT count(*) FROM custom_skills") == 0


@pytest.mark.asyncio
async def test_put_custom_skill_accepts_a_max_length_safe_name(test_app, personal_token_header):
    response = await test_app.put(
        f"/v1/custom-skills/{'a' * 128}", headers=personal_token_header, json={"content": "x"}
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_delete_custom_skill_removes_it(test_app, personal_token_header):
    await test_app.put("/v1/custom-skills/my-review", headers=personal_token_header, json={"content": "x"})
    response = await test_app.delete("/v1/custom-skills/my-review", headers=personal_token_header)
    assert response.status_code == 204
    get_response = await test_app.get("/v1/custom-skills/my-review", headers=personal_token_header)
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_delete_custom_skill_unknown_name_404(test_app, personal_token_header):
    response = await test_app.delete("/v1/custom-skills/never-added", headers=personal_token_header)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_custom_skill_scoped_to_caller_identity(test_app, personal_token_header, other_personal_token_header):
    await test_app.put("/v1/custom-skills/mine", headers=personal_token_header, json={"content": "x"})
    response = await test_app.delete("/v1/custom-skills/mine", headers=other_personal_token_header)
    assert response.status_code == 404
    # Still there for the real owner.
    get_response = await test_app.get("/v1/custom-skills/mine", headers=personal_token_header)
    assert get_response.status_code == 200


@pytest.mark.asyncio
async def test_delete_custom_skill_leaves_same_name_for_other_identity(test_app, personal_token_header, other_personal_token_header):
    await test_app.put("/v1/custom-skills/shared-name", headers=personal_token_header, json={"content": "mine"})
    await test_app.put("/v1/custom-skills/shared-name", headers=other_personal_token_header, json={"content": "theirs"})
    response = await test_app.delete("/v1/custom-skills/shared-name", headers=personal_token_header)
    assert response.status_code == 204
    other_response = await test_app.get("/v1/custom-skills/shared-name", headers=other_personal_token_header)
    assert other_response.status_code == 200
    assert other_response.json()["content"] == "theirs"


@pytest.mark.asyncio
async def test_delete_custom_skill_is_idempotent(test_app, personal_token_header):
    await test_app.put("/v1/custom-skills/my-review", headers=personal_token_header, json={"content": "x"})
    await test_app.delete("/v1/custom-skills/my-review", headers=personal_token_header)
    second = await test_app.delete("/v1/custom-skills/my-review", headers=personal_token_header)
    assert second.status_code == 404  # already gone, not a 500
