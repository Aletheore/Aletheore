import hashlib
import hmac
import ipaddress
import json
import time
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app_server import paddle_ip_allowlist
from app_server.db import get_extra_seats, get_installation, upsert_installation
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
    body = json.dumps(_subscription_created_payload("pri_01kyhevc8bkcghfpwjymz16y2h", 100)).encode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhooks/paddle", content=body, headers={"paddle-signature": _sign(body)})
    assert response.status_code == 200
    installation = await get_installation(pool, 100)
    assert installation["plan"] == "air"
    assert installation["paddle_subscription_id"] == "sub_test_123"
    assert installation["paddle_customer_id"] == "ctm_test_456"


@pytest.mark.asyncio
async def test_invalid_signature_rejected_with_no_write(pool, monkeypatch):
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    app.state.db_pool = pool
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (101, 'acme', 'free')"
    )
    body = json.dumps(_subscription_created_payload("pri_01kyhevc8bkcghfpwjymz16y2h", 101)).encode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/webhooks/paddle", content=body, headers={"paddle-signature": "ts=1;h1=deadbeef"}
        )
    assert response.status_code == 401
    installation = await get_installation(pool, 101)
    assert installation["plan"] == "free"


@pytest.mark.asyncio
async def test_malformed_json_body_with_valid_signature_returns_401_not_500(pool, monkeypatch):
    # Before this fix, a validly-signed but non-JSON body reached
    # `await request.json()` uncaught - json.JSONDecodeError propagated as
    # an unhandled 500 instead of the 401 every other verification failure
    # in this handler returns.
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    app.state.db_pool = pool
    body = b"not valid json at all"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhooks/paddle", content=body, headers={"paddle-signature": _sign(body)})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_non_object_json_body_with_valid_signature_returns_401_not_500(pool, monkeypatch):
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    app.state.db_pool = pool
    body = b"[1, 2, 3]"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhooks/paddle", content=body, headers={"paddle-signature": _sign(body)})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_confirmed_non_paddle_ip_rejected_despite_valid_signature(pool, monkeypatch):
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", WEBHOOK_SECRET)

    async def _fake_fetch():
        return [ipaddress.ip_network("203.0.113.0/24")]

    monkeypatch.setattr(paddle_ip_allowlist, "_fetch_paddle_networks", _fake_fetch)
    app.state.db_pool = pool
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (106, 'acme', 'free')"
    )
    body = json.dumps(_subscription_created_payload("pri_01kyhevc8bkcghfpwjymz16y2h", 106)).encode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/webhooks/paddle",
            content=body,
            headers={"paddle-signature": _sign(body), "x-forwarded-for": "198.51.100.1"},
        )
    assert response.status_code == 401
    installation = await get_installation(pool, 106)
    assert installation["plan"] == "free"


@pytest.mark.asyncio
async def test_confirmed_paddle_ip_accepted(pool, monkeypatch):
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", WEBHOOK_SECRET)

    async def _fake_fetch():
        return [ipaddress.ip_network("203.0.113.0/24")]

    monkeypatch.setattr(paddle_ip_allowlist, "_fetch_paddle_networks", _fake_fetch)
    app.state.db_pool = pool
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (107, 'acme', 'free')"
    )
    body = json.dumps(_subscription_created_payload("pri_01kyhevc8bkcghfpwjymz16y2h", 107)).encode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/webhooks/paddle",
            content=body,
            headers={"paddle-signature": _sign(body), "x-forwarded-for": "10.0.0.1, 203.0.113.5"},
        )
    assert response.status_code == 200
    installation = await get_installation(pool, 107)
    assert installation["plan"] == "air"


@pytest.mark.asyncio
async def test_missing_signature_header_rejected(pool):
    app.state.db_pool = pool
    body = json.dumps(_subscription_created_payload("pri_01kyhevc8bkcghfpwjymz16y2h", 102)).encode()
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
    payload = _subscription_created_payload("pri_01kyhevc8bkcghfpwjymz16y2h", 104)
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
    body = json.dumps(_subscription_created_payload("pri_01kyhevc9xn6z2nghmy8057jvp", 105)).encode()
    headers = {"paddle-signature": _sign(body)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/webhooks/paddle", content=body, headers=headers)
        r2 = await client.post("/webhooks/paddle", content=body, headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 200
    installation = await get_installation(pool, 105)
    assert installation["plan"] == "air"


@pytest.mark.asyncio
async def test_free_to_paid_transition_triggers_live_wiki_full_build(pool):
    fake_queue = MagicMock()
    await upsert_installation(pool, 200, "acme")  # defaults to plan='free'

    payload = _subscription_created_payload("pri_01kyhevc8bkcghfpwjymz16y2h", 200)
    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    installation = await get_installation(pool, 200)
    assert installation["plan"] == "air"
    assert fake_queue.enqueue.call_count == 2
    job_names = {call.args[0] for call in fake_queue.enqueue.call_args_list}
    assert job_names == {
        "scan_worker.jobs.run_live_wiki_full_build_for_installation_job",
        "scan_worker.jobs.run_live_docs_full_build_for_installation_job",
    }
    for call in fake_queue.enqueue.call_args_list:
        assert call.kwargs["installation_id"] == 200


@pytest.mark.asyncio
async def test_paid_to_paid_change_does_not_retrigger_live_wiki_full_build(pool):
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (201, 'acme', 'air')"
    )

    payload = _subscription_created_payload("pri_01kyhevc9xn6z2nghmy8057jvp", 201)
    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    installation = await get_installation(pool, 201)
    assert installation["plan"] == "air"
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_replaying_subscription_created_only_triggers_wiki_build_once(pool):
    fake_queue = MagicMock()
    await upsert_installation(pool, 202, "acme")  # defaults to plan='free'
    payload = _subscription_created_payload("pri_01kyhevc8bkcghfpwjymz16y2h", 202)

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)
    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    assert fake_queue.enqueue.call_count == 2


@pytest.mark.asyncio
async def test_subscription_canceled_revokes_to_free(pool):
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (300, 'acme', 'air')"
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
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (301, 'acme', 'air')"
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
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (302, 'acme', 'air')"
    )
    payload = _subscription_event_payload("subscription.updated", "past_due", 302)

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    installation = await get_installation(pool, 302)
    assert installation["plan"] == "free"


@pytest.mark.asyncio
async def test_subscription_updated_refreshes_plan_without_retriggering_wiki_build(pool):
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (303, 'acme', 'air')"
    )
    payload = _subscription_event_payload(
        "subscription.updated", "active", 303, price_id="pri_01kyhevc9xn6z2nghmy8057jvp"
    )

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    installation = await get_installation(pool, 303)
    assert installation["plan"] == "air"
    # Paid-to-paid change (e.g. switching monthly <-> annual) must not
    # re-trigger the one-time wiki build.
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_subscription_updated_reconciles_extra_seats_from_items(pool):
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan, extra_seats) "
        "VALUES (305, 'acme', 'air', 0)"
    )
    payload = {
        "event_type": "subscription.updated",
        "data": {
            "id": "sub_test_123",
            "customer_id": "ctm_test_456",
            "status": "active",
            "custom_data": {"installation_id": "305"},
            "items": [
                {"price": {"id": "pri_01kyhevc8bkcghfpwjymz16y2h"}, "quantity": 1},
                {"price": {"id": "pri_01kym2q99kevmdg7h71nwpm4ej"}, "quantity": 3},
            ],
        },
    }

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    installation = await get_installation(pool, 305)
    assert installation["plan"] == "air"
    assert await get_extra_seats(pool, 305) == 3


@pytest.mark.asyncio
async def test_subscription_canceled_resets_extra_seats_to_zero(pool):
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan, extra_seats) "
        "VALUES (306, 'acme', 'air', 3)"
    )
    payload = _subscription_event_payload("subscription.canceled", "canceled", 306)

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    installation = await get_installation(pool, 306)
    assert installation["plan"] == "free"
    assert await get_extra_seats(pool, 306) == 0


@pytest.mark.asyncio
async def test_subscription_updated_with_no_extra_seat_item_resets_to_zero(pool):
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan, extra_seats) "
        "VALUES (307, 'acme', 'air', 3)"
    )
    payload = _subscription_event_payload(
        "subscription.updated", "active", 307, price_id="pri_01kyhevc8bkcghfpwjymz16y2h"
    )

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    assert await get_extra_seats(pool, 307) == 0


@pytest.mark.asyncio
async def test_subscription_resumed_restores_paid_access(pool):
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (304, 'acme', 'free')"
    )
    payload = _subscription_event_payload(
        "subscription.resumed", "active", 304, price_id="pri_01kyhevc8bkcghfpwjymz16y2h"
    )

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    installation = await get_installation(pool, 304)
    assert installation["plan"] == "air"
    # Resuming after a cancellation is a free -> paid transition again,
    # so the one-time wiki and docs builds fire once more.
    assert fake_queue.enqueue.call_count == 2


@pytest.mark.asyncio
async def test_unhandled_event_type_is_ignored(pool):
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (305, 'acme', 'air')"
    )
    payload = _subscription_event_payload("transaction.completed", "completed", 305)

    await handle_paddle_webhook_event(payload, pool, "redis://unused")

    installation = await get_installation(pool, 305)
    assert installation["plan"] == "air"
