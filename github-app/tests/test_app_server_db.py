import pytest

from app_server.db import (
    add_paddle_ids_to_installation,
    check_and_reserve_demo_scan,
    count_telemetry_events,
    get_installation,
    record_telemetry_event,
)


@pytest.mark.asyncio
async def test_add_paddle_ids_to_installation(pool):
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (2, 'acme', 'free')"
    )
    await add_paddle_ids_to_installation(pool, 2, "sub_789", "ctm_3")
    row = await get_installation(pool, 2)
    assert row["paddle_subscription_id"] == "sub_789"
    assert row["paddle_customer_id"] == "ctm_3"


@pytest.mark.asyncio
async def test_check_and_reserve_demo_scan_allows_first_then_blocks_within_cooldown(pool):
    assert await check_and_reserve_demo_scan(pool, "203.0.113.5", cooldown_seconds=1200) is True
    assert await check_and_reserve_demo_scan(pool, "203.0.113.5", cooldown_seconds=1200) is False


@pytest.mark.asyncio
async def test_check_and_reserve_demo_scan_different_ips_are_independent(pool):
    assert await check_and_reserve_demo_scan(pool, "203.0.113.10", cooldown_seconds=1200) is True
    assert await check_and_reserve_demo_scan(pool, "203.0.113.11", cooldown_seconds=1200) is True


@pytest.mark.asyncio
async def test_check_and_reserve_demo_scan_allows_again_after_cooldown_elapses(pool):
    assert await check_and_reserve_demo_scan(pool, "203.0.113.20", cooldown_seconds=0) is True
    assert await check_and_reserve_demo_scan(pool, "203.0.113.20", cooldown_seconds=0) is True


@pytest.mark.asyncio
async def test_record_telemetry_event_then_count(pool):
    await record_telemetry_event(pool, "scan", "machine-a")
    await record_telemetry_event(pool, "scan", "machine-a")
    await record_telemetry_event(pool, "scan", "machine-b")

    counts = await count_telemetry_events(pool, "scan")

    assert counts == {"total": 3, "unique_machines": 2}


@pytest.mark.asyncio
async def test_count_telemetry_events_only_counts_the_given_event_type(pool):
    await record_telemetry_event(pool, "scan", "machine-a")
    await record_telemetry_event(pool, "other-event", "machine-a")

    counts = await count_telemetry_events(pool, "scan")

    assert counts["total"] == 1
