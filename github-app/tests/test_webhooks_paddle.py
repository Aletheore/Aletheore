import hashlib
import hmac
import ipaddress
import json
import logging
import time
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app_server import paddle_ip_allowlist
from app_server.affiliates import create_affiliate, get_referral, list_affiliates_with_totals, record_referral
from app_server.auth import sign_checkout_installation_id
from app_server.db import (
    add_installation_member,
    claim_webhook_delivery,
    get_extra_seats,
    get_installation,
    upsert_github_user_email,
    upsert_installation,
)
from app_server.main import app
from app_server.paddle_pricing import EXTRA_SEAT_PRICE_ID
from app_server.webhooks.paddle import handle_paddle_webhook_event

WEBHOOK_SECRET = "pdl_ntfset_test_secret"
# Matches conftest.py's SESSION_SECRET default - the webhook handler
# verifies custom_data.installation_token against this same secret via
# get_settings().session_secret, so a test-built token has to be signed
# with it to pass.
SESSION_SECRET = "test-session-secret"


def _sign(raw_body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    ts = int(time.time())
    signed_payload = f"{ts}:{raw_body.decode()}"
    digest = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
    return f"ts={ts};h1={digest}"


def _installation_token(installation_id: int) -> str:
    return sign_checkout_installation_id(installation_id, SESSION_SECRET)


def _subscription_created_payload(
    price_id: str,
    installation_id: int,
    event_id: str = "evt_created_default",
    discount_id: str | None = None,
) -> dict:
    payload = {
        "event_id": event_id,
        "event_type": "subscription.created",
        "data": {
            "id": "sub_test_123",
            "customer_id": "ctm_test_456",
            "status": "active",
            "custom_data": {"installation_token": _installation_token(installation_id)},
            "items": [{"price": {"id": price_id}}],
        },
    }
    if discount_id is not None:
        # Real Paddle subscription payloads nest this under a `discount`
        # object (`data.discount.id`) - there is no flat `discount_id`
        # field. Matching that shape here is what caught the production
        # bug where the handler read the flat field and never found it.
        payload["data"]["discount"] = {"id": discount_id}
    return payload


def _transaction_completed_payload(
    installation_id: int,
    total_cents: str,
    transaction_id: str = "txn_test_1",
    event_id: str = "evt_txn_default",
) -> dict:
    return {
        "event_id": event_id,
        "event_type": "transaction.completed",
        "data": {
            "id": transaction_id,
            "custom_data": {"installation_token": _installation_token(installation_id)},
            "details": {"totals": {"total": total_cents}},
            "billed_at": "2026-08-10T12:00:00Z",
        },
    }


def _subscription_event_payload(
    event_type: str,
    status: str,
    installation_id: int,
    price_id: str | None = None,
    event_id: str = "evt_event_default",
) -> dict:
    return {
        "event_id": event_id,
        "event_type": event_type,
        "data": {
            "id": "sub_test_123",
            "customer_id": "ctm_test_456",
            "status": status,
            "custom_data": {"installation_token": _installation_token(installation_id)},
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
async def test_non_ascii_paddle_signature_returns_401_not_500(pool, monkeypatch):
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    app.state.db_pool = pool
    body = b'{"event_type": "subscription.created"}'
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/webhooks/paddle",
            content=body,
            headers=[(b"paddle-signature", b"ts=1;h1=caf\xe9")],
        )
    assert response.status_code == 401


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
async def test_signature_failure_is_logged_with_header_presence_and_length(pool, caplog):
    # Previously silent - a real signature failure (rotated secret, clock
    # drift past tolerance, a forged request) and a missing header looked
    # identical from the outside with nothing to go on.
    app.state.db_pool = pool
    body = json.dumps(_subscription_created_payload("pri_01kyhevc8bkcghfpwjymz16y2h", 108)).encode()
    with caplog.at_level(logging.WARNING, logger="app_server.webhooks.paddle"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/webhooks/paddle", content=body, headers={"paddle-signature": "ts=1;h1=deadbeef"}
            )
    assert response.status_code == 401
    record = next(r for r in caplog.records if "signature verification failed" in r.message)
    assert "header_present=True" in record.message
    assert "header_len=16" in record.message


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
    del payload["data"]["custom_data"]["installation_token"]
    body = json.dumps(payload).encode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhooks/paddle", content=body, headers={"paddle-signature": _sign(body)})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_a_raw_unsigned_installation_id_is_rejected_not_trusted(pool):
    """The actual vulnerability this closes: custom_data is set by the
    browser calling Paddle.Checkout.open(), which nothing stops from being
    called directly with any value - a raw installation_id, spoofing a
    victim's id, must not be trusted just because it looks like a valid
    integer. Only a signed installation_token, minted server-side for a
    session that was already verified to administer that installation, may
    name one."""
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (950, 'victim-org', 'air')"
    )
    payload = _subscription_event_payload(
        "subscription.canceled", "canceled", 950, event_id="evt_spoofed"
    )
    # Simulates an attacker calling Paddle.Checkout.open() from the browser
    # console with a raw custom_data.installation_id naming a victim's
    # installation - the exact shape this codebase used to accept.
    payload["data"]["custom_data"] = {"installation_id": "950"}

    await handle_paddle_webhook_event(payload, pool, "redis://unused")

    installation = await get_installation(pool, 950)
    assert installation["plan"] == "air", "a spoofed raw installation_id must not downgrade a real customer"


@pytest.mark.asyncio
async def test_a_tampered_installation_token_is_rejected(pool):
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (951, 'victim-org2', 'air')"
    )
    payload = _subscription_event_payload("subscription.canceled", "canceled", 951, event_id="evt_tampered")
    # A token minted for a different installation, spliced onto this
    # event - must not be accepted for 951 just because it's a
    # well-formed, validly-signed token for *something*.
    payload["data"]["custom_data"]["installation_token"] = _installation_token(952)

    await handle_paddle_webhook_event(payload, pool, "redis://unused")

    installation = await get_installation(pool, 951)
    assert installation["plan"] == "air"


@pytest.mark.asyncio
async def test_a_forged_installation_token_is_rejected(pool):
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (953, 'victim-org3', 'air')"
    )
    payload = _subscription_event_payload("subscription.canceled", "canceled", 953, event_id="evt_forged")
    real_token = payload["data"]["custom_data"]["installation_token"]
    # Flipped mid-string, not the trailing character: base64's own padding
    # bits can leave the last character of a token free to change without
    # altering the decoded bytes at all, which would make this test pass
    # for the wrong reason (or flake, since the token itself is timestamp-
    # dependent and different on every run).
    middle = len(real_token) // 2
    flipped_char = "x" if real_token[middle] != "x" else "y"
    payload["data"]["custom_data"]["installation_token"] = (
        real_token[:middle] + flipped_char + real_token[middle + 1 :]
    )

    await handle_paddle_webhook_event(payload, pool, "redis://unused")

    installation = await get_installation(pool, 953)
    assert installation["plan"] == "air"


@pytest.mark.asyncio
async def test_customer_id_mismatch_is_rejected_even_with_a_valid_token(pool):
    """Defense in depth beyond the signed token: once an installation has a
    real Paddle customer on file, an event claiming a different customer_id
    must not mutate it, even if it somehow carried a validly-signed token -
    this is what closes the billing-portal-hijack path if a future change
    ever reintroduced a spoofable identifier into custom_data."""
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan, paddle_customer_id) "
        "VALUES (954, 'victim-org4', 'air', 'ctm_real_customer')"
    )
    payload = _subscription_event_payload("subscription.canceled", "canceled", 954, event_id="evt_mismatch")
    payload["data"]["customer_id"] = "ctm_attacker_customer"

    await handle_paddle_webhook_event(payload, pool, "redis://unused")

    installation = await get_installation(pool, 954)
    assert installation["plan"] == "air"
    assert installation["paddle_customer_id"] == "ctm_real_customer"


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
            "custom_data": {"installation_token": _installation_token(305)},
            "items": [
                {"price": {"id": "pri_01kyhevc8bkcghfpwjymz16y2h"}, "quantity": 1},
                {"price": {"id": EXTRA_SEAT_PRICE_ID}, "quantity": 3},
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


@pytest.mark.asyncio
async def test_past_due_from_paid_enqueues_payment_failed_email_to_members(pool, monkeypatch):
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (700, 'acme', 'air')"
    )
    await add_installation_member(pool, 700, "alice", "alice")
    await upsert_github_user_email(pool, "alice", "alice@example.com")

    enqueue_calls = []
    monkeypatch.setattr(
        "app_server.webhooks.paddle.enqueue_transactional_email",
        lambda redis_url, **kwargs: enqueue_calls.append(kwargs),
    )

    payload = _subscription_event_payload("subscription.updated", "past_due", 700)
    payload["event_id"] = "evt_past_due_1"

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    assert len(enqueue_calls) == 1
    call = enqueue_calls[0]
    assert call["template_name"] == "payment_failed"
    assert call["template_arg"] == "acme"
    assert call["to_email"] == "alice@example.com"
    assert call["dedupe_key"] == "payment_failed:evt_past_due_1:alice@example.com"
    assert call["installation_id"] == 700


@pytest.mark.asyncio
async def test_canceled_from_paid_enqueues_win_back_email(pool, monkeypatch):
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (701, 'acme', 'air')"
    )
    await add_installation_member(pool, 701, "alice", "alice")
    await upsert_github_user_email(pool, "alice", "alice@example.com")

    enqueue_calls = []
    monkeypatch.setattr(
        "app_server.webhooks.paddle.enqueue_transactional_email",
        lambda redis_url, **kwargs: enqueue_calls.append(kwargs),
    )

    payload = _subscription_event_payload("subscription.canceled", "canceled", 701)
    payload["event_id"] = "evt_canceled_1"

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    assert len(enqueue_calls) == 1
    assert enqueue_calls[0]["template_name"] == "subscription_canceled"
    assert enqueue_calls[0]["dedupe_key"] == "subscription_canceled:evt_canceled_1:alice@example.com"


@pytest.mark.asyncio
async def test_no_email_enqueued_when_installation_was_already_free(pool, monkeypatch):
    fake_queue = MagicMock()
    await upsert_installation(pool, 702, "acme")  # defaults to plan='free'
    await add_installation_member(pool, 702, "alice", "alice")
    await upsert_github_user_email(pool, "alice", "alice@example.com")

    enqueue_calls = []
    monkeypatch.setattr(
        "app_server.webhooks.paddle.enqueue_transactional_email",
        lambda redis_url, **kwargs: enqueue_calls.append(kwargs),
    )

    payload = _subscription_event_payload("subscription.updated", "past_due", 702)
    payload["event_id"] = "evt_already_free"

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    assert enqueue_calls == []


@pytest.mark.asyncio
async def test_no_email_enqueued_for_members_who_have_never_logged_in(pool, monkeypatch):
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (703, 'acme', 'air')"
    )
    # bob was added by username but has never logged in - no captured
    # email, so no row to send to. Deliberate v1 scope, not a bug.
    await add_installation_member(pool, 703, "bob", "bob")

    enqueue_calls = []
    monkeypatch.setattr(
        "app_server.webhooks.paddle.enqueue_transactional_email",
        lambda redis_url, **kwargs: enqueue_calls.append(kwargs),
    )

    payload = _subscription_event_payload("subscription.canceled", "canceled", 703)
    payload["event_id"] = "evt_no_email_on_file"

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    assert enqueue_calls == []


@pytest.mark.asyncio
async def test_no_email_enqueued_without_event_id(pool, monkeypatch):
    # No event_id means no safe dedupe_key - skip rather than risk a
    # duplicate send on a retried webhook that somehow lacks one.
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (704, 'acme', 'air')"
    )
    await add_installation_member(pool, 704, "alice", "alice")
    await upsert_github_user_email(pool, "alice", "alice@example.com")

    enqueue_calls = []
    monkeypatch.setattr(
        "app_server.webhooks.paddle.enqueue_transactional_email",
        lambda redis_url, **kwargs: enqueue_calls.append(kwargs),
    )

    # Explicitly event_id-less. The /webhooks/paddle route now rejects such
    # a payload outright, but the handler keeps its own guard - it is called
    # directly here and from tests, and an email dedupe key built from a
    # missing id would collide across unrelated events.
    payload = _subscription_event_payload("subscription.updated", "past_due", 704, event_id=None)

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    assert enqueue_calls == []


# ---------------------------------------------------------------------------
# Delivery dedupe. Paddle signatures already embed a timestamp checked to a 5s
# tolerance, so replay of a captured payload is a narrow window. These cover
# the concurrency case instead: handle_paddle_webhook_event reads
# installations.plan, then writes it, and gates a pair of expensive full
# AIRview/Docs builds on that read having been "free". Two deliveries of one
# event arriving together would both read "free" and both enqueue.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_paddle_event_is_only_handled_once(pool, monkeypatch):
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    app.state.db_pool = pool
    await upsert_installation(pool, 800, "acme")

    handled = []

    async def counting_handler(payload, pool_arg, redis_url, queue=None):
        handled.append(payload)

    monkeypatch.setattr(
        "app_server.webhooks.paddle.handle_paddle_webhook_event", counting_handler
    )

    body = json.dumps(
        _subscription_created_payload(
            "pri_01kyhevc8bkcghfpwjymz16y2h", 800, event_id="evt_dupe_1"
        )
    ).encode()
    headers = {"paddle-signature": _sign(body)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/webhooks/paddle", content=body, headers=headers)
        second = await client.post("/webhooks/paddle", content=body, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(handled) == 1, "a duplicate Paddle event was processed twice"


@pytest.mark.asyncio
async def test_distinct_paddle_events_are_both_handled(pool, monkeypatch):
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    app.state.db_pool = pool
    await upsert_installation(pool, 801, "acme")

    handled = []

    async def counting_handler(payload, pool_arg, redis_url, queue=None):
        handled.append(payload)

    monkeypatch.setattr(
        "app_server.webhooks.paddle.handle_paddle_webhook_event", counting_handler
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for event_id in ("evt_distinct_1", "evt_distinct_2"):
            body = json.dumps(
                _subscription_created_payload(
                    "pri_01kyhevc8bkcghfpwjymz16y2h", 801, event_id=event_id
                )
            ).encode()
            await client.post(
                "/webhooks/paddle", content=body, headers={"paddle-signature": _sign(body)}
            )

    assert len(handled) == 2


@pytest.mark.asyncio
async def test_paddle_event_without_event_id_is_rejected(pool, monkeypatch):
    # Undedupable. Accepting it would leave the concurrency gap open for any
    # caller willing to omit the field.
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    app.state.db_pool = pool
    payload = _subscription_created_payload("pri_01kyhevc8bkcghfpwjymz16y2h", 802)
    del payload["event_id"]
    body = json.dumps(payload).encode()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/webhooks/paddle", content=body, headers={"paddle-signature": _sign(body)}
        )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_forged_paddle_signature_never_claims_an_event_id(pool, monkeypatch):
    # Otherwise anyone could burn an event id and suppress the real delivery.
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    app.state.db_pool = pool
    body = json.dumps(
        _subscription_created_payload(
            "pri_01kyhevc8bkcghfpwjymz16y2h", 803, event_id="evt_forged_1"
        )
    ).encode()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/webhooks/paddle", content=body, headers={"paddle-signature": "ts=1;h1=deadbeef"}
        )

    assert response.status_code == 401
    assert await claim_webhook_delivery(pool, "paddle", "evt_forged_1", "") is True


@pytest.mark.asyncio
async def test_failed_paddle_handler_releases_the_claim_for_retry(pool, monkeypatch):
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    app.state.db_pool = pool
    await upsert_installation(pool, 804, "acme")
    attempts = []

    async def failing_handler(payload, pool_arg, redis_url, queue=None):
        attempts.append(payload)
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr("app_server.webhooks.paddle.handle_paddle_webhook_event", failing_handler)

    body = json.dumps(
        _subscription_created_payload(
            "pri_01kyhevc8bkcghfpwjymz16y2h", 804, event_id="evt_retry_1"
        )
    ).encode()
    headers = {"paddle-signature": _sign(body)}

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post("/webhooks/paddle", content=body, headers=headers)
        assert first.status_code == 500

        # Paddle's retry carries the same event_id. A stuck claim would drop
        # it and the plan change would be lost for good.
        async def working_handler(payload, pool_arg, redis_url, queue=None):
            attempts.append(payload)

        monkeypatch.setattr(
            "app_server.webhooks.paddle.handle_paddle_webhook_event", working_handler
        )
        second = await client.post("/webhooks/paddle", content=body, headers=headers)

    assert second.status_code == 200
    assert len(attempts) == 2, "retry of a failed Paddle event was wrongly suppressed"


# ---------------------------------------------------------------------------
# Affiliate program: referral attribution on subscription.created, and
# commission recording on transaction.completed. See
# docs/superpowers/specs/2026-08-10-aletheore-affiliate-program-design.md.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscription_created_with_known_discount_id_records_referral(pool):
    fake_queue = MagicMock()
    affiliate = await create_affiliate(pool, "SARAH10", "dsc_sarah_wh", "Sarah")
    await upsert_installation(pool, 900, "acme")

    payload = _subscription_created_payload(
        "pri_01kyhevc8bkcghfpwjymz16y2h", 900, discount_id="dsc_sarah_wh"
    )
    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    referral = await get_referral(pool, 900)
    assert referral is not None
    assert referral["affiliate_id"] == affiliate["id"]


@pytest.mark.asyncio
async def test_subscription_created_with_unknown_discount_id_creates_no_referral(pool):
    fake_queue = MagicMock()
    await upsert_installation(pool, 901, "acme")

    payload = _subscription_created_payload(
        "pri_01kyhevc8bkcghfpwjymz16y2h", 901, discount_id="dsc_totally_unknown"
    )
    # Must not raise despite the discount id not matching any affiliate.
    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    assert await get_referral(pool, 901) is None


@pytest.mark.asyncio
async def test_subscription_created_without_discount_id_creates_no_referral(pool):
    fake_queue = MagicMock()
    await upsert_installation(pool, 902, "acme")

    payload = _subscription_created_payload("pri_01kyhevc8bkcghfpwjymz16y2h", 902)
    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    assert await get_referral(pool, 902) is None


@pytest.mark.asyncio
async def test_paid_to_paid_change_with_discount_id_does_not_attribute(pool):
    # Referral attribution is gated on the free -> paid transition, same as
    # the one-time wiki build - a later subscription.updated for an
    # already-paid installation must not create or steal a referral.
    affiliate = await create_affiliate(pool, "TINA10", "dsc_tina_wh", "Tina")
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (903, 'acme', 'air')"
    )

    payload = _subscription_created_payload(
        "pri_01kyhevc9xn6z2nghmy8057jvp", 903, discount_id="dsc_tina_wh"
    )
    await handle_paddle_webhook_event(payload, pool, "redis://unused")

    assert await get_referral(pool, 903) is None


@pytest.mark.asyncio
async def test_transaction_completed_for_referred_installation_records_commission(pool):
    affiliate = await create_affiliate(pool, "NORA10", "dsc_nora_wh", "Nora")
    await upsert_installation(pool, 910, "acme")
    await record_referral(pool, 910, affiliate["id"])

    # $26.99 (2699 cents) net of that transaction's own discount - 15% of it
    # is $4.05 (rounded from 4.0485).
    payload = _transaction_completed_payload(910, "2699")
    await handle_paddle_webhook_event(payload, pool, "redis://unused")

    totals = {row["id"]: row for row in await list_affiliates_with_totals(pool)}
    assert totals[affiliate["id"]]["total_owed_usd"] == Decimal("4.05")


@pytest.mark.asyncio
async def test_transaction_completed_for_unreferred_installation_records_nothing(pool):
    await upsert_installation(pool, 911, "acme")

    payload = _transaction_completed_payload(911, "2699")
    await handle_paddle_webhook_event(payload, pool, "redis://unused")

    assert await list_affiliates_with_totals(pool) == []


@pytest.mark.asyncio
async def test_repeated_transaction_completed_delivery_does_not_double_commission(pool):
    # Paddle retries webhook delivery on any non-2xx response, re-sending
    # the same transaction id - the route-level dedupe (webhook_deliveries)
    # already guards this at the HTTP layer, but the handler itself must
    # also be safe if ever called twice for the same transaction.
    affiliate = await create_affiliate(pool, "OLA10", "dsc_ola_wh", "Ola")
    await upsert_installation(pool, 912, "acme")
    await record_referral(pool, 912, affiliate["id"])

    payload = _transaction_completed_payload(912, "2699", transaction_id="txn_repeat")
    await handle_paddle_webhook_event(payload, pool, "redis://unused")
    await handle_paddle_webhook_event(payload, pool, "redis://unused")

    totals = {row["id"]: row for row in await list_affiliates_with_totals(pool)}
    assert totals[affiliate["id"]]["total_owed_usd"] == Decimal("4.05")


@pytest.mark.asyncio
async def test_transaction_completed_missing_installation_id_does_not_error(pool):
    payload = _transaction_completed_payload(913, "2699")
    del payload["data"]["custom_data"]["installation_token"]

    # Must not raise.
    await handle_paddle_webhook_event(payload, pool, "redis://unused")


@pytest.mark.asyncio
async def test_full_webhook_route_records_commission_for_referred_installation(pool, monkeypatch):
    monkeypatch.setenv("PADDLE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    app.state.db_pool = pool
    affiliate = await create_affiliate(pool, "PIA10", "dsc_pia_wh", "Pia")
    await upsert_installation(pool, 914, "acme")
    await record_referral(pool, 914, affiliate["id"])

    body = json.dumps(_transaction_completed_payload(914, "2699", event_id="evt_txn_route")).encode()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/webhooks/paddle", content=body, headers={"paddle-signature": _sign(body)}
        )

    assert response.status_code == 200
    totals = {row["id"]: row for row in await list_affiliates_with_totals(pool)}
    assert totals[affiliate["id"]]["total_owed_usd"] == Decimal("4.05")
