from datetime import datetime, timezone
import os

import pytest

from scan_worker.db import (
    check_and_reserve_flash_review_attempt,
    check_and_reserve_managed_audit,
    get_extra_seats,
    get_last_endpoint_health,
    get_last_reviewed_sha,
    get_latest_evidence,
    get_llm_spend_this_month,
    insert_endpoint_health,
    insert_repo_history,
    list_monitored_installations,
    list_repos_for_installation,
    record_llm_spend,
    set_last_reviewed_sha,
)

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:test@localhost:55433/aletheore_test",
)


async def _insert_installation(pool, installation_id: int, account_login: str, **values) -> None:
    columns = ["installation_id", "account_login", *values.keys()]
    params = [installation_id, account_login, *values.values()]
    placeholders = ", ".join(f"${i}" for i in range(1, len(params) + 1))
    await pool.execute(
        f"INSERT INTO installations ({', '.join(columns)}) VALUES ({placeholders})",
        *params,
    )


@pytest.mark.asyncio
async def test_list_monitored_installations_filters_plan_and_url(pool):
    await _insert_installation(
        pool,
        301,
        "a",
        plan="pro",
        health_check_base_url="https://a.example.com",
    )
    await _insert_installation(
        pool,
        302,
        "b",
        plan="free",
        health_check_base_url="https://b.example.com",
    )
    await _insert_installation(pool, 303, "c", plan="pro")

    ids = {row["installation_id"] for row in list_monitored_installations(TEST_DATABASE_URL)}
    assert ids == {301}


@pytest.mark.asyncio
async def test_list_monitored_installations_includes_webhook_url(pool):
    await _insert_installation(
        pool,
        304,
        "d",
        plan="pro",
        health_check_base_url="https://d.example.com",
        webhook_url="https://hooks.slack.com/d",
    )

    result = list_monitored_installations(TEST_DATABASE_URL)
    row = next(r for r in result if r["installation_id"] == 304)
    assert row["webhook_url"] == "https://hooks.slack.com/d"


@pytest.mark.asyncio
async def test_list_repos_for_installation(pool):
    await _insert_installation(pool, 301, "a")
    insert_repo_history(TEST_DATABASE_URL, 301, "a/repo1", datetime.now(timezone.utc), {"x": 1})
    insert_repo_history(TEST_DATABASE_URL, 301, "a/repo2", datetime.now(timezone.utc), {"x": 1})

    repos = list_repos_for_installation(TEST_DATABASE_URL, 301)
    assert set(repos) == {"a/repo1", "a/repo2"}


@pytest.mark.asyncio
async def test_get_latest_evidence_returns_most_recent(pool):
    await _insert_installation(pool, 301, "a")
    insert_repo_history(TEST_DATABASE_URL, 301, "a/repo1", datetime(2026, 1, 1, tzinfo=timezone.utc), {"v": 1})
    insert_repo_history(TEST_DATABASE_URL, 301, "a/repo1", datetime(2026, 1, 2, tzinfo=timezone.utc), {"v": 2})

    evidence = get_latest_evidence(TEST_DATABASE_URL, 301, "a/repo1")
    assert evidence["v"] == 2


@pytest.mark.asyncio
async def test_insert_and_get_last_endpoint_health(pool):
    await _insert_installation(pool, 301, "a")

    assert get_last_endpoint_health(TEST_DATABASE_URL, 301, "a/repo1", "GET", "/x") is None
    insert_endpoint_health(TEST_DATABASE_URL, 301, "a/repo1", "GET", "/x", True, 200, 120.5)
    last = get_last_endpoint_health(TEST_DATABASE_URL, 301, "a/repo1", "GET", "/x")
    assert last["reachable"] is True
    assert last["latency_ms"] == 120.5


@pytest.mark.asyncio
async def test_endpoint_health_rotation_keeps_20(pool):
    await _insert_installation(pool, 301, "a")
    for _ in range(21):
        insert_endpoint_health(TEST_DATABASE_URL, 301, "a/repo1", "GET", "/x", True, 200, 100.0, keep=20)

    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM endpoint_health WHERE installation_id = 301")
    assert count == 20


@pytest.mark.asyncio
async def test_check_and_reserve_managed_audit_blocks_second_run_within_cooldown(pool):
    await _insert_installation(pool, 301, "a")
    first = check_and_reserve_managed_audit(TEST_DATABASE_URL, 301, "a/repo1", cooldown_seconds=3600)
    second = check_and_reserve_managed_audit(TEST_DATABASE_URL, 301, "a/repo1", cooldown_seconds=3600)
    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_check_and_reserve_managed_audit_allows_after_cooldown_elapses(pool):
    await _insert_installation(pool, 301, "a")
    old_run = datetime(2020, 1, 1, tzinfo=timezone.utc)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO managed_audit_rate_limits (installation_id, repo_full_name, last_run_at)
            VALUES ($1, $2, $3)
            """,
            301,
            "a/repo1",
            old_run,
        )
    allowed = check_and_reserve_managed_audit(TEST_DATABASE_URL, 301, "a/repo1", cooldown_seconds=3600)
    assert allowed is True


@pytest.mark.asyncio
async def test_record_llm_spend_accumulates_sync(pool):
    await _insert_installation(pool, 301, "a")
    record_llm_spend(TEST_DATABASE_URL, 301, 0.10)
    record_llm_spend(TEST_DATABASE_URL, 301, 0.05)
    assert get_llm_spend_this_month(TEST_DATABASE_URL, 301) == pytest.approx(0.15)


@pytest.mark.asyncio
async def test_get_extra_seats_sync_defaults_to_zero(pool):
    await _insert_installation(pool, 301, "a")
    assert get_extra_seats(TEST_DATABASE_URL, 301) == 0


@pytest.mark.asyncio
async def test_check_and_reserve_flash_review_attempt_allows_first_and_blocks_second(pool):
    await _insert_installation(pool, 301, "a")
    first = check_and_reserve_flash_review_attempt(TEST_DATABASE_URL, 301, "a/repo1", 42)
    second = check_and_reserve_flash_review_attempt(TEST_DATABASE_URL, 301, "a/repo1", 42)
    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_check_and_reserve_flash_review_attempt_allows_after_debounce_elapses(pool):
    await _insert_installation(pool, 301, "a")
    check_and_reserve_flash_review_attempt(TEST_DATABASE_URL, 301, "a/repo1", 42, debounce_seconds=0)
    allowed = check_and_reserve_flash_review_attempt(
        TEST_DATABASE_URL, 301, "a/repo1", 42, debounce_seconds=0
    )
    assert allowed is True


@pytest.mark.asyncio
async def test_get_last_reviewed_sha_returns_none_before_any_review(pool):
    await _insert_installation(pool, 301, "a")
    check_and_reserve_flash_review_attempt(TEST_DATABASE_URL, 301, "a/repo1", 42)
    assert get_last_reviewed_sha(TEST_DATABASE_URL, 301, "a/repo1", 42) is None


@pytest.mark.asyncio
async def test_set_and_get_last_reviewed_sha_round_trips(pool):
    await _insert_installation(pool, 301, "a")
    check_and_reserve_flash_review_attempt(TEST_DATABASE_URL, 301, "a/repo1", 42)
    set_last_reviewed_sha(TEST_DATABASE_URL, 301, "a/repo1", 42, "deadbeef")
    assert get_last_reviewed_sha(TEST_DATABASE_URL, 301, "a/repo1", 42) == "deadbeef"
