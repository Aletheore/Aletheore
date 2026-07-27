import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app_server.db import get_installation, upsert_installation
from app_server.main import app
from app_server.webhooks.paddle import handle_paddle_webhook_event

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
            "status": "active",
            "custom_data": {"installation_id": str(installation_id)},
            "items": [{"price": {"id": price_id}}],
        },
    }


def _subscription_event_payload(event_type: str, status: str, installation_id: int, price_id: str | None = None) -> dict:
    return {
        "event_type": event_type,
        "data": {
            "id": "sub_test_123",
            "customer_id": "ctm_test_456",
            "status": status,
            "custom_data": {"installation_id": str(installation_id)},
            "items": [{"price": {"id": price_id}}] if price_id else [],
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


@pytest.mark.asyncio
async def test_free_to_paid_transition_triggers_live_wiki_full_build(pool):
    fake_queue = MagicMock()
    await upsert_installation(pool, 200, "acme")  # defaults to plan='free'

    payload = _subscription_created_payload("pri_01ky9jwz35hvj5xs6f8xqw6htt", 200)
    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    installation = await get_installation(pool, 200)
    assert installation["plan"] == "indie"
    fake_queue.enqueue.assert_called_once()
    args, kwargs = fake_queue.enqueue.call_args
    assert args[0] == "scan_worker.jobs.run_live_wiki_full_build_for_installation_job"
    assert kwargs["installation_id"] == 200


@pytest.mark.asyncio
async def test_paid_to_paid_change_does_not_retrigger_live_wiki_full_build(pool):
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (201, 'acme', 'indie')"
    )

    payload = _subscription_created_payload("pri_01ky9jx0gbx02mnn4d166yp3vc", 201)
    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    installation = await get_installation(pool, 201)
    assert installation["plan"] == "team"
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_replaying_subscription_created_only_triggers_wiki_build_once(pool):
    fake_queue = MagicMock()
    await upsert_installation(pool, 202, "acme")  # defaults to plan='free'
    payload = _subscription_created_payload("pri_01ky9jwz35hvj5xs6f8xqw6htt", 202)

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)
    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    fake_queue.enqueue.assert_called_once()


@pytest.mark.asyncio
async def test_subscription_canceled_revokes_to_free(pool):
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (300, 'acme', 'indie')"
    )
    payload = _subscription_event_payload("subscription.canceled", "canceled", 300)

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    installation = await get_installation(pool, 300)
    assert installation["plan"] == "free"
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_subscription_paused_revokes_to_free(pool):
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (301, 'acme', 'team')"
    )
    payload = _subscription_event_payload("subscription.paused", "paused", 301)

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    installation = await get_installation(pool, 301)
    assert installation["plan"] == "free"


@pytest.mark.asyncio
async def test_subscription_past_due_revokes_to_free(pool):
    # No dunning-aware grace tier yet - a lapsed payment cuts access
    # immediately rather than silently continuing to grant it.
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (302, 'acme', 'indie')"
    )
    payload = _subscription_event_payload("subscription.updated", "past_due", 302)

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    installation = await get_installation(pool, 302)
    assert installation["plan"] == "free"


@pytest.mark.asyncio
async def test_subscription_updated_downgrades_between_paid_tiers(pool):
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (303, 'acme', 'team')"
    )
    payload = _subscription_event_payload(
        "subscription.updated", "active", 303, price_id="pri_01ky9jwz35hvj5xs6f8xqw6htt"
    )

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    installation = await get_installation(pool, 303)
    assert installation["plan"] == "indie"
    # Paid-to-paid change must not re-trigger the one-time wiki build.
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_subscription_resumed_restores_paid_access(pool):
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (304, 'acme', 'free')"
    )
    payload = _subscription_event_payload(
        "subscription.resumed", "active", 304, price_id="pri_01ky9jwz35hvj5xs6f8xqw6htt"
    )

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    installation = await get_installation(pool, 304)
    assert installation["plan"] == "indie"
    # Resuming after a cancellation is a free -> paid transition again,
    # so the one-time wiki build fires once more.
    fake_queue.enqueue.assert_called_once()


@pytest.mark.asyncio
async def test_unhandled_event_type_is_ignored(pool):
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (305, 'acme', 'indie')"
    )
    payload = _subscription_event_payload("transaction.completed", "completed", 305)

    await handle_paddle_webhook_event(payload, pool, "redis://unused")

    installation = await get_installation(pool, 305)
    assert installation["plan"] == "indie"
