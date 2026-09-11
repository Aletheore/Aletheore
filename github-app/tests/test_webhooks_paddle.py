import hashlib
import hmac
import ipaddress
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app_server import paddle_ip_allowlist
from app_server.affiliates import create_affiliate, get_referral, list_affiliates_with_totals, record_referral
from app_server.auth import sign_checkout_installation_id
from app_server.db import (
    add_installation_member,
    claim_free_to_paid_plan,
    claim_webhook_delivery,
    credit_extra_seat_purchase,
    credit_topup_purchase,
    get_extra_seats,
    get_installation,
    reset_billing_period_credit,
    upsert_github_user_email,
    upsert_installation,
)
from app_server.main import app
from app_server.paddle_pricing import (
    CREDIT_TOPUP_PRICE_ID,
    EXTRA_SEAT_PRICE_ID,
    PLAN_INTERVAL_TO_PRICE_ID,
)
from app_server.webhooks.paddle import handle_paddle_webhook_event
from scan_worker.db import list_installations_due_for_monthly_credit_reset
from scan_worker.jobs import run_monthly_credit_reset_sweep_job

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:test@localhost:55433/aletheore_test",
)

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
async def test_missing_installation_token_fires_an_ops_alert(pool, monkeypatch):
    """Real gap this closes: a subscription event with a missing/invalid
    installation_token used to only logger.warning() and return - no
    retry (still 200 to Paddle), no alert, so a real payer's plan-flip
    silently failing looked identical to a successful transaction from
    the outside. This is a real-money path (about to onboard a real
    affiliate's referral) - it needs to page someone, not just log."""
    from app_server.webhooks import paddle as paddle_module

    alerts = []
    monkeypatch.setattr(paddle_module, "send_error_alert", lambda *a, **k: alerts.append((a, k)))

    payload = _subscription_created_payload(
        "pri_01kyhevc8bkcghfpwjymz16y2h", 104, event_id="evt_missing_token"
    )
    del payload["data"]["custom_data"]["installation_token"]

    await handle_paddle_webhook_event(payload, pool, "redis://unused")

    assert len(alerts) == 1
    args, kwargs = alerts[0]
    assert args[0] == "paddle_webhook"
    assert "evt_missing_token" in (args[2] if len(args) > 2 else kwargs.get("context", ""))


@pytest.mark.asyncio
async def test_invalid_installation_token_fires_an_ops_alert(pool, monkeypatch):
    """Same as the missing-token case above, but for a token that's
    present and well-formed yet fails to unsign (tampered, forged, or
    for a different secret) - unsign_checkout_installation_id returns
    None for all of these, hitting the exact same silent-failure path."""
    from app_server.webhooks import paddle as paddle_module

    alerts = []
    monkeypatch.setattr(paddle_module, "send_error_alert", lambda *a, **k: alerts.append((a, k)))

    payload = _subscription_event_payload(
        "subscription.canceled", "canceled", 951, event_id="evt_invalid_token"
    )
    payload["data"]["custom_data"]["installation_token"] = "not-a-real-token"

    await handle_paddle_webhook_event(payload, pool, "redis://unused")

    assert len(alerts) == 1
    assert alerts[0][0][0] == "paddle_webhook"


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
async def test_free_to_flash_transition_does_not_trigger_live_wiki_full_build(pool):
    """Real bug this guards: should_run_paid_setup predates the flash tier
    and used to mean "is air" by construction. AIRview/Docs are AIR-only -
    a flash signup enqueueing a full build would have spent real money
    against an $8/mo plan's own small monthly cap."""
    fake_queue = MagicMock()
    await upsert_installation(pool, 202, "acme")  # defaults to plan='free'

    payload = _subscription_created_payload("pri_01m1dj0m1netz6ze1mmckz73nm", 202)
    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    installation = await get_installation(pool, 202)
    assert installation["plan"] == "flash"
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_free_to_flash_transition_with_known_discount_id_still_records_referral(pool):
    """Affiliate attribution is deliberately NOT gated on plan == "air" -
    an affiliate should get credit for a flash referral too, even though
    the AIRview/Docs build (tested above) correctly does not fire."""
    fake_queue = MagicMock()
    affiliate = await create_affiliate(pool, "SARAH10", "dsc_sarah_wh", "Sarah")
    await upsert_installation(pool, 203, "acme")

    payload = _subscription_created_payload(
        "pri_01m1dj0m1netz6ze1mmckz73nm", 203, discount_id="dsc_sarah_wh"
    )
    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    referral = await get_referral(pool, 203)
    assert referral is not None
    assert referral["affiliate_id"] == affiliate["id"]
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_flash_to_air_upgrade_does_not_retrigger_live_wiki_full_build(pool):
    """The one-time paid-setup claim was already consumed on this
    installation's original free -> flash transition, so an upgrade to air
    does not get the instant build here - it self-heals via the AIR-only
    catch-up sweep instead (scan_worker/db.py's
    list_paid_repos_due_for_wiki_catchup/list_paid_repos_due_for_docs_catchup),
    which finds a "never swept" row the moment the installation becomes
    eligible. This test only pins down the webhook handler's own half:
    no double-build, no crash."""
    fake_queue = MagicMock()
    await upsert_installation(pool, 204, "acme")
    await handle_paddle_webhook_event(
        _subscription_created_payload("pri_01m1dj0m1netz6ze1mmckz73nm", 204, event_id="evt_flash_first"),
        pool,
        "redis://unused",
        queue=fake_queue,
    )
    installation = await get_installation(pool, 204)
    assert installation["plan"] == "flash"
    fake_queue.reset_mock()

    await handle_paddle_webhook_event(
        _subscription_created_payload("pri_01kyhevc8bkcghfpwjymz16y2h", 204, event_id="evt_flash_to_air"),
        pool,
        "redis://unused",
        queue=fake_queue,
    )

    installation = await get_installation(pool, 204)
    assert installation["plan"] == "air"
    fake_queue.enqueue.assert_not_called()


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
async def test_crash_between_writes_rolls_back_the_plan_change_atomically(pool):
    # docs/audits/Claude_Audit.md finding 11: the plan write, Paddle-id
    # write, and extra-seats write used to be three independent awaits - a
    # crash between any two of them left the installation on the new plan
    # with stale extra_seats, or upgraded with no Paddle IDs recorded, a
    # state no near-term retry could see coming since claim_webhook_delivery
    # already thinks this event was claimed. Confirmed live before the fix:
    # injecting a crash right before set_extra_seats left plan='air' and
    # paddle_subscription_id='sub_repro' committed with extra_seats never
    # applied. One transaction means the crash now rolls back everything,
    # not just some of it.
    import app_server.webhooks.paddle as paddle_mod

    await upsert_installation(pool, 206, "acme")  # defaults to plan='free'
    payload = {
        "event_id": "evt_crash_mid_write",
        "event_type": "subscription.created",
        "data": {
            "id": "sub_should_not_persist",
            "customer_id": "ctm_should_not_persist",
            "status": "active",
            "custom_data": {"installation_token": _installation_token(206)},
            "items": [
                {"price": {"id": "pri_01kyhevc8bkcghfpwjymz16y2h"}, "quantity": 1},
                {"price": {"id": EXTRA_SEAT_PRICE_ID}, "quantity": 2},
            ],
        },
    }

    async def _crash(*a, **k):
        raise RuntimeError("simulated crash: pod killed here")

    with patch.object(paddle_mod, "set_extra_seats", _crash):
        with pytest.raises(RuntimeError):
            await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=MagicMock())

    installation = await get_installation(pool, 206)
    assert installation["plan"] == "free"
    assert installation["paddle_subscription_id"] is None
    assert await get_extra_seats(pool, 206) == 0


@pytest.mark.asyncio
async def test_crash_between_reset_and_plan_write_rolls_back_the_credit_reset_too(pool):
    # Fix round 1 regression test: reset_billing_period_credit used to run
    # as its own standalone call BEFORE the "one transaction" block below,
    # so a crash between the two left base_credit_remaining_usd/
    # current_billing_period_start/balance_epoch already committed to the
    # new period while plan/extra_seats/Paddle IDs stayed stale - exactly
    # the split-write hazard the block's own comment (and the test above)
    # documents, just with the reset on the wrong side of the boundary.
    # Folding the reset into the same `conn`/`conn.transaction()` as the
    # rest means a crash after the reset runs (here, injected at
    # set_extra_seats, same crash point as the test above) must now roll
    # the reset back too - proven below by asserting the credit/period/
    # epoch columns are untouched, not just plan/extra_seats/Paddle IDs.
    import app_server.webhooks.paddle as paddle_mod

    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan, base_credit_remaining_usd) "
        "VALUES (407, 'acme', 'air', 2.00)"
    )
    payload = {
        "event_id": "evt_crash_mid_reset_407",
        "event_type": "subscription.updated",
        "data": {
            "id": "sub_should_not_persist_407",
            "customer_id": "ctm_should_not_persist_407",
            "status": "active",
            "custom_data": {"installation_token": _installation_token(407)},
            "items": [{"price": {"id": "pri_01kyhevc8bkcghfpwjymz16y2h"}, "quantity": 1}],
            "current_billing_period": {"starts_at": "2026-09-01T00:00:00Z"},
        },
    }

    async def _crash(*a, **k):
        raise RuntimeError("simulated crash: pod killed here")

    with patch.object(paddle_mod, "set_extra_seats", _crash):
        with pytest.raises(RuntimeError):
            await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=MagicMock())

    row = await pool.fetchrow(
        "SELECT base_credit_remaining_usd, current_billing_period_start, balance_epoch, "
        "paddle_subscription_id FROM installations WHERE installation_id = $1",
        407,
    )
    # The reset (18.00, a real current_billing_period_start, balance_epoch
    # 1) must NOT have survived the crash, exactly like the Paddle-id write
    # below it in the same transaction didn't.
    assert float(row["base_credit_remaining_usd"]) == pytest.approx(2.00)
    assert row["current_billing_period_start"] is None
    assert row["balance_epoch"] == 0
    assert row["paddle_subscription_id"] is None


@pytest.mark.asyncio
async def test_crash_after_plan_write_still_runs_setup_on_retry(pool):
    """Simulates a process death between the plan write committing and
    setup (wiki/docs build, attribution) running: claim_free_to_paid_plan
    already flipped the plan to 'air' and reset paid_setup_completed_at to
    NULL (a real free->paid transition happened), but nothing past that
    point ran - as if the process died right there. claim_free_to_paid_plan
    would return False on a retry (plan isn't 'free' anymore) and silently
    skip setup forever if setup were gated on that transition boolean - the
    retry must still run it because setup itself was never actually
    claimed."""
    fake_queue = MagicMock()
    await upsert_installation(pool, 203, "acme")  # defaults to plan='free'
    assert await claim_free_to_paid_plan(pool, 203, "air") is True  # the crashed attempt

    payload = _subscription_created_payload("pri_01kyhevc8bkcghfpwjymz16y2h", 203)
    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    assert fake_queue.enqueue.call_count == 2
    job_names = {call.args[0] for call in fake_queue.enqueue.call_args_list}
    assert job_names == {
        "scan_worker.jobs.run_live_wiki_full_build_for_installation_job",
        "scan_worker.jobs.run_live_docs_full_build_for_installation_job",
    }


@pytest.mark.asyncio
async def test_setup_runs_only_once_across_repeated_deliveries(pool):
    fake_queue = MagicMock()
    await upsert_installation(pool, 204, "acme")  # defaults to plan='free'
    payload = _subscription_created_payload("pri_01kyhevc8bkcghfpwjymz16y2h", 204)

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)
    assert fake_queue.enqueue.call_count == 2

    fake_queue.reset_mock()
    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)
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
async def test_canceling_an_annual_air_subscription_disarms_the_monthly_credit_clock(pool):
    # A downgrade to a monthly price already disarms next_monthly_credit_
    # reset_at via reset_billing_period_credit's own is_annual=False write
    # (see test_a_monthly_renewal_disarms_a_previously_armed_monthly_clock)
    # - but a full CANCEL never reaches that function at all (it's gated
    # on plan != "free"), so nothing else ever cleared this column on that
    # transition. Left armed, run_monthly_credit_reset_sweep_job keeps
    # matching this now-free installation on every sweep tick forever: no
    # money is at risk (base_credit_for_plan("free", ...) is 0), but the
    # clock keeps advancing and balance_epoch keeps climbing on a churned
    # row that will never look at either again.
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan, "
        "base_credit_remaining_usd, base_credit_allotment_usd, "
        "next_monthly_credit_reset_at, balance_epoch) "
        "VALUES (303, 'churn-co', 'air', 18.00, 18.00, "
        "'2026-08-31T00:00:00Z'::timestamptz, 1)"
    )
    payload = _subscription_event_payload("subscription.canceled", "canceled", 303)

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    installation = await get_installation(pool, 303)
    assert installation["plan"] == "free"
    row = await pool.fetchrow(
        "SELECT next_monthly_credit_reset_at FROM installations WHERE installation_id = $1", 303
    )
    assert row["next_monthly_credit_reset_at"] is None
    assert 303 not in list_installations_due_for_monthly_credit_reset(TEST_DATABASE_URL)


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
async def test_subscription_updated_with_a_null_seat_quantity_does_not_crash(pool):
    # Real bug found via audit: item.get("quantity", 0) only supplies its
    # default when the key is MISSING, not when Paddle sends it present
    # with an explicit null - crashed the whole webhook handler with an
    # unhandled TypeError, permanently stuck (Paddle keeps retrying the
    # same payload) rather than a legitimate plan/seat change ever
    # applying for that customer.
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan, extra_seats) "
        "VALUES (308, 'acme', 'air', 3)"
    )
    payload = {
        "event_type": "subscription.updated",
        "data": {
            "id": "sub_test_308",
            "customer_id": "ctm_test_308",
            "status": "active",
            "custom_data": {"installation_token": _installation_token(308)},
            "items": [
                {"price": {"id": "pri_01kyhevc8bkcghfpwjymz16y2h"}, "quantity": 1},
                {"price": {"id": EXTRA_SEAT_PRICE_ID}, "quantity": None},
            ],
        },
    }

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    assert await get_extra_seats(pool, 308) == 0


@pytest.mark.asyncio
async def test_subscription_updated_with_a_string_seat_quantity_reconciles_correctly(pool):
    # Same gap as the null case above - Paddle's own real quantity field is
    # always an int, but this must not crash on a non-int shape either.
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan, extra_seats) "
        "VALUES (309, 'acme', 'air', 0)"
    )
    payload = {
        "event_type": "subscription.updated",
        "data": {
            "id": "sub_test_309",
            "customer_id": "ctm_test_309",
            "status": "active",
            "custom_data": {"installation_token": _installation_token(309)},
            "items": [
                {"price": {"id": "pri_01kyhevc8bkcghfpwjymz16y2h"}, "quantity": 1},
                {"price": {"id": EXTRA_SEAT_PRICE_ID}, "quantity": "3"},
            ],
        },
    }

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    assert await get_extra_seats(pool, 309) == 3


@pytest.mark.asyncio
async def test_subscription_updated_with_a_non_finite_seat_quantity_does_not_crash(pool):
    # Flash Review finding on the null/string-quantity fix above: the float
    # branch called int(quantity) unconditionally, but JSON's own grammar
    # has no literal for inf/-inf/nan - Python's json module accepts them
    # anyway (a real, if nonstandard, shape a sender can transmit), and
    # int() on either raises OverflowError (inf) or ValueError (nan), the
    # exact same "crash the whole webhook handler" failure this function
    # exists to close.
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan, extra_seats) "
        "VALUES (310, 'acme', 'air', 3)"
    )
    payload = {
        "event_type": "subscription.updated",
        "data": {
            "id": "sub_test_310",
            "customer_id": "ctm_test_310",
            "status": "active",
            "custom_data": {"installation_token": _installation_token(310)},
            "items": [
                {"price": {"id": "pri_01kyhevc8bkcghfpwjymz16y2h"}, "quantity": 1},
                {"price": {"id": EXTRA_SEAT_PRICE_ID}, "quantity": float("nan")},
            ],
        },
    }

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    assert await get_extra_seats(pool, 310) == 0


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
    assert call["template_arg"] == {"account_login": "acme", "plan": "air"}
    assert call["to_email"] == "alice@example.com"
    assert call["dedupe_key"] == "payment_failed:evt_past_due_1:alice@example.com"
    assert call["installation_id"] == 700


@pytest.mark.asyncio
async def test_past_due_from_flash_enqueues_payment_failed_email_naming_flash(pool, monkeypatch):
    """Real bug this guards: template_arg used to be a bare account_login
    string, so payment_failed_email always rendered "AIR" regardless of
    the installation's actual plan - a flash customer's declined card
    produced an email naming a plan and feature list they never had."""
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (703, 'acme', 'flash')"
    )
    await add_installation_member(pool, 703, "alice", "alice")
    await upsert_github_user_email(pool, "alice", "alice@example.com")

    enqueue_calls = []
    monkeypatch.setattr(
        "app_server.webhooks.paddle.enqueue_transactional_email",
        lambda redis_url, **kwargs: enqueue_calls.append(kwargs),
    )

    payload = _subscription_event_payload("subscription.updated", "past_due", 703)
    payload["event_id"] = "evt_past_due_flash"

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    assert len(enqueue_calls) == 1
    assert enqueue_calls[0]["template_arg"] == {"account_login": "acme", "plan": "flash"}


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
    assert enqueue_calls[0]["template_arg"] == {"account_login": "acme", "plan": "air"}


@pytest.mark.asyncio
async def test_canceled_from_flash_enqueues_win_back_email_naming_flash(pool, monkeypatch):
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (704, 'acme', 'flash')"
    )
    await add_installation_member(pool, 704, "alice", "alice")
    await upsert_github_user_email(pool, "alice", "alice@example.com")

    enqueue_calls = []
    monkeypatch.setattr(
        "app_server.webhooks.paddle.enqueue_transactional_email",
        lambda redis_url, **kwargs: enqueue_calls.append(kwargs),
    )

    payload = _subscription_event_payload("subscription.canceled", "canceled", 704)
    payload["event_id"] = "evt_canceled_flash"

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    assert len(enqueue_calls) == 1
    assert enqueue_calls[0]["template_arg"] == {"account_login": "acme", "plan": "flash"}


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


# --- reset_billing_period_credit / credit_topup_purchase (Task 5) ---
# Real per-installation dollar-credit balance mutations: a billing-period
# renewal resets base_credit_remaining_usd to the plan's real included
# credit, and a customer-purchased top-up adds to topup_credit_balance_usd
# exactly once per Paddle transaction id. Both increment balance_epoch,
# the dedupe key the low-balance/exhausted credit-notification emails
# (Task 6) key off of.


@pytest.mark.asyncio
async def test_reset_billing_period_credit_on_genuine_new_period(pool):
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (401, 'acme', 'air')"
    )

    changed = await reset_billing_period_credit(
        pool, 401, "air", extra_seats=0, period_start="2026-09-01T00:00:00Z", is_annual=False
    )

    assert changed is True
    row = await pool.fetchrow(
        "SELECT base_credit_remaining_usd, base_credit_allotment_usd, "
        "current_billing_period_start, balance_epoch "
        "FROM installations WHERE installation_id = $1",
        401,
    )
    assert float(row["base_credit_remaining_usd"]) == pytest.approx(18.00)
    # The ceiling resets with the balance, to the same number: it is what
    # release_llm_spend_reservation caps a true-up refill at, so a stale
    # allotment from a previous period/seat count would either strand credit
    # (too low) or let base credit leak into the never-expiring topup bucket
    # (too high).
    assert float(row["base_credit_allotment_usd"]) == pytest.approx(18.00)
    assert row["balance_epoch"] == 1


@pytest.mark.asyncio
async def test_reset_billing_period_credit_is_a_noop_on_the_same_period(pool):
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (402, 'acme', 'air')"
    )
    await reset_billing_period_credit(
        pool, 402, "air", extra_seats=0, period_start="2026-09-01T00:00:00Z", is_annual=False
    )
    # Spend some of it down.
    await pool.execute(
        "UPDATE installations SET base_credit_remaining_usd = 2.00 WHERE installation_id = $1",
        402,
    )

    # Same period_start delivered again (a replayed or unrelated subscription.updated).
    changed = await reset_billing_period_credit(
        pool, 402, "air", extra_seats=0, period_start="2026-09-01T00:00:00Z", is_annual=False
    )

    assert changed is False
    row = await pool.fetchrow(
        "SELECT base_credit_remaining_usd FROM installations WHERE installation_id = $1",
        402,
    )
    # Must NOT have been reset back to 18.00 - the spent-down 2.00 survives.
    assert float(row["base_credit_remaining_usd"]) == pytest.approx(2.00)


@pytest.mark.asyncio
async def test_reset_billing_period_credit_rejects_an_out_of_order_older_period(pool):
    # Paddle does not guarantee in-order webhook delivery - a retry of an
    # OLDER subscription.updated can land after a newer one already
    # committed. The old IS DISTINCT FROM guard only checked "different",
    # not "later", so a stale older event could re-fire the reset, wiping
    # out real spend-down progress and rewinding current_billing_period_
    # start backward.
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan, "
        "base_credit_remaining_usd, base_credit_allotment_usd, current_billing_period_start, balance_epoch) "
        "VALUES (409, 'acme', 'air', 2.50, 18.00, '2026-10-01T00:00:00Z', 5)"
    )

    # An older, out-of-order event for the PREVIOUS period arrives late.
    changed = await reset_billing_period_credit(
        pool, 409, "air", extra_seats=0, period_start="2026-09-01T00:00:00Z", is_annual=False
    )

    assert changed is False
    row = await pool.fetchrow(
        "SELECT base_credit_remaining_usd, current_billing_period_start, balance_epoch "
        "FROM installations WHERE installation_id = $1",
        409,
    )
    # Must NOT have wiped the real spent-down balance back to a fresh 18.00,
    # and must NOT have rewound current_billing_period_start backward.
    assert float(row["base_credit_remaining_usd"]) == pytest.approx(2.50)
    assert row["current_billing_period_start"].isoformat() == "2026-10-01T00:00:00+00:00"
    assert row["balance_epoch"] == 5

    # A genuinely newer period must still reset correctly.
    changed_forward = await reset_billing_period_credit(
        pool, 409, "air", extra_seats=0, period_start="2026-11-01T00:00:00Z", is_annual=False
    )
    assert changed_forward is True


@pytest.mark.asyncio
async def test_reset_billing_period_credit_arms_the_monthly_clock_for_an_annual_subscriber(pool):
    # An annual subscriber's Paddle billing period only advances once a
    # year, so this reset is the only one they will get from Paddle for the
    # next 12 months. next_monthly_credit_reset_at is what lets
    # run_monthly_credit_reset_sweep_job hand them the other 11 months of
    # the $18/month allotment they actually paid for.
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (420, 'annual-co', 'air')"
    )

    changed = await reset_billing_period_credit(
        pool, 420, "air", extra_seats=0, period_start="2026-09-01T00:00:00Z", is_annual=True
    )

    assert changed is True
    row = await pool.fetchrow(
        "SELECT base_credit_remaining_usd, next_monthly_credit_reset_at "
        "FROM installations WHERE installation_id = $1",
        420,
    )
    assert float(row["base_credit_remaining_usd"]) == pytest.approx(18.00)
    # One month past THIS reset, not past "now" - see the 30-day note in
    # reset_billing_period_credit's docstring on why dateutil's exact
    # relativedelta isn't used (it is not a dependency of this service).
    assert row["next_monthly_credit_reset_at"] == datetime(
        2026, 9, 1, tzinfo=timezone.utc
    ) + timedelta(days=30)


@pytest.mark.asyncio
async def test_reset_billing_period_credit_leaves_the_monthly_clock_null_for_a_monthly_subscriber(pool):
    # A monthly subscriber already gets a real reset from Paddle every
    # month, so the synthetic sweep must never see them: this reset plus a
    # sweep firing in the same month would credit them twice.
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (421, 'monthly-co', 'air')"
    )

    await reset_billing_period_credit(
        pool, 421, "air", extra_seats=0, period_start="2026-09-01T00:00:00Z", is_annual=False
    )

    row = await pool.fetchrow(
        "SELECT next_monthly_credit_reset_at FROM installations WHERE installation_id = $1", 421
    )
    assert row["next_monthly_credit_reset_at"] is None


@pytest.mark.asyncio
async def test_a_monthly_renewal_disarms_a_previously_armed_monthly_clock(pool):
    # A customer who downgrades from the annual price to the monthly one
    # keeps whatever next_monthly_credit_reset_at their annual renewal set,
    # unless a monthly reset actively clears it - and if it survived, they
    # would collect both a real monthly reset and a synthetic one every
    # month. is_annual=False writing NULL is what makes that impossible.
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan, "
        "next_monthly_credit_reset_at) "
        "VALUES (422, 'switched-co', 'air', '2026-10-01T00:00:00Z')"
    )

    await reset_billing_period_credit(
        pool, 422, "air", extra_seats=0, period_start="2026-09-15T00:00:00Z", is_annual=False
    )

    row = await pool.fetchrow(
        "SELECT next_monthly_credit_reset_at FROM installations WHERE installation_id = $1", 422
    )
    assert row["next_monthly_credit_reset_at"] is None


@pytest.mark.asyncio
async def test_credit_extra_seat_purchase_cannot_be_farmed_by_removing_and_re_adding_a_seat(pool):
    # Seat REMOVAL deliberately doesn't debit anything (the customer already
    # paid for the period the seat was bought in - see
    # test_seat_removal_does_not_change_the_credit_balance), and only an
    # increase credits. With an unconditional "+= EXTRA_SEAT_LLM_CAP_USD"
    # that made the pair asymmetric and self-service farmable: remove the
    # seat, re-add it, collect another $3.00, repeat, all within one billing
    # cycle and with no ceiling.
    #
    # The clamp at base_credit_for_plan(plan, extra_seats) - the CURRENT,
    # post-purchase seat count - closes it: the balance can never exceed
    # what the seats actually on the subscription right now entitle it to.
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan, extra_seats, "
        "base_credit_remaining_usd, base_credit_allotment_usd) "
        "VALUES (413, 'acme', 'flash', 0, 5.00, 5.00)"
    )

    # First purchase: 0 -> 1 seat. Ceiling is base_credit_for_plan("flash", 1)
    # = 5.00 + 3.00 = 8.00, and 5.00 + 3.00 lands exactly on it.
    await credit_extra_seat_purchase(pool, 413, added_seats=1, plan="flash", extra_seats=1)
    row = await pool.fetchrow(
        "SELECT base_credit_remaining_usd, base_credit_allotment_usd, balance_epoch "
        "FROM installations WHERE installation_id = $1",
        413,
    )
    assert float(row["base_credit_remaining_usd"]) == pytest.approx(8.00)
    assert float(row["base_credit_allotment_usd"]) == pytest.approx(8.00)
    assert row["balance_epoch"] == 1

    # The seat is now removed (no code path credits or debits for that) and
    # re-added: the second purchase is again "one seat added, one extra seat
    # in total afterwards", so the ceiling is still 8.00 - and the balance
    # stays 8.00 instead of ratcheting to 11.00.
    await credit_extra_seat_purchase(pool, 413, added_seats=1, plan="flash", extra_seats=1)
    row = await pool.fetchrow(
        "SELECT base_credit_remaining_usd, base_credit_allotment_usd FROM installations "
        "WHERE installation_id = $1",
        413,
    )
    assert float(row["base_credit_remaining_usd"]) == pytest.approx(8.00)
    assert float(row["base_credit_allotment_usd"]) == pytest.approx(8.00)


@pytest.mark.asyncio
async def test_credit_extra_seat_purchase_still_credits_a_spent_down_balance_in_full(pool):
    # The clamp must not turn into a silent no-op for the normal case: a
    # customer who has already spent most of the period's credit and then
    # buys a seat still gets the whole EXTRA_SEAT_LLM_CAP_USD bonus, because
    # 1.00 + 3.00 is nowhere near the 8.00 ceiling.
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan, extra_seats, "
        "base_credit_remaining_usd, base_credit_allotment_usd) "
        "VALUES (414, 'acme', 'flash', 0, 1.00, 5.00)"
    )

    await credit_extra_seat_purchase(pool, 414, added_seats=1, plan="flash", extra_seats=1)

    row = await pool.fetchrow(
        "SELECT base_credit_remaining_usd, base_credit_allotment_usd FROM installations "
        "WHERE installation_id = $1",
        414,
    )
    assert float(row["base_credit_remaining_usd"]) == pytest.approx(4.00)
    # The ceiling still moves to the new seat count's real allotment, even
    # though the balance is far below it.
    assert float(row["base_credit_allotment_usd"]) == pytest.approx(8.00)


@pytest.mark.asyncio
async def test_credit_topup_purchase_increments_topup_balance(pool):
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (403, 'acme', 'flash')"
    )

    credited = await credit_topup_purchase(pool, 403, 8.00, "txn_topup_403")

    assert credited is True
    row = await pool.fetchrow(
        "SELECT topup_credit_balance_usd, base_credit_remaining_usd, "
        "base_credit_allotment_usd, balance_epoch "
        "FROM installations WHERE installation_id = $1",
        403,
    )
    assert float(row["topup_credit_balance_usd"]) == pytest.approx(8.00)
    assert row["balance_epoch"] == 1
    # A purchased top-up is never-expiring credit and touches NEITHER base
    # column: raising base_credit_allotment_usd here would let the next
    # true-up release convert purchased topup credit into monthly base
    # credit that a renewal then wipes out.
    assert float(row["base_credit_remaining_usd"]) == pytest.approx(0.00)
    assert float(row["base_credit_allotment_usd"]) == pytest.approx(0.00)


@pytest.mark.asyncio
async def test_credit_topup_purchase_is_idempotent_on_replayed_transaction(pool):
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (404, 'acme', 'flash')"
    )

    first = await credit_topup_purchase(pool, 404, 8.00, "txn_topup_404")
    second = await credit_topup_purchase(pool, 404, 8.00, "txn_topup_404")

    assert first is True
    assert second is False
    row = await pool.fetchrow(
        "SELECT topup_credit_balance_usd FROM installations WHERE installation_id = $1",
        404,
    )
    # Only credited once, not twice.
    assert float(row["topup_credit_balance_usd"]) == pytest.approx(8.00)


# --- Webhook wiring for the two mutations above ---


@pytest.mark.asyncio
async def test_subscription_updated_with_current_billing_period_resets_credit(pool):
    # A genuine renewal delivered as subscription.updated: current_billing_
    # period.starts_at is new for this installation (NULL -> a real
    # timestamp), so the base credit must reset to the plan's real included
    # credit even though the balance had been spent down to 2.00.
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan, base_credit_remaining_usd) "
        "VALUES (405, 'acme', 'air', 2.00)"
    )
    payload = {
        "event_id": "evt_renewal_405",
        "event_type": "subscription.updated",
        "data": {
            "id": "sub_test_405",
            "customer_id": "ctm_test_405",
            "status": "active",
            "custom_data": {"installation_token": _installation_token(405)},
            "items": [{"price": {"id": "pri_01kyhevc8bkcghfpwjymz16y2h"}, "quantity": 1}],
            "current_billing_period": {"starts_at": "2026-09-01T00:00:00Z"},
        },
    }

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    row = await pool.fetchrow(
        "SELECT base_credit_remaining_usd, balance_epoch, current_billing_period_start "
        "FROM installations WHERE installation_id = $1",
        405,
    )
    assert float(row["base_credit_remaining_usd"]) == pytest.approx(18.00)
    assert row["balance_epoch"] == 1
    assert row["current_billing_period_start"] is not None


@pytest.mark.asyncio
async def test_monthly_air_subscription_updated_does_not_arm_the_monthly_credit_clock(pool):
    # The monthly AIR price. Paddle already advances this subscription's
    # current_billing_period every month, so the synthetic clock must stay
    # NULL - a monthly subscriber picked up by the sweep would be credited
    # twice a month.
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (430, 'monthly-co', 'air')"
    )
    payload = {
        "event_id": "evt_renewal_430",
        "event_type": "subscription.updated",
        "data": {
            "id": "sub_test_430",
            "customer_id": "ctm_test_430",
            "status": "active",
            "custom_data": {"installation_token": _installation_token(430)},
            "items": [
                {"price": {"id": PLAN_INTERVAL_TO_PRICE_ID[("air", "month")]}, "quantity": 1}
            ],
            "current_billing_period": {"starts_at": "2026-09-01T00:00:00Z"},
        },
    }

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    row = await pool.fetchrow(
        "SELECT base_credit_remaining_usd, next_monthly_credit_reset_at "
        "FROM installations WHERE installation_id = $1",
        430,
    )
    assert float(row["base_credit_remaining_usd"]) == pytest.approx(18.00)
    assert row["next_monthly_credit_reset_at"] is None


@pytest.mark.asyncio
async def test_annual_air_subscriber_really_gets_re_credited_mid_year(pool):
    # End-to-end proof of the bug this whole mechanism exists to fix. An
    # annual AIR subscriber's current_billing_period.starts_at advances once
    # a YEAR, so before this the webhook reset below was the ONLY credit
    # they would see for 12 months: $18 for the year instead of $18 a month.
    #
    # Two halves, in order: the real annual renewal webhook arms the
    # synthetic monthly clock one month out, and then the sweep - the thing
    # that actually runs monthly in production - re-credits them off that
    # clock, with no Paddle event involved at all.
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (431, 'annual-co', 'air')"
    )
    payload = {
        "event_id": "evt_renewal_431",
        "event_type": "subscription.updated",
        "data": {
            "id": "sub_test_431",
            "customer_id": "ctm_test_431",
            "status": "active",
            "custom_data": {"installation_token": _installation_token(431)},
            "items": [
                {"price": {"id": PLAN_INTERVAL_TO_PRICE_ID[("air", "year")]}, "quantity": 1}
            ],
            "current_billing_period": {"starts_at": "2026-09-01T00:00:00Z"},
        },
    }

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    row = await pool.fetchrow(
        "SELECT base_credit_remaining_usd, balance_epoch, next_monthly_credit_reset_at "
        "FROM installations WHERE installation_id = $1",
        431,
    )
    assert float(row["base_credit_remaining_usd"]) == pytest.approx(18.00)
    assert row["balance_epoch"] == 1
    assert row["next_monthly_credit_reset_at"] == datetime(
        2026, 9, 1, tzinfo=timezone.utc
    ) + timedelta(days=30)

    # Now they spend the month's credit down and their synthetic due date
    # arrives (moved into the past here rather than waiting a month - the
    # sweep's own trigger is next_monthly_credit_reset_at <= now()).
    await pool.execute(
        "UPDATE installations SET base_credit_remaining_usd = 0.40, "
        "next_monthly_credit_reset_at = now() - interval '1 minute' "
        "WHERE installation_id = $1",
        431,
    )

    run_monthly_credit_reset_sweep_job()

    row = await pool.fetchrow(
        "SELECT base_credit_remaining_usd, base_credit_allotment_usd, balance_epoch, "
        "current_billing_period_start, next_monthly_credit_reset_at "
        "FROM installations WHERE installation_id = $1",
        431,
    )
    # The whole point: a full monthly allotment again, mid-year, with no
    # Paddle renewal anywhere near it.
    assert float(row["base_credit_remaining_usd"]) == pytest.approx(18.00)
    assert float(row["base_credit_allotment_usd"]) == pytest.approx(18.00)
    # Bumped so the low-balance/exhausted emails they may already have been
    # sent this year don't suppress next month's (the dedupe key is
    # f"credit_low_balance:{installation_id}:{balance_epoch}").
    assert row["balance_epoch"] == 2
    # Paddle still owns the real billing period - the sweep must not fake it
    # forward, or the next genuine annual renewal would look like a replay
    # and be skipped.
    assert row["current_billing_period_start"] == datetime(2026, 9, 1, tzinfo=timezone.utc)
    # And the clock is armed again for the month after.
    assert row["next_monthly_credit_reset_at"] > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_subscription_updated_replay_does_not_reset_spent_down_credit(pool):
    # The same current_billing_period.starts_at delivered twice (a Paddle
    # retry, or an unrelated subscription.updated within the same period)
    # must not reset the balance back up a second time.
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (406, 'acme', 'air')"
    )
    payload = {
        "event_id": "evt_renewal_406a",
        "event_type": "subscription.updated",
        "data": {
            "id": "sub_test_406",
            "customer_id": "ctm_test_406",
            "status": "active",
            "custom_data": {"installation_token": _installation_token(406)},
            "items": [{"price": {"id": "pri_01kyhevc8bkcghfpwjymz16y2h"}, "quantity": 1}],
            "current_billing_period": {"starts_at": "2026-09-01T00:00:00Z"},
        },
    }
    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)
    await pool.execute(
        "UPDATE installations SET base_credit_remaining_usd = 3.00 WHERE installation_id = $1", 406
    )

    payload["event_id"] = "evt_renewal_406b"
    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    row = await pool.fetchrow(
        "SELECT base_credit_remaining_usd FROM installations WHERE installation_id = $1", 406
    )
    assert float(row["base_credit_remaining_usd"]) == pytest.approx(3.00)


@pytest.mark.asyncio
async def test_mid_cycle_seat_purchase_credits_the_balance_immediately(pool):
    # I5 of the final-review fix wave: a seat purchase fires
    # subscription.updated with the SAME current_billing_period.starts_at, so
    # reset_billing_period_credit is a deliberate no-op - the per-seat bonus
    # baked into base_credit_for_plan never landed until the next real
    # renewal, and a customer paid $6.99/seat for $0 of extra credit for up
    # to a month. (The old flat cap recomputed itself live from
    # get_extra_seats at every enforcement call site, so it used to rise
    # immediately.)
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan, extra_seats, "
        "base_credit_remaining_usd, current_billing_period_start) "
        "VALUES (407, 'acme', 'air', 1, 12.00, '2026-09-01T00:00:00Z')"
    )
    payload = {
        "event_id": "evt_seat_407",
        "event_type": "subscription.updated",
        "data": {
            "id": "sub_test_407",
            "customer_id": "ctm_test_407",
            "status": "active",
            "custom_data": {"installation_token": _installation_token(407)},
            "items": [
                {"price": {"id": "pri_01kyhevc8bkcghfpwjymz16y2h"}, "quantity": 1},
                {"price": {"id": EXTRA_SEAT_PRICE_ID}, "quantity": 3},
            ],
            # Same period as what's already stored - so the renewal reset is
            # correctly a no-op and cannot be what applies the seat credit.
            "current_billing_period": {"starts_at": "2026-09-01T00:00:00Z"},
        },
    }

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    assert await get_extra_seats(pool, 407) == 3
    row = await pool.fetchrow(
        "SELECT base_credit_remaining_usd, base_credit_allotment_usd, balance_epoch "
        "FROM installations WHERE installation_id = $1",
        407,
    )
    # 2 seats added (1 -> 3) x EXTRA_SEAT_LLM_CAP_USD (3.00), on top of the
    # already-spent-down 12.00 - NOT a reset to base_credit_for_plan. Well
    # under the new ceiling, so the anti-farming clamp doesn't bite.
    assert float(row["base_credit_remaining_usd"]) == pytest.approx(12.00 + 2 * 3.00)
    # And the ceiling itself moves to what 3 seats now entitle this
    # installation to - base_credit_for_plan("air", 3) = 18.00 + 3 * 3.00.
    # This is also what proves the webhook passes the CURRENT plan and seat
    # count through to credit_extra_seat_purchase: with the pre-purchase
    # seat count it would land at 21.00 instead.
    assert float(row["base_credit_allotment_usd"]) == pytest.approx(27.00)
    # Balance went up, so the low-balance email dedupe epoch advances too.
    assert row["balance_epoch"] == 1


@pytest.mark.asyncio
async def test_seat_removal_does_not_change_the_credit_balance(pool):
    # Only an INCREASE credits. Removing a seat must not claw credit back
    # mid-cycle (the customer already paid for the period it was bought in);
    # the smaller allotment simply applies at the next renewal reset.
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan, extra_seats, "
        "base_credit_remaining_usd, current_billing_period_start) "
        "VALUES (408, 'acme', 'air', 3, 12.00, '2026-09-01T00:00:00Z')"
    )
    payload = {
        "event_id": "evt_seat_408",
        "event_type": "subscription.updated",
        "data": {
            "id": "sub_test_408",
            "customer_id": "ctm_test_408",
            "status": "active",
            "custom_data": {"installation_token": _installation_token(408)},
            "items": [
                {"price": {"id": "pri_01kyhevc8bkcghfpwjymz16y2h"}, "quantity": 1},
                {"price": {"id": EXTRA_SEAT_PRICE_ID}, "quantity": 1},
            ],
            "current_billing_period": {"starts_at": "2026-09-01T00:00:00Z"},
        },
    }

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    assert await get_extra_seats(pool, 408) == 1
    row = await pool.fetchrow(
        "SELECT base_credit_remaining_usd FROM installations WHERE installation_id = $1", 408
    )
    assert float(row["base_credit_remaining_usd"]) == pytest.approx(12.00)


@pytest.mark.asyncio
async def test_renewal_with_more_seats_resets_without_double_counting_the_seat_bonus(pool):
    # A genuine renewal that ALSO carries a higher seat count: the reset
    # already sets the balance to base_credit_for_plan(plan, extra_seats),
    # which includes the new seats - crediting the mid-cycle bonus on top of
    # that would double-count it.
    fake_queue = MagicMock()
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan, extra_seats, "
        "base_credit_remaining_usd, current_billing_period_start) "
        "VALUES (409, 'acme', 'air', 1, 2.00, '2026-08-01T00:00:00Z')"
    )
    payload = {
        "event_id": "evt_renewal_seats_409",
        "event_type": "subscription.updated",
        "data": {
            "id": "sub_test_409",
            "customer_id": "ctm_test_409",
            "status": "active",
            "custom_data": {"installation_token": _installation_token(409)},
            "items": [
                {"price": {"id": "pri_01kyhevc8bkcghfpwjymz16y2h"}, "quantity": 1},
                {"price": {"id": EXTRA_SEAT_PRICE_ID}, "quantity": 3},
            ],
            "current_billing_period": {"starts_at": "2026-09-01T00:00:00Z"},
        },
    }

    await handle_paddle_webhook_event(payload, pool, "redis://unused", queue=fake_queue)

    row = await pool.fetchrow(
        "SELECT base_credit_remaining_usd FROM installations WHERE installation_id = $1", 409
    )
    # base_credit_for_plan("air", 3) = 18.00 + 3 * 3.00 = 27.00, and nothing
    # more on top of it.
    assert float(row["base_credit_remaining_usd"]) == pytest.approx(27.00)


@pytest.mark.asyncio
async def test_transaction_completed_credits_topup_purchase(pool):
    await upsert_installation(pool, 910 + 1000, "acme")
    installation_id = 1910
    payload = {
        "event_id": "evt_topup_1910",
        "event_type": "transaction.completed",
        "data": {
            "id": "txn_topup_wire_1910",
            "customer_id": "ctm_test_1910",
            "custom_data": {"installation_token": _installation_token(installation_id)},
            "items": [{"price": {"id": CREDIT_TOPUP_PRICE_ID}, "quantity": 10}],
            # details.totals.total (Paddle's actual collected amount, in
            # cents) is what determines the credited amount, not quantity -
            # this happens to be a round, undiscounted $10.00 (quantity * 100
            # cents) so this test alone doesn't prove the fix; see
            # test_transaction_completed_credits_topup_purchase_at_discounted_total
            # below for the case where they deliberately diverge.
            "details": {"totals": {"total": "1000"}},
            "billed_at": "2026-09-01T12:00:00Z",
        },
    }

    await handle_paddle_webhook_event(payload, pool, "redis://unused")

    row = await pool.fetchrow(
        "SELECT topup_credit_balance_usd, balance_epoch FROM installations WHERE installation_id = $1",
        installation_id,
    )
    assert float(row["topup_credit_balance_usd"]) == pytest.approx(10.00)
    assert row["balance_epoch"] == 1


@pytest.mark.asyncio
async def test_transaction_completed_credits_topup_purchase_at_discounted_total(pool):
    # Real production billing bug: crediting used to trust the line item's
    # raw quantity (assuming exactly $1.00/unit was collected), ignoring
    # any discount actually applied by Paddle. Here quantity is 10 (which
    # would wrongly credit $10.00) but Paddle only collected $8.00 net of a
    # discount - proving the fix credits details.totals.total, not
    # quantity.
    await upsert_installation(pool, 1914, "acme")
    installation_id = 1914
    payload = {
        "event_id": "evt_topup_1914",
        "event_type": "transaction.completed",
        "data": {
            "id": "txn_topup_wire_1914",
            "customer_id": "ctm_test_1914",
            "custom_data": {"installation_token": _installation_token(installation_id)},
            "items": [{"price": {"id": CREDIT_TOPUP_PRICE_ID}, "quantity": 10}],
            "details": {"totals": {"total": "800"}},
            "billed_at": "2026-09-01T12:00:00Z",
        },
    }

    await handle_paddle_webhook_event(payload, pool, "redis://unused")

    row = await pool.fetchrow(
        "SELECT topup_credit_balance_usd, balance_epoch FROM installations WHERE installation_id = $1",
        installation_id,
    )
    assert float(row["topup_credit_balance_usd"]) == pytest.approx(8.00)
    assert row["balance_epoch"] == 1


@pytest.mark.asyncio
async def test_transaction_completed_skips_topup_credit_when_bundled_with_another_item(pool, caplog):
    # This codebase has never parsed Paddle's per-line-item totals, only the
    # transaction-level total - crediting details.totals.total when the
    # top-up isn't the only item would credit the OTHER item's cost too.
    # buyCredit() itself never sends more than one item, but nothing
    # server-side enforced that before this guard: a devtools-crafted
    # Paddle.Checkout.open() call could otherwise bundle a top-up with
    # another price (e.g. an extra seat) and get topped up for the combined
    # amount. Proves the transaction is skipped, not partially credited.
    installation_id = 1915
    await upsert_installation(pool, installation_id, "acme")
    payload = {
        "event_id": "evt_topup_1915",
        "event_type": "transaction.completed",
        "data": {
            "id": "txn_topup_wire_1915",
            "customer_id": "ctm_test_1915",
            "custom_data": {"installation_token": _installation_token(installation_id)},
            "items": [
                {"price": {"id": CREDIT_TOPUP_PRICE_ID}, "quantity": 10},
                {"price": {"id": EXTRA_SEAT_PRICE_ID}, "quantity": 1},
            ],
            "details": {"totals": {"total": "1699"}},
            "billed_at": "2026-09-01T12:00:00Z",
        },
    }

    with caplog.at_level(logging.WARNING):
        await handle_paddle_webhook_event(payload, pool, "redis://unused")

    row = await pool.fetchrow(
        "SELECT topup_credit_balance_usd, balance_epoch FROM installations WHERE installation_id = $1",
        installation_id,
    )
    assert float(row["topup_credit_balance_usd"]) == pytest.approx(0.00)
    assert row["balance_epoch"] == 0
    assert "bundled with other line items" in caplog.text


@pytest.mark.asyncio
async def test_transaction_completed_topup_is_independent_of_referral_commission(pool):
    # Proves the topup branch isn't skipped by the referral early-return
    # for unreferred transactions (the common case), since this
    # installation has no referral on file at all. (For a *referred*
    # installation, a topup transaction is deliberately excluded from
    # commission entirely - see
    # test_transaction_completed_topup_for_referred_installation_is_excluded_from_commission
    # below.)
    installation_id = 1911
    await upsert_installation(pool, installation_id, "acme")
    payload = {
        "event_id": "evt_topup_1911",
        "event_type": "transaction.completed",
        "data": {
            "id": "txn_topup_wire_1911",
            "customer_id": "ctm_test_1911",
            "custom_data": {"installation_token": _installation_token(installation_id)},
            "items": [{"price": {"id": CREDIT_TOPUP_PRICE_ID}, "quantity": 5}],
            "details": {"totals": {"total": "500"}},
            "billed_at": "2026-09-01T12:00:00Z",
        },
    }

    await handle_paddle_webhook_event(payload, pool, "redis://unused")

    row = await pool.fetchrow(
        "SELECT topup_credit_balance_usd FROM installations WHERE installation_id = $1",
        installation_id,
    )
    assert float(row["topup_credit_balance_usd"]) == pytest.approx(5.00)
    # No affiliate/referral was ever recorded for this installation - the
    # referral early-return in _handle_transaction_completed doesn't
    # prevent the (independent) topup branch from running.
    assert await get_referral(pool, installation_id) is None


@pytest.mark.asyncio
async def test_transaction_completed_topup_for_referred_installation_is_excluded_from_commission(pool):
    # Real production billing bug: credit top-ups are pass-through LLM
    # spend with near-zero margin. Before this fix, a referred
    # installation's top-up purchase still fell through into the
    # unconditional commission block below and paid its referrer 15% of
    # the top-up amount - a real, recurring loss with no offsetting
    # revenue. The top-up must still be credited (that part is real
    # revenue-neutral top-up crediting, unrelated to the affiliate
    # program), but this transaction must NOT generate a commission.
    affiliate = await create_affiliate(pool, "TOPUPEXCL10", "dsc_topupexcl_wh", "Topupexcl")
    installation_id = 1912
    await upsert_installation(pool, installation_id, "acme")
    await record_referral(pool, installation_id, affiliate["id"])

    payload = {
        "event_id": "evt_topup_1912",
        "event_type": "transaction.completed",
        "data": {
            "id": "txn_topup_wire_1912",
            "customer_id": "ctm_test_1912",
            "custom_data": {"installation_token": _installation_token(installation_id)},
            "items": [{"price": {"id": CREDIT_TOPUP_PRICE_ID}, "quantity": 20}],
            # A large total (20 * $1 topup unit price scope aside) - if the
            # commission bug were still present this would pay a very
            # visible $3.00 (15% of $20.00) commission.
            "details": {"totals": {"total": "2000"}},
            "billed_at": "2026-09-01T12:00:00Z",
        },
    }

    await handle_paddle_webhook_event(payload, pool, "redis://unused")

    # The top-up itself was still credited.
    row = await pool.fetchrow(
        "SELECT topup_credit_balance_usd FROM installations WHERE installation_id = $1",
        installation_id,
    )
    assert float(row["topup_credit_balance_usd"]) == pytest.approx(20.00)

    # But no commission was recorded for the referring affiliate.
    totals = {row["id"]: row for row in await list_affiliates_with_totals(pool)}
    assert totals[affiliate["id"]]["total_owed_usd"] == Decimal("0")


@pytest.mark.asyncio
async def test_transaction_completed_regular_purchase_for_referred_installation_still_records_commission(pool):
    # Companion to the exclusion test above: a referred installation's
    # ordinary (non-topup) subscription/seat transaction must still earn
    # its referrer commission exactly as before - the topup exclusion
    # must not have broken the existing, correct commission path.
    affiliate = await create_affiliate(pool, "REGULAR10", "dsc_regular_wh", "Regular")
    installation_id = 1913
    await upsert_installation(pool, installation_id, "acme")
    await record_referral(pool, installation_id, affiliate["id"])

    # $26.99 (2699 cents), no CREDIT_TOPUP_PRICE_ID item - same shape as
    # test_transaction_completed_for_referred_installation_records_commission.
    payload = _transaction_completed_payload(installation_id, "2699", transaction_id="txn_regular_1913")
    await handle_paddle_webhook_event(payload, pool, "redis://unused")

    totals = {row["id"]: row for row in await list_affiliates_with_totals(pool)}
    assert totals[affiliate["id"]]["total_owed_usd"] == Decimal("4.05")
