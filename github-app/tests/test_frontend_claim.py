from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app_server.auth import encrypt_access_token, sign_session_id
from app_server.db import (
    create_session,
    get_installation,
    get_pending_subscription_claim_by_token,
    insert_pending_subscription_claim,
    mark_subscription_claim_claimed,
    upsert_installation,
)
from app_server.main import app


async def _logged_in_client(pool, monkeypatch, administered_ids):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    await create_session(
        pool,
        "claim-sess",
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
    signed = sign_session_id("claim-sess", "test-session-secret")
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies={"session": signed})


@pytest.mark.asyncio
async def test_claim_page_redirects_to_login_when_not_signed_in(pool):
    app.state.db_pool = pool
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/subscribe/claim", follow_redirects=False)
    assert response.status_code == 307
    assert "/auth/login" in response.headers["location"]
    assert "next=%2Fsubscribe%2Fclaim" in response.headers["location"]


@pytest.mark.asyncio
async def test_claim_page_shows_polling_state_when_claim_not_found_yet(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, [])
    client.cookies.set("claim_token", "tok_not_found_yet")
    async with client:
        response = await client.get("/subscribe/claim")
    assert response.status_code == 200
    assert "Confirming your payment" in response.text


@pytest.mark.asyncio
async def test_claim_page_shows_already_claimed_state(pool, monkeypatch):
    await upsert_installation(pool, 1001, "acme")
    await insert_pending_subscription_claim(pool, "tok_claimed", "sub_1", "ctm_1", None, "team")
    await mark_subscription_claim_claimed(pool, "tok_claimed", 1001)
    client = await _logged_in_client(pool, monkeypatch, [1001])
    client.cookies.set("claim_token", "tok_claimed")
    async with client:
        response = await client.get("/subscribe/claim")
    assert response.status_code == 200
    assert "already activated" in response.text.lower()


@pytest.mark.asyncio
async def test_claim_page_zero_installations_prompts_install(pool, monkeypatch):
    await insert_pending_subscription_claim(pool, "tok_zero", "sub_2", "ctm_2", None, "indie")
    client = await _logged_in_client(pool, monkeypatch, [])
    client.cookies.set("claim_token", "tok_zero")
    async with client:
        response = await client.get("/subscribe/claim")
    assert response.status_code == 200
    assert "Install the Aletheore GitHub App" in response.text


@pytest.mark.asyncio
async def test_claim_page_one_installation_shows_confirm(pool, monkeypatch):
    await upsert_installation(pool, 1002, "acme")
    await insert_pending_subscription_claim(pool, "tok_one", "sub_3", "ctm_3", None, "team")
    client = await _logged_in_client(pool, monkeypatch, [1002])
    client.cookies.set("claim_token", "tok_one")
    async with client:
        response = await client.get("/subscribe/claim")
    assert response.status_code == 200
    assert "Apply" in response.text
    assert "acme" in response.text


@pytest.mark.asyncio
async def test_apply_updates_installation_plan_and_marks_claimed(pool, monkeypatch):
    await upsert_installation(pool, 1003, "acme")
    await insert_pending_subscription_claim(pool, "tok_apply", "sub_4", "ctm_4", None, "enterprise")
    client = await _logged_in_client(pool, monkeypatch, [1003])
    async with client:
        response = await client.post(
            "/subscribe/claim/apply",
            content="claim_token=tok_apply&installation_id=1003",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    assert response.status_code == 200
    installation = await get_installation(pool, 1003)
    assert installation["plan"] == "enterprise"
    assert installation["paddle_subscription_id"] == "sub_4"
    claim = await get_pending_subscription_claim_by_token(pool, "tok_apply")
    assert claim["claimed_at"] is not None


@pytest.mark.asyncio
async def test_apply_rejects_installation_user_does_not_administer(pool, monkeypatch):
    await upsert_installation(pool, 1004, "someone-else")
    await insert_pending_subscription_claim(pool, "tok_forbidden", "sub_5", "ctm_5", None, "team")
    client = await _logged_in_client(pool, monkeypatch, [])
    async with client:
        response = await client.post(
            "/subscribe/claim/apply",
            content="claim_token=tok_forbidden&installation_id=1004",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
    assert response.status_code == 403
