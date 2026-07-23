from unittest.mock import MagicMock

import pytest

from app_server.db import get_installation, is_installation_member, set_installation_plan, upsert_installation
from app_server.webhooks.marketplace import handle_marketplace_event


def _payload(action: str, installation_id: int, login: str, plan_name: str = "indie", sender_login: str = "octocat"):
    return {
        "action": action,
        "sender": {"login": sender_login},
        "marketplace_purchase": {
            "account": {"id": installation_id, "login": login},
            "plan": {"name": plan_name},
        },
    }


@pytest.mark.asyncio
async def test_purchased_sets_plan(pool):
    fake_queue = MagicMock()
    await upsert_installation(pool, 777, "octocat")
    await handle_marketplace_event(_payload("purchased", 777, "octocat", "indie"), pool, "redis://unused", queue=fake_queue)
    row = await get_installation(pool, 777)
    assert row["plan"] == "indie"


@pytest.mark.asyncio
async def test_changed_updates_plan(pool):
    fake_queue = MagicMock()
    await upsert_installation(pool, 777, "octocat")
    await handle_marketplace_event(_payload("purchased", 777, "octocat", "indie"), pool, "redis://unused", queue=fake_queue)
    await handle_marketplace_event(_payload("changed", 777, "octocat", "team"), pool, "redis://unused", queue=fake_queue)
    row = await get_installation(pool, 777)
    assert row["plan"] == "team"


@pytest.mark.asyncio
async def test_cancelled_resets_to_free(pool):
    fake_queue = MagicMock()
    await upsert_installation(pool, 777, "octocat")
    await handle_marketplace_event(_payload("purchased", 777, "octocat", "indie"), pool, "redis://unused", queue=fake_queue)
    await handle_marketplace_event(_payload("cancelled", 777, "octocat"), pool, "redis://unused", queue=fake_queue)
    row = await get_installation(pool, 777)
    assert row["plan"] == "free"


@pytest.mark.asyncio
async def test_purchased_creates_installation_if_missing(pool):
    fake_queue = MagicMock()
    await handle_marketplace_event(_payload("purchased", 888, "neworg", "indie"), pool, "redis://unused", queue=fake_queue)
    row = await get_installation(pool, 888)
    assert row is not None
    assert row["plan"] == "indie"


@pytest.mark.asyncio
async def test_replaying_same_event_is_idempotent(pool):
    fake_queue = MagicMock()
    payload = _payload("purchased", 777, "octocat", "indie")
    await handle_marketplace_event(payload, pool, "redis://unused", queue=fake_queue)
    await handle_marketplace_event(payload, pool, "redis://unused", queue=fake_queue)
    row = await get_installation(pool, 777)
    assert row["plan"] == "indie"


@pytest.mark.asyncio
async def test_free_to_paid_transition_triggers_live_wiki_full_build(pool):
    fake_queue = MagicMock()
    await upsert_installation(pool, 777, "octocat")  # defaults to plan='free'

    await handle_marketplace_event(_payload("purchased", 777, "octocat", "indie"), pool, "redis://unused", queue=fake_queue)

    fake_queue.enqueue.assert_called_once()
    args, kwargs = fake_queue.enqueue.call_args
    assert args[0] == "scan_worker.jobs.run_live_wiki_full_build_for_installation_job"
    assert kwargs["installation_id"] == 777


@pytest.mark.asyncio
async def test_paid_to_paid_change_does_not_retrigger_live_wiki_build(pool):
    fake_queue = MagicMock()
    await upsert_installation(pool, 777, "octocat")
    await set_installation_plan(pool, 777, "indie")

    await handle_marketplace_event(_payload("changed", 777, "octocat", "team"), pool, "redis://unused", queue=fake_queue)

    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_new_installation_purchasing_paid_plan_triggers_live_wiki_build(pool):
    fake_queue = MagicMock()
    await handle_marketplace_event(_payload("purchased", 999, "neworg", "indie"), pool, "redis://unused", queue=fake_queue)

    fake_queue.enqueue.assert_called_once()


@pytest.mark.asyncio
async def test_cancellation_does_not_trigger_live_wiki_build(pool):
    fake_queue = MagicMock()
    await upsert_installation(pool, 777, "octocat")
    await set_installation_plan(pool, 777, "indie")

    await handle_marketplace_event(_payload("cancelled", 777, "octocat"), pool, "redis://unused", queue=fake_queue)

    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_purchase_seats_the_sender_as_first_member(pool):
    fake_queue = MagicMock()
    await handle_marketplace_event(
        _payload("purchased", 777, "octocat", "indie", sender_login="alice"), pool, "redis://unused", queue=fake_queue
    )
    assert await is_installation_member(pool, 777, "alice") is True


@pytest.mark.asyncio
async def test_cancellation_does_not_remove_existing_members(pool):
    fake_queue = MagicMock()
    await upsert_installation(pool, 777, "octocat")
    await set_installation_plan(pool, 777, "indie")
    await handle_marketplace_event(
        _payload("purchased", 777, "octocat", "indie", sender_login="alice"), pool, "redis://unused", queue=fake_queue
    )
    await handle_marketplace_event(_payload("cancelled", 777, "octocat"), pool, "redis://unused", queue=fake_queue)
    assert await is_installation_member(pool, 777, "alice") is True
