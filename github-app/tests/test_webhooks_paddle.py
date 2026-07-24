import hashlib
import hmac
import json
import time

import pytest
from httpx import ASGITransport, AsyncClient

from app_server.db import get_pending_subscription_claim_by_token, insert_pending_subscription_claim
from app_server.main import app

WEBHOOK_SECRET = "pdl_ntfset_test_secret"


def _sign(raw_body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    ts = int(time.time())
    signed_payload = f"{ts}:{raw_body.decode()}"
    digest = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
    return f"ts={ts};h1={digest}"


def _subscription_created_payload(price_id: str, claim_token: str) -> dict:
    return {
        "event_type": "subscription.created",
        "data": {
            "id": "sub_test_123",
            "customer_id": "ctm_test_456",
            "custom_data": {"claim_token": claim_token},
            "items": [{"price": {"id": price_id}}],
        },
    }


@pytest.mark.asyncio
async def test_valid_subscription_created_creates_pending_claim(pool, monkeypatch):
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    app.state.db_pool = pool
    body = json.dumps(_subscription_created_payload("pri_01ky9jwz35hvj5xs6f8xqw6htt", "claim_abc")).encode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhooks/paddle", content=body, headers={"paddle-signature": _sign(body)})
    assert response.status_code == 200
    claim = await get_pending_subscription_claim_by_token(pool, "claim_abc")
    assert claim["plan"] == "indie"
    assert claim["paddle_subscription_id"] == "sub_test_123"


@pytest.mark.asyncio
async def test_invalid_signature_rejected_with_no_write(pool, monkeypatch):
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    app.state.db_pool = pool
    body = json.dumps(_subscription_created_payload("pri_01ky9jwz35hvj5xs6f8xqw6htt", "claim_xyz")).encode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhooks/paddle", content=body, headers={"paddle-signature": "ts=1;h1=deadbeef"})
    assert response.status_code == 401
    assert await get_pending_subscription_claim_by_token(pool, "claim_xyz") is None


@pytest.mark.asyncio
async def test_missing_signature_header_rejected(pool):
    app.state.db_pool = pool
    body = json.dumps(_subscription_created_payload("pri_01ky9jwz35hvj5xs6f8xqw6htt", "claim_none")).encode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhooks/paddle", content=body)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_unknown_price_id_returns_200_but_writes_no_claim(pool, monkeypatch):
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    app.state.db_pool = pool
    body = json.dumps(_subscription_created_payload("pri_totally_unknown", "claim_unknown")).encode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhooks/paddle", content=body, headers={"paddle-signature": _sign(body)})
    assert response.status_code == 200
    assert await get_pending_subscription_claim_by_token(pool, "claim_unknown") is None


@pytest.mark.asyncio
async def test_duplicate_delivery_is_idempotent(pool, monkeypatch):
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    app.state.db_pool = pool
    body = json.dumps(_subscription_created_payload("pri_01ky9jwz35hvj5xs6f8xqw6htt", "claim_dup")).encode()
    headers = {"paddle-signature": _sign(body)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/webhooks/paddle", content=body, headers=headers)
        r2 = await client.post("/webhooks/paddle", content=body, headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert await get_pending_subscription_claim_by_token(pool, "claim_dup") is not None


@pytest.mark.asyncio
async def test_customer_updated_backfills_pending_claim_email(pool, monkeypatch):
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    app.state.db_pool = pool
    await insert_pending_subscription_claim(pool, "claim_email", "sub_999", "ctm_email", None, "team")
    body = json.dumps({"event_type": "customer.updated", "data": {"id": "ctm_email", "email": "buyer@example.com"}}).encode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhooks/paddle", content=body, headers={"paddle-signature": _sign(body)})
    assert response.status_code == 200
    claim = await get_pending_subscription_claim_by_token(pool, "claim_email")
    assert claim["paddle_customer_email"] == "buyer@example.com"
