import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app_server.auth import encrypt_access_token, sign_session_id, unsign_checkout_installation_id
from app_server.db import add_paddle_ids_to_installation, create_session, upsert_installation
from app_server.frontend import _plan_display_name
from app_server.main import app


def test_plan_display_name_covers_all_three_plans():
    # Real bug found by an independent audit pass: this used to be a bare
    # binary (free vs "else AIR"), which silently mislabeled a flash-plan
    # installation as "Aletheore AIR" in the UI - a real, misleading
    # billing-adjacent bug, not cosmetic.
    assert _plan_display_name("free") == "Aletheore Community"
    assert _plan_display_name("flash") == "Aletheore Flash"
    assert _plan_display_name("air") == "Aletheore AIR"


def test_plan_display_name_falls_back_to_air_for_unrecognized_value():
    # Matches this function's original fail-open shape (anything not
    # literally "free" used to read as AIR) - now explicit, not accidental.
    assert _plan_display_name("some-future-plan") == "Aletheore AIR"


async def _logged_in_client(pool, monkeypatch, administered_ids):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("GITHUB_APP_SLUG", "aletheore")
    await create_session(
        pool,
        "sub-sess",
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
    app.state.db_pool = pool
    signed = sign_session_id("sub-sess", "test-session-secret")
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies={"session": signed})


async def _logged_in_client_with_dead_token(pool, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("GITHUB_APP_SLUG", "aletheore")
    await create_session(
        pool,
        "dead-sess",
        43,
        "octocat",
        encrypt_access_token("gho_deadtoken", "test-session-secret"),
        datetime.now(timezone.utc) + timedelta(hours=1),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )
    app.state.db_pool = pool
    signed = sign_session_id("dead-sess", "test-session-secret")
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies={"session": signed})


@pytest.mark.asyncio
async def test_invalid_plan_returns_400(pool):
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/subscribe?plan=bogus&interval=month")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_invalid_interval_returns_400(pool):
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/subscribe?plan=air&interval=daily")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_not_signed_in_redirects_to_login_preserving_plan_and_interval(pool):
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/subscribe?plan=air&interval=year", follow_redirects=False)
    assert response.status_code == 307
    location = response.headers["location"]
    assert "/auth/login" in location
    assert "next=" in location
    assert "plan%3Dair" in location
    assert "interval%3Dyear" in location


@pytest.mark.asyncio
async def test_dead_github_token_redirects_to_login_and_clears_session(pool, monkeypatch):
    client = await _logged_in_client_with_dead_token(pool, monkeypatch)
    async with client:
        response = await client.get("/subscribe?plan=air&interval=month", follow_redirects=False)
    assert response.status_code == 307
    assert "/auth/login" in response.headers["location"]
    assert response.cookies.get("session") is None


@pytest.mark.asyncio
async def test_zero_installations_shows_install_prompt(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, [])
    async with client:
        response = await client.get("/subscribe?plan=air&interval=month")
    assert response.status_code == 200
    assert "Install the Aletheore GitHub App" in response.text
    assert "github.com/apps/aletheore/installations/new" in response.text
    assert 'href="/dashboard"' in response.text


@pytest.mark.asyncio
async def test_one_installation_shows_checkout_with_current_plan(pool, monkeypatch):
    await upsert_installation(pool, 2001, "acme")
    client = await _logged_in_client(pool, monkeypatch, [2001])
    async with client:
        response = await client.get("/subscribe?plan=air&interval=month")
    assert response.status_code == 200
    assert "acme" in response.text
    assert "currently on Aletheore Community" in response.text
    # A signed token, not the raw installation_id - the browser must never
    # be handed something Paddle.Checkout.open() could forward for a
    # different installation. Decoding it is what proves it's actually
    # usable, not just present-and-opaque.
    assert 'data-installation-id="2001"' not in response.text
    match = re.search(r'data-installation-token="([^"]+)"', response.text)
    assert match is not None
    assert unsign_checkout_installation_id(match.group(1), "test-session-secret") == 2001
    assert "pri_01kyhevc8bkcghfpwjymz16y2h" in response.text  # air monthly price id
    assert "customData" in response.text
    assert "installation_token" in response.text
    assert "successUrl" in response.text and "dashboard" in response.text
    assert 'href="/dashboard"' in response.text
    assert "pwCustomer" not in response.text


@pytest.mark.asyncio
async def test_one_installation_with_existing_paddle_customer_wires_pw_customer(pool, monkeypatch):
    # Before this fix, Paddle.Initialize() never passed pwCustomer at all, so
    # Paddle Retain had no way to recognize a returning customer re-subscribing
    # through this page - even though the installation already has a real
    # Paddle customer ID from a previous subscription.
    await upsert_installation(pool, 2004, "returning-corp")
    await add_paddle_ids_to_installation(pool, 2004, "sub_existing", "ctm_existing123")
    client = await _logged_in_client(pool, monkeypatch, [2004])
    async with client:
        response = await client.get("/subscribe?plan=air&interval=month")
    assert response.status_code == 200
    assert 'pwCustomer: { id: "ctm_existing123" }' in response.text


@pytest.mark.asyncio
async def test_multiple_installations_shows_selection(pool, monkeypatch):
    await upsert_installation(pool, 2002, "acme")
    await upsert_installation(pool, 2003, "beta-corp")
    client = await _logged_in_client(pool, monkeypatch, [2002, 2003])
    async with client:
        response = await client.get("/subscribe?plan=air&interval=year")
    assert response.status_code == 200
    # Radio values are signed tokens, not raw installation ids - same
    # reasoning as the single-installation case above.
    assert 'value="2002"' not in response.text
    assert 'value="2003"' not in response.text
    tokens = re.findall(r'name="installation_token" value="([^"]+)"', response.text)
    decoded = {unsign_checkout_installation_id(token, "test-session-secret") for token in tokens}
    assert decoded == {2002, 2003}
    assert "acme" in response.text
    assert "beta-corp" in response.text
    assert "pri_01kyhevc9xn6z2nghmy8057jvp" in response.text  # air yearly price id
