from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app_server.auth import (
    encrypt_access_token,
    get_current_session,
    sign_session_id,
    unsign_session_id,
)
from app_server.db import create_session, get_session
from app_server.main import app


def test_sign_and_unsign_round_trip():
    signed = sign_session_id("sess-123", "test-secret")
    assert unsign_session_id(signed, "test-secret") == "sess-123"


def test_unsign_rejects_tampered_value():
    signed = sign_session_id("sess-123", "test-secret")
    first, rest = signed.split(".", 1)
    tampered_first = ("a" if first[0] != "a" else "b") + first[1:]
    tampered = f"{tampered_first}.{rest}"
    assert unsign_session_id(tampered, "test-secret") is None


@pytest.mark.asyncio
async def test_login_redirects_to_github_authorize(pool, monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://test")
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 307
    assert "github.com/login/oauth/authorize" in response.headers["location"]
    assert "client_id=test-client-id" in response.headers["location"]


@pytest.mark.asyncio
async def test_callback_creates_session_and_sets_cookie(pool, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_faketoken"})
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 42, "login": "octocat"})
        return httpx.Response(404)

    monkeypatch.setattr(
        "app_server.auth._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )
    monkeypatch.setattr(
        "app_server.auth._github_oauth_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://github.com"),
    )

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        login_response = await client.get("/auth/login", follow_redirects=False)
        state = login_response.headers["location"].split("state=")[1]
        response = await client.get(
            f"/auth/callback?code=fake-code&state={state}", follow_redirects=False
        )

    assert response.status_code == 307
    assert "session" in response.cookies
    session_id = unsign_session_id(response.cookies["session"], "test-session-secret")
    row = await get_session(pool, session_id)
    assert row["github_login"] == "octocat"
    assert row["github_access_token"] != "gho_faketoken"


@pytest.mark.asyncio
async def test_callback_rejects_mismatched_state(pool, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as client:
        await client.get("/auth/login", follow_redirects=False)
        response = await client.get(
            "/auth/callback?code=fake-code&state=wrong-state", follow_redirects=False
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_callback_rejects_missing_state_cookie(pool, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/auth/callback?code=fake-code&state=anything", follow_redirects=False
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_get_current_session_decrypts_access_token(pool, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    encrypted = encrypt_access_token("gho_realtoken", "test-session-secret")
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    await create_session(pool, "sess-3", 42, "octocat", encrypted, expires)
    signed = sign_session_id("sess-3", "test-session-secret")

    class FakeRequest:
        cookies = {"session": signed}
        app = type("App", (), {"state": type("State", (), {"db_pool": pool})()})()

    session = await get_current_session(FakeRequest())
    assert session["github_access_token"] == "gho_realtoken"
