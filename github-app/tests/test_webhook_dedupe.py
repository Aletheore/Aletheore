import hashlib
import hmac
import json

import pytest
from httpx import ASGITransport, AsyncClient

from app_server.db import claim_webhook_delivery, release_webhook_delivery
from app_server.main import app, settings


def _signature(payload: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _push_payload() -> bytes:
    return json.dumps(
        {
            "ref": "refs/heads/main",
            "after": "abc123",
            "installation": {"id": 123},
            "repository": {"full_name": "octocat/hello-world"},
        }
    ).encode()


async def _post(client, body, delivery_id, event="push"):
    headers = {
        "X-Hub-Signature-256": _signature(body, settings.github_webhook_secret),
        "X-GitHub-Event": event,
    }
    if delivery_id is not None:
        headers["X-GitHub-Delivery"] = delivery_id
    return await client.post("/webhook", content=body, headers=headers)


@pytest.mark.asyncio
async def test_claim_is_granted_once_and_refused_after(pool):
    assert await claim_webhook_delivery(pool, "github", "d-1", "push") is True
    assert await claim_webhook_delivery(pool, "github", "d-1", "push") is False


@pytest.mark.asyncio
async def test_release_lets_a_delivery_be_claimed_again(pool):
    await claim_webhook_delivery(pool, "github", "d-2", "push")
    await release_webhook_delivery(pool, "github", "d-2")

    assert await claim_webhook_delivery(pool, "github", "d-2", "push") is True


@pytest.mark.asyncio
async def test_distinct_delivery_ids_are_independent(pool):
    assert await claim_webhook_delivery(pool, "github", "d-3", "push") is True
    assert await claim_webhook_delivery(pool, "github", "d-4", "push") is True


@pytest.mark.asyncio
async def test_replayed_delivery_is_not_handled_twice(pool, monkeypatch):
    app.state.db_pool = pool
    handled = []

    async def fake_handle(payload_arg, redis_url):
        handled.append(payload_arg)

    monkeypatch.setattr("app_server.webhooks.push.handle_push_event", fake_handle)
    body = _push_payload()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await _post(client, body, "delivery-replay-1")
        second = await _post(client, body, "delivery-replay-1")

    assert first.status_code == 200
    assert first.json() == {"ok": True}
    assert second.status_code == 200
    assert second.json() == {"ok": True, "duplicate": True}
    assert len(handled) == 1, "a replayed delivery enqueued a second scan"


@pytest.mark.asyncio
async def test_a_genuinely_new_delivery_still_runs(pool, monkeypatch):
    # Redelivering from the GitHub UI mints a fresh GUID, and that is an
    # operator deliberately asking for another run - it must not be
    # swallowed as a duplicate.
    app.state.db_pool = pool
    handled = []

    async def fake_handle(payload_arg, redis_url):
        handled.append(payload_arg)

    monkeypatch.setattr("app_server.webhooks.push.handle_push_event", fake_handle)
    body = _push_payload()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await _post(client, body, "delivery-new-1")
        await _post(client, body, "delivery-new-2")

    assert len(handled) == 2


@pytest.mark.asyncio
async def test_missing_delivery_header_is_rejected(pool):
    # Otherwise stripping one header would bypass replay protection wholesale.
    app.state.db_pool = pool
    body = _push_payload()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await _post(client, body, None)

    assert response.status_code == 400
    assert "X-GitHub-Delivery" in response.json()["detail"]


@pytest.mark.asyncio
async def test_unsigned_request_never_reaches_the_ledger(pool):
    # A forged request must not be able to burn a GUID and thereby suppress
    # the real delivery that follows.
    app.state.db_pool = pool
    body = _push_payload()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhook",
            content=body,
            headers={
                "X-Hub-Signature-256": "sha256=forged",
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": "delivery-forged-1",
            },
        )

    assert response.status_code == 401
    assert await claim_webhook_delivery(pool, "github", "delivery-forged-1", "push") is True


@pytest.mark.asyncio
async def test_failed_handler_releases_the_claim_so_github_can_retry(pool, monkeypatch):
    app.state.db_pool = pool
    attempts = []

    async def failing_handle(payload_arg, redis_url):
        attempts.append(payload_arg)
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr("app_server.webhooks.push.handle_push_event", failing_handle)
    body = _push_payload()

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await _post(client, body, "delivery-retry-1")
        assert first.status_code == 500

        # GitHub's automatic retry reuses the same GUID. If the claim had
        # stuck, this would be discarded as a duplicate and the event lost.
        async def working_handle(payload_arg, redis_url):
            attempts.append(payload_arg)

        monkeypatch.setattr("app_server.webhooks.push.handle_push_event", working_handle)
        second = await _post(client, body, "delivery-retry-1")

    assert second.status_code == 200
    assert second.json() == {"ok": True}
    assert len(attempts) == 2, "retry of a failed delivery was wrongly suppressed"


@pytest.mark.asyncio
async def test_concurrent_claims_of_one_id_produce_exactly_one_winner(pool):
    """The property the whole design rests on.

    Sequential dedupe is easy; the case that actually motivated an atomic
    INSERT is two deliveries of one event landing at once. A read-then-write
    implementation passes every other test in this file and fails here.
    """
    import asyncio

    results = await asyncio.gather(
        *(claim_webhook_delivery(pool, "github", "d-concurrent", "push") for _ in range(8))
    )

    assert sum(results) == 1, f"expected exactly one winner, got {sum(results)}"


@pytest.mark.asyncio
async def test_the_same_id_from_two_sources_does_not_collide(pool):
    # GitHub GUIDs and Paddle event ids share one table; the source column is
    # what keeps them from suppressing each other.
    assert await claim_webhook_delivery(pool, "github", "shared-id", "push") is True
    assert await claim_webhook_delivery(pool, "paddle", "shared-id", "subscription.created") is True
