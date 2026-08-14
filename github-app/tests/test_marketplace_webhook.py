from unittest.mock import MagicMock

import pytest

from app_server.db import (
    get_installation,
    get_extra_seats,
    is_installation_member,
    set_extra_seats,
    set_installation_plan,
    upsert_installation,
)
from app_server.webhooks.marketplace import _normalize_marketplace_plan_name, handle_marketplace_event

# account_id is deliberately never the same number as installation_id in
# these fixtures - marketplace_purchase.account.id is a GitHub user/org
# account ID, a different ID space from the installation ID the handler
# actually looks up by account_login. A test that happened to use the
# same number for both would hide a regression back to the old
# account["id"]-as-installation_id bug.


def _payload(
    action: str,
    account_id: int,
    login: str,
    plan_name: str = "Aletheore AIR",
    sender_login: str = "octocat",
):
    return {
        "action": action,
        "sender": {"login": sender_login},
        "marketplace_purchase": {
            "account": {"id": account_id, "login": login},
            "plan": {"name": plan_name},
        },
    }


def test_normalize_marketplace_plan_name_recognizes_air_case_insensitively():
    assert _normalize_marketplace_plan_name("Aletheore AIR") == "air"
    assert _normalize_marketplace_plan_name("air") == "air"
    assert _normalize_marketplace_plan_name("  AIR  ") == "air"


def test_normalize_marketplace_plan_name_defaults_unrecognized_names_to_free():
    # Fail-closed: an unrecognized name must not grant paid access, unlike
    # the original bug where anything other than the exact lowercase
    # string "free" did.
    assert _normalize_marketplace_plan_name("Free") == "free"
    assert _normalize_marketplace_plan_name("Community") == "free"
    assert _normalize_marketplace_plan_name("") == "free"
    assert _normalize_marketplace_plan_name("Enterprise Plus") == "free"


@pytest.mark.asyncio
async def test_purchased_sets_plan_on_the_real_installation(pool):
    fake_queue = MagicMock()
    await upsert_installation(pool, 777, "octocat")
    await handle_marketplace_event(
        _payload("purchased", 999001, "octocat"), pool, "redis://unused", queue=fake_queue
    )
    row = await get_installation(pool, 777)
    assert row["plan"] == "air"


@pytest.mark.asyncio
async def test_purchased_without_a_matching_installation_is_a_noop(pool):
    # Previously this silently created a bogus installations row keyed by
    # the Marketplace account ID (wrong ID space) instead of doing
    # nothing - now there's no row to correlate the purchase to, so it
    # must not fabricate one.
    fake_queue = MagicMock()
    await handle_marketplace_event(
        _payload("purchased", 999002, "no-such-installation"), pool, "redis://unused", queue=fake_queue
    )
    assert await get_installation(pool, 999002) is None
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_changed_updates_plan(pool):
    fake_queue = MagicMock()
    await upsert_installation(pool, 777, "octocat")
    await handle_marketplace_event(
        _payload("purchased", 999003, "octocat"), pool, "redis://unused", queue=fake_queue
    )
    await handle_marketplace_event(
        _payload("changed", 999003, "octocat", plan_name="Aletheore AIR (annual)"), pool, "redis://unused", queue=fake_queue
    )
    row = await get_installation(pool, 777)
    assert row["plan"] == "air"


@pytest.mark.asyncio
async def test_cancelled_resets_to_free(pool):
    fake_queue = MagicMock()
    await upsert_installation(pool, 777, "octocat")
    await set_extra_seats(pool, 777, 3)
    await handle_marketplace_event(
        _payload("purchased", 999004, "octocat"), pool, "redis://unused", queue=fake_queue
    )
    await handle_marketplace_event(
        _payload("cancelled", 999004, "octocat"), pool, "redis://unused", queue=fake_queue
    )
    row = await get_installation(pool, 777)
    assert row["plan"] == "free"
    assert await get_extra_seats(pool, 777) == 0


@pytest.mark.asyncio
async def test_replaying_same_event_is_idempotent(pool):
    fake_queue = MagicMock()
    await upsert_installation(pool, 777, "octocat")
    payload = _payload("purchased", 999005, "octocat")
    await handle_marketplace_event(payload, pool, "redis://unused", queue=fake_queue)
    await handle_marketplace_event(payload, pool, "redis://unused", queue=fake_queue)
    row = await get_installation(pool, 777)
    assert row["plan"] == "air"


@pytest.mark.asyncio
async def test_free_to_paid_transition_triggers_live_wiki_full_build(pool):
    fake_queue = MagicMock()
    await upsert_installation(pool, 777, "octocat")  # defaults to plan='free'

    await handle_marketplace_event(
        _payload("purchased", 999006, "octocat"), pool, "redis://unused", queue=fake_queue
    )

    assert fake_queue.enqueue.call_count == 2
    job_names = {call.args[0] for call in fake_queue.enqueue.call_args_list}
    assert job_names == {
        "scan_worker.jobs.run_live_wiki_full_build_for_installation_job",
        "scan_worker.jobs.run_live_docs_full_build_for_installation_job",
    }
    for call in fake_queue.enqueue.call_args_list:
        assert call.kwargs["installation_id"] == 777


@pytest.mark.asyncio
async def test_paid_to_paid_change_does_not_retrigger_live_wiki_build(pool):
    fake_queue = MagicMock()
    await upsert_installation(pool, 777, "octocat")
    await set_installation_plan(pool, 777, "air")

    await handle_marketplace_event(
        _payload("changed", 999007, "octocat", plan_name="Aletheore AIR (annual)"),
        pool,
        "redis://unused",
        queue=fake_queue,
    )

    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_cancellation_does_not_trigger_live_wiki_build(pool):
    fake_queue = MagicMock()
    await upsert_installation(pool, 777, "octocat")
    await set_installation_plan(pool, 777, "air")

    await handle_marketplace_event(
        _payload("cancelled", 999008, "octocat"), pool, "redis://unused", queue=fake_queue
    )

    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_purchase_seats_the_sender_as_first_member(pool):
    fake_queue = MagicMock()
    await upsert_installation(pool, 777, "octocat")
    await handle_marketplace_event(
        _payload("purchased", 999009, "octocat", sender_login="alice"),
        pool,
        "redis://unused",
        queue=fake_queue,
    )
    assert await is_installation_member(pool, 777, "alice") is True


@pytest.mark.asyncio
async def test_cancellation_does_not_remove_existing_members(pool):
    fake_queue = MagicMock()
    await upsert_installation(pool, 777, "octocat")
    await set_installation_plan(pool, 777, "air")
    await handle_marketplace_event(
        _payload("purchased", 999010, "octocat", sender_login="alice"),
        pool,
        "redis://unused",
        queue=fake_queue,
    )
    await handle_marketplace_event(
        _payload("cancelled", 999010, "octocat"), pool, "redis://unused", queue=fake_queue
    )
    assert await is_installation_member(pool, 777, "alice") is True
