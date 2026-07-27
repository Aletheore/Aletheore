import socket
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app_server.admin import _administered_installation_ids_for_session_or_401
from app_server.auth import decrypt_access_token, encrypt_access_token, sign_session_id
from app_server.db import (
    create_session,
    get_max_tokens,
    get_session,
    insert_repo_history,
    set_installation_plan,
    upsert_installation,
)
from app_server.main import app


async def _logged_in_client(pool, monkeypatch, installation_id=100, plan="air"):
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
    # Deterministic, network-free DNS answer for URL-validation tests that
    # don't care about SSRF behavior specifically - a real public address.
    monkeypatch.setattr(
        "app_server.url_validation.socket.getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
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


async def _create_session_with_tokens(
    pool, monkeypatch, session_id, access_token, refresh_token=None
):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    await create_session(
        pool,
        session_id,
        42,
        "octocat",
        encrypt_access_token(access_token, "test-session-secret"),
        datetime.now(timezone.utc) + timedelta(hours=1),
        refresh_token=encrypt_access_token(refresh_token, "test-session-secret") if refresh_token else None,
    )
    return {
        "id": session_id,
        "github_login": "octocat",
        "github_access_token": access_token,
        "github_refresh_token": refresh_token,
    }


@pytest.mark.asyncio
async def test_administered_ids_for_session_succeeds_without_refresh_when_token_valid(pool, monkeypatch):
    session = await _create_session_with_tokens(pool, monkeypatch, "sess-valid", "gho_valid")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer gho_valid"
        return httpx.Response(200, json={"installations": [{"id": 1}, {"id": 2}]})

    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )

    ids = await _administered_installation_ids_for_session_or_401(pool, session)
    assert ids == {1, 2}
    # Untouched - no refresh was needed.
    row = await get_session(pool, "sess-valid")
    assert decrypt_access_token(row["github_access_token"], "test-session-secret") == "gho_valid"


@pytest.mark.asyncio
async def test_administered_ids_for_session_refreshes_and_retries_on_401(pool, monkeypatch):
    session = await _create_session_with_tokens(
        pool, monkeypatch, "sess-refresh", "gho_dead", refresh_token="ghr_stillgood"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["Authorization"] == "Bearer gho_dead":
            return httpx.Response(401, json={"message": "Bad credentials"})
        if request.headers["Authorization"] == "Bearer gho_fresh":
            return httpx.Response(200, json={"installations": [{"id": 7}]})
        raise AssertionError("unexpected token")

    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )

    def fake_refresh(refresh_token, client_id, client_secret):
        assert refresh_token == "ghr_stillgood"
        return "gho_fresh", "ghr_rotated"

    monkeypatch.setattr("app_server.admin.refresh_github_access_token", fake_refresh)

    ids = await _administered_installation_ids_for_session_or_401(pool, session)
    assert ids == {7}

    row = await get_session(pool, "sess-refresh")
    assert decrypt_access_token(row["github_access_token"], "test-session-secret") == "gho_fresh"
    assert decrypt_access_token(row["github_refresh_token"], "test-session-secret") == "ghr_rotated"


@pytest.mark.asyncio
async def test_administered_ids_for_session_keeps_old_refresh_token_if_github_omits_a_new_one(
    pool, monkeypatch
):
    session = await _create_session_with_tokens(
        pool, monkeypatch, "sess-norotate", "gho_dead", refresh_token="ghr_stillgood"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["Authorization"] == "Bearer gho_dead":
            return httpx.Response(401, json={"message": "Bad credentials"})
        return httpx.Response(200, json={"installations": [{"id": 7}]})

    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )
    monkeypatch.setattr(
        "app_server.admin.refresh_github_access_token",
        lambda refresh_token, client_id, client_secret: ("gho_fresh", None),
    )

    await _administered_installation_ids_for_session_or_401(pool, session)

    row = await get_session(pool, "sess-norotate")
    assert decrypt_access_token(row["github_refresh_token"], "test-session-secret") == "ghr_stillgood"


@pytest.mark.asyncio
async def test_administered_ids_for_session_deletes_session_when_no_refresh_token(pool, monkeypatch):
    session = await _create_session_with_tokens(pool, monkeypatch, "sess-norefresh", "gho_dead")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )

    with pytest.raises(HTTPException) as exc_info:
        await _administered_installation_ids_for_session_or_401(pool, session)
    assert exc_info.value.status_code == 401
    assert await get_session(pool, "sess-norefresh") is None


@pytest.mark.asyncio
async def test_administered_ids_for_session_deletes_session_when_refresh_fails(pool, monkeypatch, caplog):
    session = await _create_session_with_tokens(
        pool, monkeypatch, "sess-refreshfails", "gho_dead", refresh_token="ghr_alsodead"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )

    def fake_refresh(refresh_token, client_id, client_secret):
        raise RuntimeError("bad_refresh_token")

    monkeypatch.setattr("app_server.admin.refresh_github_access_token", fake_refresh)

    # Before this fix, a failed refresh attempt (dead refresh_token, GitHub
    # outage, unexpected response shape) was swallowed with a bare `except
    # Exception: return None` - nothing distinguished it in logs from the
    # unremarkable "no refresh_token on file" case.
    with caplog.at_level("WARNING", logger="app_server.admin"):
        with pytest.raises(HTTPException) as exc_info:
            await _administered_installation_ids_for_session_or_401(pool, session)
    assert exc_info.value.status_code == 401
    assert await get_session(pool, "sess-refreshfails") is None
    assert any("token refresh failed" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_administered_ids_for_session_propagates_502_without_deleting_session(pool, monkeypatch):
    session = await _create_session_with_tokens(pool, monkeypatch, "sess-outage", "gho_valid")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "internal error"})

    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )

    with pytest.raises(HTTPException) as exc_info:
        await _administered_installation_ids_for_session_or_401(pool, session)
    assert exc_info.value.status_code == 502
    assert await get_session(pool, "sess-outage") is not None


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
async def test_generate_token_returns_422_for_missing_label(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.post("/admin/octocat/hello-world/tokens", json={})
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_label",
    ["", "x" * 101, "bad\nlabel", "bad\ttab", "bad\x00null"],
)
async def test_generate_token_rejects_invalid_labels(pool, monkeypatch, bad_label):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.post("/admin/octocat/hello-world/tokens", json={"label": bad_label})
    assert response.status_code == 422


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
async def test_add_health_check_target(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, installation_id=100)
    async with client:
        response = await client.post(
            "/admin/octocat/hello-world/health-targets",
            json={
                "label": "Production",
                "base_url": "https://api.example.com",
                "latency_threshold_ms": 3000,
            },
        )
    assert response.status_code == 200
    assert "id" in response.json()


@pytest.mark.asyncio
async def test_add_health_check_target_returns_422_for_non_integer_threshold(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, installation_id=100)
    async with client:
        response = await client.post(
            "/admin/octocat/hello-world/health-targets",
            json={
                "label": "Production",
                "base_url": "https://api.example.com",
                "latency_threshold_ms": "not-a-number",
            },
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_add_health_check_target_enforces_plan_limit(pool, monkeypatch):
    # Pro's included limit is 5 (INCLUDED_HEALTH_CHECK_TARGETS) - filling
    # it up, then the 6th add should be rejected.
    client = await _logged_in_client(pool, monkeypatch, installation_id=100, plan="air")
    async with client:
        for i in range(5):
            response = await client.post(
                "/admin/octocat/hello-world/health-targets",
                json={"label": f"Target {i}", "base_url": f"https://api{i}.example.com", "latency_threshold_ms": None},
            )
            assert response.status_code == 200
        sixth = await client.post(
            "/admin/octocat/hello-world/health-targets",
            json={"label": "One too many", "base_url": "https://api6.example.com", "latency_threshold_ms": None},
        )
    assert sixth.status_code == 409
    assert "limit reached" in sixth.json()["detail"]


@pytest.mark.asyncio
async def test_add_health_check_target_rejects_internal_address(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, installation_id=100)
    monkeypatch.setattr(
        "app_server.url_validation.socket.getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))],
    )
    async with client:
        response = await client.post(
            "/admin/octocat/hello-world/health-targets",
            json={"label": "Internal", "base_url": "https://internal-service.local", "latency_threshold_ms": 3000},
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_add_health_check_target_requires_login(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/admin/octocat/hello-world/health-targets",
            json={"label": "Production", "base_url": "https://api.example.com", "latency_threshold_ms": None},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_remove_health_check_target(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, installation_id=100)
    async with client:
        add_response = await client.post(
            "/admin/octocat/hello-world/health-targets",
            json={"label": "Production", "base_url": "https://api.example.com", "latency_threshold_ms": None},
        )
        target_id = add_response.json()["id"]
        remove_response = await client.delete(f"/admin/octocat/hello-world/health-targets/{target_id}")
        page = await client.get("/admin/octocat/hello-world")
    assert remove_response.status_code == 200
    assert page.json()["health_targets"] == []


@pytest.mark.asyncio
async def test_set_webhook_url_rejects_internal_address(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    monkeypatch.setattr(
        "app_server.url_validation.socket.getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))],
    )
    async with client:
        response = await client.put(
            "/admin/octocat/hello-world/webhook-url",
            json={"webhook_url": "https://metadata.internal/latest/meta-data"},
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_set_webhook_url_rejects_non_https(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.put(
            "/admin/octocat/hello-world/webhook-url",
            json={"webhook_url": "http://hooks.slack.com/services/x"},
        )
    assert response.status_code == 400




@pytest.mark.asyncio
async def test_my_installations_returns_only_paid_and_administered(pool, monkeypatch):
    await upsert_installation(pool, 100, "acme")
    await set_installation_plan(pool, 100, "indie")
    await upsert_installation(pool, 200, "free-org")
    await set_installation_plan(pool, 200, "free")
    await upsert_installation(pool, 300, "not-mine")
    await set_installation_plan(pool, 300, "indie")
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
    await set_installation_plan(pool, 100, "indie")
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
    await set_installation_plan(pool, 100, "indie")
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
    await set_installation_plan(pool, 100, "indie")
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
async def test_create_cli_token_returns_422_for_missing_fields(pool, monkeypatch):
    await upsert_installation(pool, 100, "acme")
    await set_installation_plan(pool, 100, "indie")
    await _mock_github_installations(monkeypatch, [100])

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/cli-tokens",
            json={"installation_id": 100},
            headers={"Authorization": "Bearer gho_faketoken"},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_cli_token_rejects_invalid_label(pool, monkeypatch):
    await upsert_installation(pool, 100, "acme")
    await set_installation_plan(pool, 100, "indie")
    await _mock_github_installations(monkeypatch, [100])

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/cli-tokens",
            json={"installation_id": 100, "label": "bad\nlabel"},
            headers={"Authorization": "Bearer gho_faketoken"},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_cli_token_requires_bearer_token(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/v1/cli-tokens", json={"installation_id": 100, "label": "x"})
    assert response.status_code == 401


async def _second_session_client(monkeypatch, github_user_id: int, login: str, session_id: str):
    from app_server.db import create_session

    pool = app.state.db_pool
    await create_session(
        pool,
        session_id,
        github_user_id,
        login,
        encrypt_access_token("gho_faketoken2", "test-session-secret"),
        datetime.now(timezone.utc) + timedelta(hours=1),
    )
    signed = sign_session_id(session_id, "test-session-secret")
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test", cookies={"session": signed})


@pytest.mark.asyncio
async def test_first_admin_to_arrive_is_auto_seated(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.get("/admin/octocat/hello-world")
    assert response.status_code == 200
    assert [m["github_login"] for m in response.json()["members"]] == ["octocat"]
    assert response.json()["seat_limit"] == 5


@pytest.mark.asyncio
async def test_second_github_admin_without_a_seat_is_rejected(pool, monkeypatch):
    first = await _logged_in_client(pool, monkeypatch)
    async with first:
        await first.get("/admin/octocat/hello-world")  # bootstraps octocat as seat one

    second = await _second_session_client(monkeypatch, 43, "alice", "sess-2")
    async with second:
        response = await second.get("/admin/octocat/hello-world")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_adding_a_member_grants_them_access(pool, monkeypatch):
    first = await _logged_in_client(pool, monkeypatch)
    async with first:
        await first.get("/admin/octocat/hello-world")
        add_response = await first.post("/admin/octocat/hello-world/members", json={"github_login": "alice"})
    assert add_response.status_code == 200

    second = await _second_session_client(monkeypatch, 43, "alice", "sess-2")
    async with second:
        response = await second.get("/admin/octocat/hello-world")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_removing_a_member_revokes_access(pool, monkeypatch):
    first = await _logged_in_client(pool, monkeypatch)
    async with first:
        await first.get("/admin/octocat/hello-world")
        await first.post("/admin/octocat/hello-world/members", json={"github_login": "alice"})
        remove_response = await first.delete("/admin/octocat/hello-world/members/alice")
    assert remove_response.status_code == 200

    second = await _second_session_client(monkeypatch, 43, "alice", "sess-2")
    async with second:
        response = await second.get("/admin/octocat/hello-world")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_add_member_enforces_seat_cap(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        await client.get("/admin/octocat/hello-world")  # seats octocat (1 of 5)
        await client.post("/admin/octocat/hello-world/members", json={"github_login": "alice"})  # 2 of 5
        await client.post("/admin/octocat/hello-world/members", json={"github_login": "bob"})  # 3 of 5
        await client.post("/admin/octocat/hello-world/members", json={"github_login": "carol"})  # 4 of 5
        await client.post("/admin/octocat/hello-world/members", json={"github_login": "dave"})  # 5 of 5
        response = await client.post("/admin/octocat/hello-world/members", json={"github_login": "erin"})
    assert response.status_code == 409
    assert "seat limit reached" in response.json()["detail"]


@pytest.mark.asyncio
async def test_add_member_rejects_invalid_github_login(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        response = await client.post("/admin/octocat/hello-world/members", json={"github_login": "-bad-login-"})
    assert response.status_code == 422
