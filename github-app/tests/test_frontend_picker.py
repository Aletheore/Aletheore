from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app_server.auth import encrypt_access_token, sign_session_id
from app_server.db import create_session
from app_server.main import app


async def _async_true(*args, **kwargs) -> bool:
    return True


async def _logged_in_client(pool, monkeypatch, administered_ids):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("GITHUB_APP_SLUG", "aletheore")
    await create_session(
        pool,
        "picker-sess",
        42,
        "octocat",
        encrypt_access_token("gho_faketoken", "test-session-secret"),
        datetime.now(timezone.utc) + timedelta(hours=1),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "total_count": len(administered_ids),
                "installations": [{"id": installation_id} for installation_id in administered_ids],
            },
        )

    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )
    monkeypatch.setattr("app_server.admin._has_real_admin_permission", _async_true)
    app.state.db_pool = pool
    signed = sign_session_id("picker-sess", "test-session-secret")
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies={"session": signed})


@pytest.mark.asyncio
async def test_picker_page_requires_login(pool):
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/dashboard", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/"


@pytest.mark.asyncio
async def test_picker_page_ships_install_and_subscribe_ctas_for_the_empty_state(pool, monkeypatch):
    # Real gap found dogfooding this page (2026-09-28): a login with zero
    # installations, or one still on the free plan (Flash never gets a
    # dashboard either, by design), saw an accurate explanation here but no
    # way to act on it. The empty-state branch is client-side JS - the page
    # always ships the same HTML regardless of data - so this just checks
    # the shipped markup has real, correct links, not a specific request's
    # rendered output.
    client = await _logged_in_client(pool, monkeypatch, [])
    async with client:
        response = await client.get("/dashboard")

    assert response.status_code == 200
    assert "Install the Aletheore GitHub App" in response.text
    assert "github.com/apps/aletheore/installations/new" in response.text
    assert "Subscribe to AIR" in response.text
    assert '/subscribe?plan=air&amp;interval=month' in response.text
    # AIR specifically - Flash still wouldn't unlock this page, so it must
    # never be offered as the CTA here.
    assert "plan=flash" not in response.text
