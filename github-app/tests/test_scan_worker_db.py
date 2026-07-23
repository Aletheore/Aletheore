from datetime import datetime, timezone
import os

import pytest

from datetime import timedelta

from app_server.evidence_limits import EvidenceTooLargeError, MAX_EVIDENCE_BYTES
from scan_worker.db import (
    check_and_reserve_flash_review_attempt,
    check_and_reserve_managed_audit,
    delete_expired_sessions,
    delete_wiki_subsystems_not_in,
    get_extra_seats,
    get_last_endpoint_health,
    get_last_reviewed_sha,
    get_latest_evidence,
    get_llm_spend_this_month,
    get_wiki_overview,
    insert_endpoint_health,
    insert_repo_history,
    installation_spend_lock,
    list_health_check_targets_all,
    list_repos_for_installation,
    list_wiki_subsystems,
    record_llm_spend,
    set_last_reviewed_sha,
    upsert_wiki_overview,
    upsert_wiki_subsystem,
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


async def _insert_health_target(pool, installation_id: int, repo_full_name: str, base_url: str, **values) -> int:
    values.setdefault("label", "Primary")
    columns = ["installation_id", "repo_full_name", "base_url", *values.keys()]
    params = [installation_id, repo_full_name, base_url, *values.values()]
    placeholders = ", ".join(f"${i}" for i in range(1, len(params) + 1))
    return await pool.fetchval(
        f"INSERT INTO health_check_targets ({', '.join(columns)}) VALUES ({placeholders}) RETURNING id",
        *params,
    )


@pytest.mark.asyncio
async def test_list_health_check_targets_all_filters_by_plan(pool):
    await _insert_installation(pool, 301, "a", plan="indie")
    await _insert_health_target(pool, 301, "a/repo1", "https://a.example.com")
    await _insert_installation(pool, 302, "b", plan="free")
    await _insert_health_target(pool, 302, "b/repo1", "https://b.example.com")
    await _insert_installation(pool, 303, "c", plan="indie")

    targets = list_health_check_targets_all(TEST_DATABASE_URL)
    installation_ids = {t["installation_id"] for t in targets}
    assert installation_ids == {301}


@pytest.mark.asyncio
async def test_list_health_check_targets_all_includes_webhook_url_and_repo(pool):
    await _insert_installation(pool, 304, "d", plan="indie", webhook_url="https://hooks.slack.com/d")
    await _insert_health_target(pool, 304, "d/repo1", "https://d.example.com", latency_threshold_ms=2000)

    targets = list_health_check_targets_all(TEST_DATABASE_URL)
    row = next(t for t in targets if t["installation_id"] == 304)
    assert row["webhook_url"] == "https://hooks.slack.com/d"
    assert row["repo_full_name"] == "d/repo1"
    assert row["base_url"] == "https://d.example.com"
    assert row["latency_threshold_ms"] == 2000


@pytest.mark.asyncio
async def test_list_health_check_targets_all_returns_one_row_per_target(pool):
    await _insert_installation(pool, 305, "e", plan="indie")
    await _insert_health_target(pool, 305, "e/repo1", "https://staging.example.com")
    await _insert_health_target(pool, 305, "e/repo1", "https://prod.example.com")

    targets = [t for t in list_health_check_targets_all(TEST_DATABASE_URL) if t["installation_id"] == 305]
    assert len(targets) == 2
    assert {t["base_url"] for t in targets} == {"https://staging.example.com", "https://prod.example.com"}


@pytest.mark.asyncio
async def test_list_repos_for_installation(pool):
    await _insert_installation(pool, 301, "a")
    insert_repo_history(TEST_DATABASE_URL, 301, "a/repo1", datetime.now(timezone.utc), {"x": 1})
    insert_repo_history(TEST_DATABASE_URL, 301, "a/repo2", datetime.now(timezone.utc), {"x": 1})

    repos = list_repos_for_installation(TEST_DATABASE_URL, 301)
    assert set(repos) == {"a/repo1", "a/repo2"}


@pytest.mark.asyncio
async def test_insert_repo_history_rejects_oversized_evidence(pool):
    await _insert_installation(pool, 301, "a")
    oversized = {"padding": "x" * (MAX_EVIDENCE_BYTES + 1)}
    with pytest.raises(EvidenceTooLargeError):
        insert_repo_history(TEST_DATABASE_URL, 301, "a/repo1", datetime.now(timezone.utc), oversized)
    assert list_repos_for_installation(TEST_DATABASE_URL, 301) == []


@pytest.mark.asyncio
async def test_delete_expired_sessions_removes_only_expired_rows(pool):
    now = datetime.now(timezone.utc)
    await pool.execute(
        """
        INSERT INTO sessions (id, github_user_id, github_login, github_access_token, expires_at)
        VALUES ($1, $2, $3, $4, $5)
        """,
        "expired-session",
        1,
        "octocat",
        "token-a",
        now - timedelta(hours=1),
    )
    await pool.execute(
        """
        INSERT INTO sessions (id, github_user_id, github_login, github_access_token, expires_at)
        VALUES ($1, $2, $3, $4, $5)
        """,
        "active-session",
        2,
        "hubot",
        "token-b",
        now + timedelta(hours=1),
    )

    deleted = delete_expired_sessions(TEST_DATABASE_URL)

    assert deleted == 1
    remaining = await pool.fetch("SELECT id FROM sessions")
    assert {row["id"] for row in remaining} == {"active-session"}


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
async def test_endpoint_health_is_scoped_per_target(pool):
    # Two targets checking the exact same method+path on the same repo (e.g.
    # staging and production) must not see or overwrite each other's history.
    await _insert_installation(pool, 306, "f", plan="indie")
    staging_id = await _insert_health_target(pool, 306, "f/repo1", "https://staging.example.com")
    prod_id = await _insert_health_target(pool, 306, "f/repo1", "https://prod.example.com")

    insert_endpoint_health(TEST_DATABASE_URL, 306, "f/repo1", "GET", "/x", True, 200, 50.0, target_id=staging_id)
    insert_endpoint_health(TEST_DATABASE_URL, 306, "f/repo1", "GET", "/x", False, 503, None, target_id=prod_id)

    staging_last = get_last_endpoint_health(TEST_DATABASE_URL, 306, "f/repo1", "GET", "/x", target_id=staging_id)
    prod_last = get_last_endpoint_health(TEST_DATABASE_URL, 306, "f/repo1", "GET", "/x", target_id=prod_id)

    assert staging_last["reachable"] is True
    assert prod_last["reachable"] is False


@pytest.mark.asyncio
async def test_endpoint_health_rotation_is_scoped_per_target(pool):
    await _insert_installation(pool, 307, "g", plan="indie")
    target_a = await _insert_health_target(pool, 307, "g/repo1", "https://a.example.com")
    target_b = await _insert_health_target(pool, 307, "g/repo1", "https://b.example.com")

    for _ in range(21):
        insert_endpoint_health(TEST_DATABASE_URL, 307, "g/repo1", "GET", "/x", True, 200, 100.0, target_id=target_a, keep=20)
    insert_endpoint_health(TEST_DATABASE_URL, 307, "g/repo1", "GET", "/x", True, 200, 100.0, target_id=target_b, keep=20)

    async with pool.acquire() as conn:
        count_a = await conn.fetchval("SELECT count(*) FROM endpoint_health WHERE target_id = $1", target_a)
        count_b = await conn.fetchval("SELECT count(*) FROM endpoint_health WHERE target_id = $1", target_b)
    assert count_a == 20
    assert count_b == 1


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


@pytest.mark.asyncio
async def test_installation_spend_lock_blocks_concurrent_acquisition(pool):
    import psycopg

    with installation_spend_lock(TEST_DATABASE_URL, 301):
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(301)")
                acquired = cur.fetchone()[0]
        assert acquired is False


@pytest.mark.asyncio
async def test_installation_spend_lock_releases_after_context_exits(pool):
    import psycopg

    with installation_spend_lock(TEST_DATABASE_URL, 301):
        pass

    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(301)")
            acquired = cur.fetchone()[0]
            cur.execute("SELECT pg_advisory_unlock(301)")
    assert acquired is True


@pytest.mark.asyncio
async def test_upsert_and_get_wiki_overview(pool):
    await _insert_installation(pool, 301, "a")

    upsert_wiki_overview(TEST_DATABASE_URL, 301, "a/repo1", "First description.", "flowchart TD", "sha1")
    row = get_wiki_overview(TEST_DATABASE_URL, 301, "a/repo1")

    assert row["description"] == "First description."
    assert row["diagram_mermaid"] == "flowchart TD"
    assert row["source_commit"] == "sha1"


@pytest.mark.asyncio
async def test_upsert_wiki_overview_overwrites_on_conflict(pool):
    await _insert_installation(pool, 301, "a")

    upsert_wiki_overview(TEST_DATABASE_URL, 301, "a/repo1", "First.", "diagram1", "sha1")
    upsert_wiki_overview(TEST_DATABASE_URL, 301, "a/repo1", "Second.", "diagram2", "sha2")

    row = get_wiki_overview(TEST_DATABASE_URL, 301, "a/repo1")
    assert row["description"] == "Second."
    assert row["source_commit"] == "sha2"


@pytest.mark.asyncio
async def test_get_wiki_overview_returns_none_when_missing(pool):
    await _insert_installation(pool, 301, "a")
    assert get_wiki_overview(TEST_DATABASE_URL, 301, "a/repo1") is None


@pytest.mark.asyncio
async def test_upsert_and_list_wiki_subsystems(pool):
    await _insert_installation(pool, 301, "a")

    upsert_wiki_subsystem(
        TEST_DATABASE_URL, 301, "a/repo1", "0", "Authentication", "Handles login.",
        [{"path": "auth/login.py", "role": "entry point", "key_symbols": []}], "flowchart TD", "sha1",
    )
    upsert_wiki_subsystem(
        TEST_DATABASE_URL, 301, "a/repo1", "1", "Billing", "Handles payments.",
        [], "flowchart TD", "sha1",
    )

    subsystems = list_wiki_subsystems(TEST_DATABASE_URL, 301, "a/repo1")

    assert len(subsystems) == 2
    names = {s["name"] for s in subsystems}
    assert names == {"Authentication", "Billing"}
    auth = next(s for s in subsystems if s["name"] == "Authentication")
    assert auth["files"] == [{"path": "auth/login.py", "role": "entry point", "key_symbols": []}]


@pytest.mark.asyncio
async def test_upsert_wiki_subsystem_overwrites_on_conflict(pool):
    await _insert_installation(pool, 301, "a")

    upsert_wiki_subsystem(TEST_DATABASE_URL, 301, "a/repo1", "0", "Auth", "First.", [], "d1", "sha1")
    upsert_wiki_subsystem(TEST_DATABASE_URL, 301, "a/repo1", "0", "Auth v2", "Second.", [], "d2", "sha2")

    subsystems = list_wiki_subsystems(TEST_DATABASE_URL, 301, "a/repo1")
    assert len(subsystems) == 1
    assert subsystems[0]["name"] == "Auth v2"
    assert subsystems[0]["description"] == "Second."


@pytest.mark.asyncio
async def test_delete_wiki_subsystems_not_in_removes_stale_clusters(pool):
    await _insert_installation(pool, 301, "a")

    upsert_wiki_subsystem(TEST_DATABASE_URL, 301, "a/repo1", "0", "Auth", "d", [], "diag", "sha1")
    upsert_wiki_subsystem(TEST_DATABASE_URL, 301, "a/repo1", "1", "Billing", "d", [], "diag", "sha1")
    upsert_wiki_subsystem(TEST_DATABASE_URL, 301, "a/repo1", "2", "Stale", "d", [], "diag", "sha1")

    delete_wiki_subsystems_not_in(TEST_DATABASE_URL, 301, "a/repo1", ["0", "1"])

    subsystems = list_wiki_subsystems(TEST_DATABASE_URL, 301, "a/repo1")
    ids = {s["subsystem_id"] for s in subsystems}
    assert ids == {"0", "1"}


@pytest.mark.asyncio
async def test_wiki_subsystems_are_scoped_per_repo(pool):
    await _insert_installation(pool, 301, "a")

    upsert_wiki_subsystem(TEST_DATABASE_URL, 301, "a/repo1", "0", "Auth", "d", [], "diag", "sha1")
    upsert_wiki_subsystem(TEST_DATABASE_URL, 301, "a/repo2", "0", "Other", "d", [], "diag", "sha1")

    repo1_subsystems = list_wiki_subsystems(TEST_DATABASE_URL, 301, "a/repo1")
    assert len(repo1_subsystems) == 1
    assert repo1_subsystems[0]["name"] == "Auth"
