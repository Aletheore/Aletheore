import hashlib
import hmac
import json
import time

import pytest
from httpx import ASGITransport, AsyncClient

from app_server.db import get_installation
from app_server.main import app

WEBHOOK_SECRET = "pdl_ntfset_test_secret"


def _sign(raw_body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    ts = int(time.time())
    signed_payload = f"{ts}:{raw_body.decode()}"
    digest = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
    return f"ts={ts};h1={digest}"


def _subscription_created_payload(price_id: str, installation_id: int) -> dict:
    return {
        "event_type": "subscription.created",
        "data": {
            "id": "sub_test_123",
            "customer_id": "ctm_test_456",
            "custom_data": {"installation_id": str(installation_id)},
            "items": [{"price": {"id": price_id}}],
        },
    }


@pytest.mark.asyncio
async def test_valid_subscription_created_updates_installation_plan(pool, monkeypatch):
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    app.state.db_pool = pool
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (100, 'acme', 'free')"
    )
    body = json.dumps(_subscription_created_payload("pri_01ky9jwz35hvj5xs6f8xqw6htt", 100)).encode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhooks/paddle", content=body, headers={"paddle-signature": _sign(body)})
    assert response.status_code == 200
    installation = await get_installation(pool, 100)
    assert installation["plan"] == "indie"
    assert installation["paddle_subscription_id"] == "sub_test_123"
    assert installation["paddle_customer_id"] == "ctm_test_456"


@pytest.mark.asyncio
async def test_invalid_signature_rejected_with_no_write(pool, monkeypatch):
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    app.state.db_pool = pool
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (101, 'acme', 'free')"
    )
    body = json.dumps(_subscription_created_payload("pri_01ky9jwz35hvj5xs6f8xqw6htt", 101)).encode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/webhooks/paddle", content=body, headers={"paddle-signature": "ts=1;h1=deadbeef"}
        )
    assert response.status_code == 401
    installation = await get_installation(pool, 101)
    assert installation["plan"] == "free"


@pytest.mark.asyncio
async def test_missing_signature_header_rejected(pool):
    app.state.db_pool = pool
    body = json.dumps(_subscription_created_payload("pri_01ky9jwz35hvj5xs6f8xqw6htt", 102)).encode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhooks/paddle", content=body)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_unknown_price_id_returns_200_but_writes_nothing(pool, monkeypatch):
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    app.state.db_pool = pool
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (103, 'acme', 'free')"
    )
    body = json.dumps(_subscription_created_payload("pri_totally_unknown", 103)).encode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhooks/paddle", content=body, headers={"paddle-signature": _sign(body)})
    assert response.status_code == 200
    installation = await get_installation(pool, 103)
    assert installation["plan"] == "free"


@pytest.mark.asyncio
async def test_missing_installation_id_returns_200_but_writes_nothing(pool, monkeypatch):
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    app.state.db_pool = pool
    payload = _subscription_created_payload("pri_01ky9jwz35hvj5xs6f8xqw6htt", 104)
    del payload["data"]["custom_data"]["installation_id"]
    body = json.dumps(payload).encode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhooks/paddle", content=body, headers={"paddle-signature": _sign(body)})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_duplicate_delivery_is_idempotent(pool, monkeypatch):
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    app.state.db_pool = pool
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (105, 'acme', 'free')"
    )
    body = json.dumps(_subscription_created_payload("pri_01ky9jx0gbx02mnn4d166yp3vc", 105)).encode()
    headers = {"paddle-signature": _sign(body)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/webhooks/paddle", content=body, headers=headers)
        r2 = await client.post("/webhooks/paddle", content=body, headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 200
    installation = await get_installation(pool, 105)
    assert installation["plan"] == "team"
