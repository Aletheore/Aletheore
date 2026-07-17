import pytest

from app_server.db import get_installation, upsert_installation
from app_server.webhooks.marketplace import handle_marketplace_event


def _payload(action: str, installation_id: int, login: str, plan_name: str = "pro"):
    return {
        "action": action,
        "marketplace_purchase": {
            "account": {"id": installation_id, "login": login},
            "plan": {"name": plan_name},
        },
    }


@pytest.mark.asyncio
async def test_purchased_sets_plan(pool):
    await upsert_installation(pool, 777, "octocat")
    await handle_marketplace_event(_payload("purchased", 777, "octocat", "pro"), pool)
    row = await get_installation(pool, 777)
    assert row["plan"] == "pro"


@pytest.mark.asyncio
async def test_changed_updates_plan(pool):
    await upsert_installation(pool, 777, "octocat")
    await handle_marketplace_event(_payload("purchased", 777, "octocat", "pro"), pool)
    await handle_marketplace_event(_payload("changed", 777, "octocat", "team"), pool)
    row = await get_installation(pool, 777)
    assert row["plan"] == "team"


@pytest.mark.asyncio
async def test_cancelled_resets_to_free(pool):
    await upsert_installation(pool, 777, "octocat")
    await handle_marketplace_event(_payload("purchased", 777, "octocat", "pro"), pool)
    await handle_marketplace_event(_payload("cancelled", 777, "octocat"), pool)
    row = await get_installation(pool, 777)
    assert row["plan"] == "free"


@pytest.mark.asyncio
async def test_purchased_creates_installation_if_missing(pool):
    await handle_marketplace_event(_payload("purchased", 888, "neworg", "pro"), pool)
    row = await get_installation(pool, 888)
    assert row is not None
    assert row["plan"] == "pro"


@pytest.mark.asyncio
async def test_replaying_same_event_is_idempotent(pool):
    payload = _payload("purchased", 777, "octocat", "pro")
    await handle_marketplace_event(payload, pool)
    await handle_marketplace_event(payload, pool)
    row = await get_installation(pool, 777)
    assert row["plan"] == "pro"
