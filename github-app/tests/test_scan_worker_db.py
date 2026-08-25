from datetime import datetime, timezone
import os

import pytest

import scan_worker.db as scan_worker_db

from datetime import timedelta

from aletheore.evidence import EVIDENCE_VERSION
from app_server.evidence_limits import EvidenceTooLargeError, MAX_EVIDENCE_BYTES
from scan_worker.db import (
    check_and_reserve_flash_review_attempt,
    check_and_reserve_managed_audit,
    managed_audit_definitely_still_cooling_down,
    check_and_reserve_monthly_repo_scan_slot,
    count_repo_scans_since,
    delete_docs_symbols_not_in,
    get_docs_symbol_hashes,
    delete_expired_endpoint_health,
    delete_expired_sessions,
    delete_expired_webhook_deliveries,
    delete_wiki_subsystems_not_in,
    email_already_sent,
    get_dismissed_identity_keys,
    get_endpoint_health_summary,
    get_extra_seats,
    get_last_endpoint_health,
    get_last_reviewed_sha,
    get_latest_evidence,
    get_llm_spend_this_month,
    get_seconds_since_last_health_check,
    get_wiki_overview,
    insert_endpoint_health,
    insert_repo_history,
    installation_spend_lock,
    repo_checkout_lock,
    REPO_CHECKOUT_LOCK_NAMESPACE,
    SCAN_SLOT_LOCK_NAMESPACE,
    SPEND_LOCK_NAMESPACE,
    list_health_check_targets_all,
    list_docs_symbols,
    list_installation_member_emails,
    list_paid_installations_due_for_digest,
    list_paid_repos_due_for_docs_catchup,
    list_paid_repos_due_for_wiki_catchup,
    list_repos_for_installation,
    list_wiki_subsystems,
    record_digest_sent,
    record_docs_catchup_swept,
    record_llm_spend,
    record_sent_email,
    record_wiki_catchup_swept,
    release_flash_review_count_reservation,
    release_llm_spend_reservation,
    reserve_flash_review_count,
    reserve_llm_spend,
    set_last_reviewed_sha,
    upsert_docs_symbol,
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
async def test_list_health_check_targets_all_includes_alert_email(pool):
    await _insert_installation(pool, 306, "f", plan="indie", alert_email="ops@f.example.com")
    await _insert_health_target(pool, 306, "f/repo1", "https://f.example.com")

    targets = list_health_check_targets_all(TEST_DATABASE_URL)
    row = next(t for t in targets if t["installation_id"] == 306)
    assert row["alert_email"] == "ops@f.example.com"


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
async def test_insert_and_list_evidence_packet_cache_rows(pool):
    await _insert_installation(pool, 401, "cache-org")

    from scan_worker.db import insert_evidence_packet_cache_row, list_recent_evidence_packet_cache_rows

    insert_evidence_packet_cache_row(
        TEST_DATABASE_URL,
        401,
        "cache-org/repo",
        "hash-1",
        [0.1, 0.2, 0.3],
        {"changed_files": ["a.py"]},
        {"description": "does a thing"},
        "deepseek-v4-pro",
        "test-embedder",
    )

    rows = list_recent_evidence_packet_cache_rows(TEST_DATABASE_URL, 401, "cache-org/repo", "test-embedder")

    assert len(rows) == 1
    assert rows[0]["content_hash"] == "hash-1"
    assert rows[0]["embedding"] == [0.1, 0.2, 0.3]
    assert rows[0]["packet_json"]["changed_files"] == ["a.py"]
    assert rows[0]["model_output"]["description"] == "does a thing"
    assert rows[0]["model_used"] == "deepseek-v4-pro"


@pytest.mark.asyncio
async def test_list_evidence_packet_cache_rows_never_crosses_installations(pool):
    await _insert_installation(pool, 402, "org-a")
    await _insert_installation(pool, 403, "org-b")

    from scan_worker.db import insert_evidence_packet_cache_row, list_recent_evidence_packet_cache_rows

    insert_evidence_packet_cache_row(
        TEST_DATABASE_URL, 402, "org-a/repo", "hash-a", [1.0], {}, {"description": "a"}, "deepseek-v4-pro", "test-embedder"
    )
    insert_evidence_packet_cache_row(
        TEST_DATABASE_URL, 403, "org-b/repo", "hash-b", [1.0], {}, {"description": "b"}, "deepseek-v4-pro", "test-embedder"
    )

    rows = list_recent_evidence_packet_cache_rows(TEST_DATABASE_URL, 402, "org-a/repo", "test-embedder")

    assert len(rows) == 1
    assert rows[0]["content_hash"] == "hash-a"


@pytest.mark.asyncio
async def test_insert_and_list_flash_review_cache_rows(pool):
    await _insert_installation(pool, 411, "flash-org")

    from scan_worker.db import insert_flash_review_cache_row, list_recent_flash_review_cache_rows

    insert_flash_review_cache_row(
        TEST_DATABASE_URL,
        411,
        "flash-org/repo",
        "hash-1",
        [0.1, 0.2, 0.3],
        "--- a.py ---\n@@ -1,1 +1,1 @@\n+x = 1",
        [{"file": "a.py", "line": 1, "issue": "unused variable"}],
        "deepseek-v4-flash",
        "test-embedder",
    )

    rows = list_recent_flash_review_cache_rows(TEST_DATABASE_URL, 411, "flash-org/repo", "test-embedder")

    assert len(rows) == 1
    assert rows[0]["content_hash"] == "hash-1"
    assert rows[0]["embedding"] == [0.1, 0.2, 0.3]
    assert rows[0]["diff_text"] == "--- a.py ---\n@@ -1,1 +1,1 @@\n+x = 1"
    assert rows[0]["findings"] == [{"file": "a.py", "line": 1, "issue": "unused variable"}]
    assert rows[0]["model_used"] == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_list_flash_review_cache_rows_never_crosses_installations(pool):
    await _insert_installation(pool, 412, "org-a")
    await _insert_installation(pool, 413, "org-b")

    from scan_worker.db import insert_flash_review_cache_row, list_recent_flash_review_cache_rows

    insert_flash_review_cache_row(
        TEST_DATABASE_URL, 412, "org-a/repo", "hash-a", [1.0], "diff a", [], "deepseek-v4-flash", "test-embedder"
    )
    insert_flash_review_cache_row(
        TEST_DATABASE_URL, 413, "org-b/repo", "hash-b", [1.0], "diff b", [], "deepseek-v4-flash", "test-embedder"
    )

    rows = list_recent_flash_review_cache_rows(TEST_DATABASE_URL, 412, "org-a/repo", "test-embedder")

    assert len(rows) == 1
    assert rows[0]["content_hash"] == "hash-a"


@pytest.mark.asyncio
async def test_record_flash_review_cache_hit_updates_hit_count_and_last_hit_at(pool):
    await _insert_installation(pool, 414, "hit-org")

    from scan_worker.db import (
        insert_flash_review_cache_row,
        list_recent_flash_review_cache_rows,
        record_flash_review_cache_hit,
    )

    insert_flash_review_cache_row(
        TEST_DATABASE_URL, 414, "hit-org/repo", "hash-1", [1.0], "diff", [], "deepseek-v4-flash", "test-embedder"
    )
    row_id = list_recent_flash_review_cache_rows(TEST_DATABASE_URL, 414, "hit-org/repo", "test-embedder")[0]["id"]

    record_flash_review_cache_hit(TEST_DATABASE_URL, row_id)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT hit_count, last_hit_at FROM flash_review_cache WHERE id = $1", row_id
        )
    assert row["hit_count"] == 1
    assert row["last_hit_at"] is not None


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
async def test_delete_expired_webhook_deliveries_keeps_rows_inside_the_window(pool):
    now = datetime.now(timezone.utc)
    await pool.execute(
        "INSERT INTO webhook_deliveries (source, delivery_id, event, received_at) "
        "VALUES ('github', $1, $2, $3)",
        "old-delivery",
        "push",
        now - timedelta(days=31),
    )
    await pool.execute(
        "INSERT INTO webhook_deliveries (source, delivery_id, event, received_at) "
        "VALUES ('github', $1, $2, $3)",
        "recent-delivery",
        "push",
        now - timedelta(days=29),
    )

    deleted = delete_expired_webhook_deliveries(TEST_DATABASE_URL, 30)

    assert deleted == 1
    remaining = await pool.fetch("SELECT delivery_id FROM webhook_deliveries")
    assert {row["delivery_id"] for row in remaining} == {"recent-delivery"}


@pytest.mark.asyncio
async def test_delete_expired_endpoint_health_keeps_rows_inside_the_window(pool):
    await _insert_installation(pool, 302, "health-org")
    now = datetime.now(timezone.utc)
    await pool.execute(
        """
        INSERT INTO endpoint_health
            (installation_id, repo_full_name, endpoint_method, endpoint_path,
             reachable, checked_at)
        VALUES
            (302, 'health-org/repo', 'GET', '/old', true, $1),
            (302, 'health-org/repo', 'GET', '/recent', true, $2)
        """,
        now - timedelta(days=31),
        now - timedelta(days=29),
    )

    deleted = delete_expired_endpoint_health(TEST_DATABASE_URL, 30)

    assert deleted == 1
    remaining = await pool.fetch("SELECT endpoint_path FROM endpoint_health")
    assert {row["endpoint_path"] for row in remaining} == {"/recent"}


@pytest.mark.asyncio
async def test_delete_expired_flash_review_cache_keeps_rows_inside_the_window(pool):
    # flash_review_cache stores a real PR diff (source code) per row, unlike
    # every other table this cleanup pattern already covers - a real
    # retention limit, not just an operational-metadata one.
    await _insert_installation(pool, 415, "cache-retention-org")
    now = datetime.now(timezone.utc)
    await pool.execute(
        """
        INSERT INTO flash_review_cache
            (installation_id, repo_full_name, content_hash, embedding,
             diff_text, findings, model_used, created_at)
        VALUES
            (415, 'cache-retention-org/repo', 'old-hash', '{0.1}',
             'old diff', '[]', 'deepseek-v4-flash', $1),
            (415, 'cache-retention-org/repo', 'recent-hash', '{0.2}',
             'recent diff', '[]', 'deepseek-v4-flash', $2)
        """,
        now - timedelta(days=31),
        now - timedelta(days=29),
    )

    from scan_worker.db import delete_expired_flash_review_cache

    deleted = delete_expired_flash_review_cache(TEST_DATABASE_URL, 30)

    assert deleted == 1
    remaining = await pool.fetch("SELECT content_hash FROM flash_review_cache")
    assert {row["content_hash"] for row in remaining} == {"recent-hash"}


@pytest.mark.asyncio
async def test_get_latest_evidence_returns_most_recent(pool):
    await _insert_installation(pool, 301, "a")
    # Version-stamped because get_latest_evidence now refuses evidence written
    # by an incompatible schema - a bare {"v": N} is not something any scan
    # ever produced, so asserting against it was testing an impossible input.
    insert_repo_history(
        TEST_DATABASE_URL, 301, "a/repo1", datetime(2026, 1, 1, tzinfo=timezone.utc),
        {"aletheore_version": EVIDENCE_VERSION, "v": 1},
    )
    insert_repo_history(
        TEST_DATABASE_URL, 301, "a/repo1", datetime(2026, 1, 2, tzinfo=timezone.utc),
        {"aletheore_version": EVIDENCE_VERSION, "v": 2},
    )

    evidence = get_latest_evidence(TEST_DATABASE_URL, 301, "a/repo1")
    assert evidence["v"] == 2


@pytest.mark.asyncio
async def test_get_latest_evidence_ignores_rows_from_an_incompatible_version(pool):
    """repo_history rows outlive the schema that wrote them. The CLI, MCP
    server and dashboard all version-check evidence before reading it; this
    path did not, so an EVIDENCE_VERSION bump would leave its five callers
    reading old-shaped rows as current and KeyError on the first new key.

    None rather than raising: every caller already handles it as the normal
    never-scanned-yet case, and the next scan overwrites the row anyway.
    """
    await _insert_installation(pool, 302, "a")
    insert_repo_history(
        TEST_DATABASE_URL, 302, "a/repo1", datetime(2026, 1, 1, tzinfo=timezone.utc),
        {"aletheore_version": "0.1.0", "repository": {}},
    )

    assert get_latest_evidence(TEST_DATABASE_URL, 302, "a/repo1") is None

    # A compatible row written afterwards is picked up normally, so this is
    # a per-row check and not a latch that disables the whole read path.
    insert_repo_history(
        TEST_DATABASE_URL, 302, "a/repo1", datetime(2026, 1, 2, tzinfo=timezone.utc),
        {"aletheore_version": EVIDENCE_VERSION, "v": 9},
    )
    assert get_latest_evidence(TEST_DATABASE_URL, 302, "a/repo1")["v"] == 9


@pytest.mark.asyncio
async def test_insert_repo_history_returns_the_new_rows_id(pool):
    await _insert_installation(pool, 303, "a")
    history_id = insert_repo_history(
        TEST_DATABASE_URL, 303, "a/repo1", datetime(2026, 1, 1, tzinfo=timezone.utc),
        {"aletheore_version": EVIDENCE_VERSION, "v": 1},
    )
    row = await pool.fetchrow("SELECT id FROM repo_history WHERE installation_id = 303")
    assert history_id == row["id"]


@pytest.mark.asyncio
async def test_get_evidence_by_id_returns_the_exact_row_not_the_latest(pool):
    # Real bug this guards: a queued follow-up job that reloaded evidence
    # via get_latest_evidence (rather than the specific row its own scan
    # persisted) would silently pick up a newer, unrelated scan's evidence
    # if one landed first - see run_live_wiki_incremental_update_job's
    # docstring for the production incident this caused.
    from scan_worker.db import get_evidence_by_id

    await _insert_installation(pool, 304, "a")
    first_id = insert_repo_history(
        TEST_DATABASE_URL, 304, "a/repo1", datetime(2026, 1, 1, tzinfo=timezone.utc),
        {"aletheore_version": EVIDENCE_VERSION, "v": "first"},
    )
    insert_repo_history(
        TEST_DATABASE_URL, 304, "a/repo1", datetime(2026, 1, 2, tzinfo=timezone.utc),
        {"aletheore_version": EVIDENCE_VERSION, "v": "second-and-latest"},
    )

    evidence = get_evidence_by_id(TEST_DATABASE_URL, 304, "a/repo1", first_id)

    assert evidence["v"] == "first"
    # Confirms this isn't accidentally equivalent to get_latest_evidence.
    assert get_latest_evidence(TEST_DATABASE_URL, 304, "a/repo1")["v"] == "second-and-latest"


@pytest.mark.asyncio
async def test_get_evidence_by_id_returns_none_for_wrong_installation_or_repo(pool):
    from scan_worker.db import get_evidence_by_id

    await _insert_installation(pool, 305, "a")
    await _insert_installation(pool, 306, "b")
    history_id = insert_repo_history(
        TEST_DATABASE_URL, 305, "a/repo1", datetime(2026, 1, 1, tzinfo=timezone.utc),
        {"aletheore_version": EVIDENCE_VERSION, "v": "belongs-to-305"},
    )

    assert get_evidence_by_id(TEST_DATABASE_URL, 306, "a/repo1", history_id) is None
    assert get_evidence_by_id(TEST_DATABASE_URL, 305, "b/repo1", history_id) is None
    assert get_evidence_by_id(TEST_DATABASE_URL, 305, "a/repo1", history_id)["v"] == "belongs-to-305"


@pytest.mark.asyncio
async def test_get_evidence_by_id_ignores_an_incompatible_version(pool):
    from scan_worker.db import get_evidence_by_id

    await _insert_installation(pool, 307, "a")
    history_id = insert_repo_history(
        TEST_DATABASE_URL, 307, "a/repo1", datetime(2026, 1, 1, tzinfo=timezone.utc),
        {"aletheore_version": "0.1.0", "repository": {}},
    )

    assert get_evidence_by_id(TEST_DATABASE_URL, 307, "a/repo1", history_id) is None


@pytest.mark.asyncio
async def test_insert_and_get_last_endpoint_health(pool):
    await _insert_installation(pool, 301, "a")

    assert get_last_endpoint_health(TEST_DATABASE_URL, 301, "a/repo1", "GET", "/x") is None
    insert_endpoint_health(TEST_DATABASE_URL, 301, "a/repo1", "GET", "/x", True, 200, 120.5)
    last = get_last_endpoint_health(TEST_DATABASE_URL, 301, "a/repo1", "GET", "/x")
    assert last["reachable"] is True
    assert last["latency_ms"] == 120.5


@pytest.mark.asyncio
async def test_insert_audit_report(pool):
    await _insert_installation(pool, 421, "audit-org")

    from scan_worker.db import insert_audit_report

    insert_audit_report(
        TEST_DATABASE_URL,
        421,
        "audit-org/repo",
        "tok-abc123",
        "the report text",
        "hash-1",
        "sig-1",
        "pubkey-1",
    )

    row = await pool.fetchrow(
        "SELECT * FROM audit_reports WHERE verification_token = 'tok-abc123'"
    )
    assert row["installation_id"] == 421
    assert row["repo_full_name"] == "audit-org/repo"
    assert row["report_text"] == "the report text"
    assert row["content_hash"] == "hash-1"
    assert row["signature"] == "sig-1"
    # Recorded per report so a later key rotation can't invalidate it.
    assert row["signing_public_key"] == "pubkey-1"


@pytest.mark.asyncio
async def test_list_recent_endpoint_incidents_groups_by_endpoint(pool):
    await _insert_installation(pool, 431, "incident-org")

    from scan_worker.db import list_recent_endpoint_incidents

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO endpoint_health
                (installation_id, repo_full_name, endpoint_method, endpoint_path, reachable)
            VALUES
                (431, 'incident-org/repo', 'GET', '/api/users', false),
                (431, 'incident-org/repo', 'GET', '/api/users', false),
                (431, 'incident-org/repo', 'GET', '/api/orders', true)
            """
        )

    since = datetime.now(timezone.utc) - timedelta(days=7)
    incidents = list_recent_endpoint_incidents(
        TEST_DATABASE_URL,
        431,
        "incident-org/repo",
        since,
    )

    assert len(incidents) == 1
    assert incidents[0]["endpoint_method"] == "GET"
    assert incidents[0]["endpoint_path"] == "/api/users"
    assert incidents[0]["incident_count"] == 2


@pytest.mark.asyncio
async def test_list_recent_endpoint_incidents_excludes_old_incidents(pool):
    await _insert_installation(pool, 432, "incident-org")

    from scan_worker.db import list_recent_endpoint_incidents

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO endpoint_health
                (installation_id, repo_full_name, endpoint_method, endpoint_path, reachable, checked_at)
            VALUES (432, 'incident-org/repo', 'GET', '/api/stale', false, now() - interval '30 days')
            """
        )

    since = datetime.now(timezone.utc) - timedelta(days=7)
    incidents = list_recent_endpoint_incidents(
        TEST_DATABASE_URL,
        432,
        "incident-org/repo",
        since,
    )

    assert incidents == []


@pytest.mark.asyncio
async def test_insert_and_get_last_endpoint_health_with_response_shape(pool):
    await _insert_installation(pool, 301, "a")

    insert_endpoint_health(
        TEST_DATABASE_URL,
        301,
        "a/repo1",
        "GET",
        "/x",
        True,
        200,
        120.5,
        response_shape=["email", "id", "name"],
    )
    last = get_last_endpoint_health(TEST_DATABASE_URL, 301, "a/repo1", "GET", "/x")

    assert last["response_shape"] == ["email", "id", "name"]


@pytest.mark.asyncio
async def test_insert_endpoint_health_defaults_response_shape_to_none(pool):
    await _insert_installation(pool, 301, "a")

    insert_endpoint_health(TEST_DATABASE_URL, 301, "a/repo1", "GET", "/x", True, 200, 120.5)
    last = get_last_endpoint_health(TEST_DATABASE_URL, 301, "a/repo1", "GET", "/x")

    assert last["response_shape"] is None


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
async def test_managed_audit_definitely_still_cooling_down_true_for_a_recent_run(pool):
    await _insert_installation(pool, 303, "a")
    check_and_reserve_managed_audit(TEST_DATABASE_URL, 303, "a/repo1", cooldown_seconds=3600)
    assert (
        managed_audit_definitely_still_cooling_down(
            TEST_DATABASE_URL, 303, "a/repo1", min_cooldown_seconds=3600
        )
        is True
    )


@pytest.mark.asyncio
async def test_managed_audit_definitely_still_cooling_down_false_for_an_old_run(pool):
    await _insert_installation(pool, 304, "a")
    old_run = datetime(2020, 1, 1, tzinfo=timezone.utc)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO managed_audit_rate_limits (installation_id, repo_full_name, last_run_at)
            VALUES ($1, $2, $3)
            """,
            304,
            "a/repo1",
            old_run,
        )
    assert (
        managed_audit_definitely_still_cooling_down(
            TEST_DATABASE_URL, 304, "a/repo1", min_cooldown_seconds=3600
        )
        is False
    )


@pytest.mark.asyncio
async def test_managed_audit_definitely_still_cooling_down_false_when_never_run(pool):
    await _insert_installation(pool, 305, "a")
    assert (
        managed_audit_definitely_still_cooling_down(
            TEST_DATABASE_URL, 305, "a/repo1", min_cooldown_seconds=3600
        )
        is False
    )


@pytest.mark.asyncio
async def test_managed_audit_definitely_still_cooling_down_does_not_reserve_a_slot(pool):
    # Read-only pre-check - unlike check_and_reserve_managed_audit, calling
    # this must not itself count as a run or block a later real check.
    await _insert_installation(pool, 306, "a")
    managed_audit_definitely_still_cooling_down(TEST_DATABASE_URL, 306, "a/repo1", min_cooldown_seconds=3600)
    allowed = check_and_reserve_managed_audit(TEST_DATABASE_URL, 306, "a/repo1", cooldown_seconds=3600)
    assert allowed is True


@pytest.mark.asyncio
async def test_monthly_repo_scan_slot_allows_up_to_the_limit(pool):
    await _insert_installation(pool, 302, "a")
    for i in range(3):
        allowed = check_and_reserve_monthly_repo_scan_slot(TEST_DATABASE_URL, 302, f"a/repo{i}", limit=3)
        assert allowed is True


@pytest.mark.asyncio
async def test_monthly_repo_scan_slot_blocks_a_new_repo_past_the_limit(pool):
    await _insert_installation(pool, 303, "a")
    for i in range(3):
        check_and_reserve_monthly_repo_scan_slot(TEST_DATABASE_URL, 303, f"a/repo{i}", limit=3)
    blocked = check_and_reserve_monthly_repo_scan_slot(TEST_DATABASE_URL, 303, "a/repo-new", limit=3)
    assert blocked is False


@pytest.mark.asyncio
async def test_monthly_repo_scan_slot_always_allows_an_already_counted_repo(pool):
    await _insert_installation(pool, 304, "a")
    for i in range(3):
        check_and_reserve_monthly_repo_scan_slot(TEST_DATABASE_URL, 304, f"a/repo{i}", limit=3)
    allowed_again = check_and_reserve_monthly_repo_scan_slot(TEST_DATABASE_URL, 304, "a/repo0", limit=3)
    assert allowed_again is True


@pytest.mark.asyncio
async def test_monthly_repo_scan_slot_is_scoped_per_installation(pool):
    await _insert_installation(pool, 305, "a")
    await _insert_installation(pool, 306, "b")
    for i in range(3):
        check_and_reserve_monthly_repo_scan_slot(TEST_DATABASE_URL, 305, f"a/repo{i}", limit=3)
    allowed = check_and_reserve_monthly_repo_scan_slot(TEST_DATABASE_URL, 306, "b/repo0", limit=3)
    assert allowed is True


@pytest.mark.asyncio
async def test_record_llm_spend_accumulates_sync(pool):
    await _insert_installation(pool, 301, "a")
    record_llm_spend(TEST_DATABASE_URL, 301, 0.10)
    record_llm_spend(TEST_DATABASE_URL, 301, 0.05)
    assert get_llm_spend_this_month(TEST_DATABASE_URL, 301) == pytest.approx(0.15)


@pytest.mark.asyncio
async def test_record_llm_spend_sync_without_monthly_cap_never_warns(pool, caplog):
    await _insert_installation(pool, 301, "a")
    with caplog.at_level("WARNING"):
        record_llm_spend(TEST_DATABASE_URL, 301, 10.00)
    assert caplog.records == []


@pytest.mark.asyncio
async def test_record_llm_spend_sync_warns_once_when_crossing_the_threshold(pool, caplog):
    await _insert_installation(pool, 301, "a")
    with caplog.at_level("WARNING"):
        record_llm_spend(TEST_DATABASE_URL, 301, 2.00, monthly_cap=15.00)  # under 30% ($4.50)
        record_llm_spend(TEST_DATABASE_URL, 301, 5.00, monthly_cap=15.00)  # $7 total - crosses it
        record_llm_spend(TEST_DATABASE_URL, 301, 1.00, monthly_cap=15.00)  # already over

    warnings = [r for r in caplog.records if "installation=301" in r.message]
    assert len(warnings) == 1
    assert "30%" in warnings[0].message


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
async def test_reserve_flash_review_count_allows_up_to_limit_then_blocks(pool):
    await _insert_installation(pool, 401, "a")
    first = reserve_flash_review_count(TEST_DATABASE_URL, 401, limit=2)
    second = reserve_flash_review_count(TEST_DATABASE_URL, 401, limit=2)
    third = reserve_flash_review_count(TEST_DATABASE_URL, 401, limit=2)
    assert (first, second, third) == (True, True, False)


@pytest.mark.asyncio
async def test_release_flash_review_count_reservation_frees_a_slot(pool):
    await _insert_installation(pool, 402, "a")
    reserve_flash_review_count(TEST_DATABASE_URL, 402, limit=1)
    blocked = reserve_flash_review_count(TEST_DATABASE_URL, 402, limit=1)
    assert blocked is False

    release_flash_review_count_reservation(TEST_DATABASE_URL, 402)
    allowed_again = reserve_flash_review_count(TEST_DATABASE_URL, 402, limit=1)
    assert allowed_again is True


@pytest.mark.asyncio
async def test_release_flash_review_count_reservation_never_goes_negative(pool):
    await _insert_installation(pool, 403, "a")
    # Nothing reserved yet - a double-release (or a release with no matching
    # reserve) must not underflow the count into negative territory.
    release_flash_review_count_reservation(TEST_DATABASE_URL, 403)
    release_flash_review_count_reservation(TEST_DATABASE_URL, 403)
    allowed = reserve_flash_review_count(TEST_DATABASE_URL, 403, limit=1)
    assert allowed is True


@pytest.mark.asyncio
async def test_reserve_flash_review_count_is_atomic_under_real_concurrency(pool):
    # This is the actual regression test for the TOCTOU race
    # run_flash_review_job used to have: many real, concurrent callers
    # racing the same (installation_id, month) row, same as two Flash
    # Reviews for one installation landing on different scan-worker
    # replicas at once. A read-then-write check-then-act would let more
    # than `limit` through under real thread-level concurrency; the atomic
    # UPSERT...WHERE...RETURNING must not, no matter how many callers race
    # it at once.
    import concurrent.futures

    await _insert_installation(pool, 404, "a")
    limit = 20
    attempts = 60

    with concurrent.futures.ThreadPoolExecutor(max_workers=attempts) as pool_exec:
        results = list(
            pool_exec.map(
                lambda _: reserve_flash_review_count(TEST_DATABASE_URL, 404, limit=limit),
                range(attempts),
            )
        )

    assert sum(results) == limit
    assert results.count(False) == attempts - limit


@pytest.mark.asyncio
async def test_reserve_llm_spend_allows_up_to_cap_then_blocks(pool):
    await _insert_installation(pool, 405, "a")
    first = reserve_llm_spend(TEST_DATABASE_URL, 405, reserve_usd=0.5, monthly_cap=1.0)
    second = reserve_llm_spend(TEST_DATABASE_URL, 405, reserve_usd=0.5, monthly_cap=1.0)
    third = reserve_llm_spend(TEST_DATABASE_URL, 405, reserve_usd=0.5, monthly_cap=1.0)
    assert (first, second, third) == (True, True, False)
    assert get_llm_spend_this_month(TEST_DATABASE_URL, 405) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_release_llm_spend_reservation_gives_back_the_reserved_amount(pool):
    await _insert_installation(pool, 406, "a")
    reserve_llm_spend(TEST_DATABASE_URL, 406, reserve_usd=0.5, monthly_cap=1.0)
    release_llm_spend_reservation(TEST_DATABASE_URL, 406, reserve_usd=0.5)
    assert get_llm_spend_this_month(TEST_DATABASE_URL, 406) == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_release_llm_spend_reservation_never_goes_negative(pool):
    await _insert_installation(pool, 407, "a")
    release_llm_spend_reservation(TEST_DATABASE_URL, 407, reserve_usd=0.5)
    assert get_llm_spend_this_month(TEST_DATABASE_URL, 407) == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_reserve_llm_spend_is_atomic_under_real_concurrency(pool):
    import concurrent.futures

    await _insert_installation(pool, 408, "a")
    reserve_usd = 0.5
    cap = 5.0
    max_successes = 10  # cap / reserve_usd
    attempts = 30

    with concurrent.futures.ThreadPoolExecutor(max_workers=attempts) as pool_exec:
        results = list(
            pool_exec.map(
                lambda _: reserve_llm_spend(TEST_DATABASE_URL, 408, reserve_usd, cap),
                range(attempts),
            )
        )

    assert sum(results) == max_successes
    assert get_llm_spend_this_month(TEST_DATABASE_URL, 408) == pytest.approx(max_successes * reserve_usd)


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
                cur.execute("SELECT pg_try_advisory_lock(%s, %s)", (SPEND_LOCK_NAMESPACE, 301))
                acquired = cur.fetchone()[0]
        assert acquired is False


@pytest.mark.asyncio
async def test_installation_spend_lock_releases_after_context_exits(pool):
    import psycopg

    with installation_spend_lock(TEST_DATABASE_URL, 301):
        pass

    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s, %s)", (SPEND_LOCK_NAMESPACE, 301))
            acquired = cur.fetchone()[0]
            cur.execute("SELECT pg_advisory_unlock(%s, %s)", (SPEND_LOCK_NAMESPACE, 301))
    assert acquired is True


def test_installation_spend_lock_retries_transient_lock_not_available(monkeypatch):
    execute_calls = []
    sleep_calls = []
    lock_attempts = 0

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def execute(self, query, params):
            nonlocal lock_attempts
            execute_calls.append((query, params))
            if "pg_advisory_lock" in query:
                lock_attempts += 1
                if lock_attempts < 3:
                    raise scan_worker_db.psycopg.errors.LockNotAvailable("transient")

    class _Connection:
        def cursor(self):
            return _Cursor()

        def close(self):
            pass

    monkeypatch.setattr(scan_worker_db.psycopg, "connect", lambda *args, **kwargs: _Connection())
    monkeypatch.setattr(scan_worker_db.time, "sleep", sleep_calls.append)

    with scan_worker_db.installation_spend_lock("postgresql://unused", 301):
        pass

    assert lock_attempts == 3
    assert sleep_calls == [scan_worker_db.INSTALLATION_SPEND_LOCK_RETRY_DELAY_SECONDS] * 2
    assert sum("set_config" in query for query, _ in execute_calls) == 1
    assert sum("pg_advisory_unlock" in query for query, _ in execute_calls) == 1


def test_installation_spend_lock_reraises_after_retry_limit(monkeypatch):
    lock_attempts = 0
    sleep_calls = []

    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def execute(self, query, params):
            nonlocal lock_attempts
            if "pg_advisory_lock" in query:
                lock_attempts += 1
                raise scan_worker_db.psycopg.errors.LockNotAvailable("persistent")

    class _Connection:
        def cursor(self):
            return _Cursor()

        def close(self):
            pass

    monkeypatch.setattr(scan_worker_db.psycopg, "connect", lambda *args, **kwargs: _Connection())
    monkeypatch.setattr(scan_worker_db.time, "sleep", sleep_calls.append)

    with pytest.raises(scan_worker_db.psycopg.errors.LockNotAvailable, match="persistent"):
        with scan_worker_db.installation_spend_lock("postgresql://unused", 301):
            pass

    assert lock_attempts == scan_worker_db.INSTALLATION_SPEND_LOCK_MAX_ATTEMPTS
    assert sleep_calls == [scan_worker_db.INSTALLATION_SPEND_LOCK_RETRY_DELAY_SECONDS] * (
        scan_worker_db.INSTALLATION_SPEND_LOCK_MAX_ATTEMPTS - 1
    )


@pytest.mark.asyncio
async def test_repo_checkout_lock_blocks_concurrent_acquisition_for_same_repo(pool):
    import psycopg

    with repo_checkout_lock(TEST_DATABASE_URL, 301, "a/repo1"):
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_try_advisory_lock(%s, hashtext(%s))",
                    (REPO_CHECKOUT_LOCK_NAMESPACE, "301:a/repo1"),
                )
                acquired = cur.fetchone()[0]
        assert acquired is False


@pytest.mark.asyncio
async def test_repo_checkout_lock_releases_after_context_exits(pool):
    import psycopg

    with repo_checkout_lock(TEST_DATABASE_URL, 301, "a/repo1"):
        pass

    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_try_advisory_lock(%s, hashtext(%s))",
                (REPO_CHECKOUT_LOCK_NAMESPACE, "301:a/repo1"),
            )
            acquired = cur.fetchone()[0]
            cur.execute(
                "SELECT pg_advisory_unlock(%s, hashtext(%s))",
                (REPO_CHECKOUT_LOCK_NAMESPACE, "301:a/repo1"),
            )
    assert acquired is True


@pytest.mark.asyncio
async def test_repo_checkout_lock_does_not_block_a_different_repo(pool):
    import psycopg

    with repo_checkout_lock(TEST_DATABASE_URL, 301, "a/repo1"):
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_try_advisory_lock(%s, hashtext(%s))",
                    (REPO_CHECKOUT_LOCK_NAMESPACE, "301:a/repo2"),
                )
                acquired = cur.fetchone()[0]
                cur.execute(
                    "SELECT pg_advisory_unlock(%s, hashtext(%s))",
                    (REPO_CHECKOUT_LOCK_NAMESPACE, "301:a/repo2"),
                )
    assert acquired is True


@pytest.mark.asyncio
async def test_repo_checkout_lock_does_not_collide_with_spend_lock(pool):
    with installation_spend_lock(TEST_DATABASE_URL, 301):
        with repo_checkout_lock(TEST_DATABASE_URL, 301, "a/repo1"):
            pass


@pytest.mark.asyncio
async def test_scan_slot_lock_does_not_collide_with_spend_lock(pool):
    await _insert_installation(pool, 307, "a")
    with installation_spend_lock(TEST_DATABASE_URL, 307):
        assert check_and_reserve_monthly_repo_scan_slot(
            TEST_DATABASE_URL, 307, "a/repo", limit=1
        ) is True


@pytest.mark.asyncio
async def test_scan_slot_lock_still_serializes_same_purpose(pool):
    import psycopg
    from psycopg.errors import LockNotAvailable

    with psycopg.connect(TEST_DATABASE_URL, autocommit=False) as first:
        with first.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (SCAN_SLOT_LOCK_NAMESPACE, 308))
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as second:
            with second.cursor() as cur:
                cur.execute("SELECT set_config('lock_timeout', '100ms', false)")
                with pytest.raises(LockNotAvailable):
                    cur.execute(
                        "SELECT pg_advisory_xact_lock(%s, %s)",
                        (SCAN_SLOT_LOCK_NAMESPACE, 308),
                    )


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


@pytest.mark.asyncio
async def test_upsert_docs_symbol_overwrites_on_conflict(pool):
    await _insert_installation(pool, 301, "a")

    upsert_docs_symbol(TEST_DATABASE_URL, 301, "a/repo1", "a.py", "add", "First draft.", "generated", "sha1")
    upsert_docs_symbol(TEST_DATABASE_URL, 301, "a/repo1", "a.py", "add", "Second draft.", "generated", "sha2")

    symbols = list_docs_symbols(TEST_DATABASE_URL, 301, "a/repo1")
    assert len(symbols) == 1
    assert symbols[0]["description"] == "Second draft."
    assert symbols[0]["source_commit"] == "sha2"


@pytest.mark.asyncio
async def test_delete_docs_symbols_not_in_removes_stale_symbols_for_that_module(pool):
    await _insert_installation(pool, 301, "a")

    upsert_docs_symbol(TEST_DATABASE_URL, 301, "a/repo1", "a.py", "add", "d", "generated", "sha1")
    upsert_docs_symbol(TEST_DATABASE_URL, 301, "a/repo1", "a.py", "subtract", "d", "generated", "sha1")
    upsert_docs_symbol(TEST_DATABASE_URL, 301, "a/repo1", "b.py", "unrelated", "d", "generated", "sha1")

    delete_docs_symbols_not_in(TEST_DATABASE_URL, 301, "a/repo1", "a.py", ["add"])

    symbols = list_docs_symbols(TEST_DATABASE_URL, 301, "a/repo1")
    names = {(s["module_path"], s["symbol_name"]) for s in symbols}
    assert names == {("a.py", "add"), ("b.py", "unrelated")}


@pytest.mark.asyncio
async def test_get_docs_symbol_hashes_returns_only_hashed_rows_for_that_module(pool):
    await _insert_installation(pool, 301, "a")

    upsert_docs_symbol(TEST_DATABASE_URL, 301, "a/repo1", "a.py", "add", "d", "generated", "sha1", "hash-add")
    upsert_docs_symbol(TEST_DATABASE_URL, 301, "a/repo1", "a.py", "subtract", "d", "generated", "sha1", "hash-sub")
    # No content_hash passed - simulates a row written before the column existed.
    upsert_docs_symbol(TEST_DATABASE_URL, 301, "a/repo1", "a.py", "legacy", "d", "generated", "sha1")
    upsert_docs_symbol(TEST_DATABASE_URL, 301, "a/repo1", "b.py", "unrelated", "d", "generated", "sha1", "hash-other")

    hashes = get_docs_symbol_hashes(TEST_DATABASE_URL, 301, "a/repo1", "a.py")
    assert hashes == {"add": "hash-add", "subtract": "hash-sub"}


@pytest.mark.asyncio
async def test_upsert_docs_symbol_updates_content_hash_on_conflict(pool):
    await _insert_installation(pool, 301, "a")

    upsert_docs_symbol(TEST_DATABASE_URL, 301, "a/repo1", "a.py", "add", "d1", "generated", "sha1", "hash-v1")
    upsert_docs_symbol(TEST_DATABASE_URL, 301, "a/repo1", "a.py", "add", "d2", "generated", "sha2", "hash-v2")

    hashes = get_docs_symbol_hashes(TEST_DATABASE_URL, 301, "a/repo1", "a.py")
    assert hashes == {"add": "hash-v2"}


@pytest.mark.asyncio
async def test_docs_symbols_are_scoped_per_repo(pool):
    await _insert_installation(pool, 301, "a")

    upsert_docs_symbol(TEST_DATABASE_URL, 301, "a/repo1", "a.py", "add", "d1", "generated", "sha1")
    upsert_docs_symbol(TEST_DATABASE_URL, 301, "a/repo2", "a.py", "add", "d2", "generated", "sha1")

    repo1_symbols = list_docs_symbols(TEST_DATABASE_URL, 301, "a/repo1")
    assert len(repo1_symbols) == 1
    assert repo1_symbols[0]["description"] == "d1"


@pytest.mark.asyncio
async def test_docs_catchup_due_list_excludes_free_plan_repos(pool):
    await _insert_installation(pool, 501, "free-org", plan="free")
    insert_repo_history(TEST_DATABASE_URL, 501, "free-org/repo", datetime.now(timezone.utc), {"v": 1})

    due = list_paid_repos_due_for_docs_catchup(TEST_DATABASE_URL, 48 * 60 * 60)

    assert (501, "free-org/repo") not in due


@pytest.mark.asyncio
async def test_docs_catchup_due_list_includes_paid_repo_never_swept(pool):
    await _insert_installation(pool, 502, "paid-org", plan="indie")
    insert_repo_history(TEST_DATABASE_URL, 502, "paid-org/repo", datetime.now(timezone.utc), {"v": 1})

    due = list_paid_repos_due_for_docs_catchup(TEST_DATABASE_URL, 48 * 60 * 60)

    assert (502, "paid-org/repo") in due


@pytest.mark.asyncio
async def test_docs_catchup_due_list_excludes_paid_repo_with_no_scan_history(pool):
    await _insert_installation(pool, 503, "unscanned-org", plan="indie")

    due = list_paid_repos_due_for_docs_catchup(TEST_DATABASE_URL, 48 * 60 * 60)

    assert all(installation_id != 503 for installation_id, _ in due)


@pytest.mark.asyncio
async def test_docs_catchup_due_list_excludes_repo_swept_within_cooldown(pool):
    await _insert_installation(pool, 504, "recent-org", plan="indie")
    insert_repo_history(TEST_DATABASE_URL, 504, "recent-org/repo", datetime.now(timezone.utc), {"v": 1})
    record_docs_catchup_swept(TEST_DATABASE_URL, 504, "recent-org/repo")

    due = list_paid_repos_due_for_docs_catchup(TEST_DATABASE_URL, 48 * 60 * 60)

    assert (504, "recent-org/repo") not in due


@pytest.mark.asyncio
async def test_docs_catchup_due_list_excludes_repo_swept_long_ago_with_no_new_activity(pool):
    await _insert_installation(pool, 505, "stale-org", plan="indie")
    old_scan = datetime.now(timezone.utc) - timedelta(days=10)
    insert_repo_history(TEST_DATABASE_URL, 505, "stale-org/repo", old_scan, {"v": 1})
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO docs_catchup_sweeps (installation_id, repo_full_name, last_swept_at)
            VALUES ($1, $2, now() - interval '5 days')
            """,
            505,
            "stale-org/repo",
        )

    due = list_paid_repos_due_for_docs_catchup(TEST_DATABASE_URL, 48 * 60 * 60)

    assert (505, "stale-org/repo") not in due


@pytest.mark.asyncio
async def test_docs_catchup_due_list_includes_repo_swept_long_ago_with_new_activity_since(pool):
    await _insert_installation(pool, 506, "active-org", plan="indie")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO docs_catchup_sweeps (installation_id, repo_full_name, last_swept_at)
            VALUES ($1, $2, now() - interval '5 days')
            """,
            506,
            "active-org/repo",
        )
    insert_repo_history(TEST_DATABASE_URL, 506, "active-org/repo", datetime.now(timezone.utc), {"v": 1})

    due = list_paid_repos_due_for_docs_catchup(TEST_DATABASE_URL, 48 * 60 * 60)

    assert (506, "active-org/repo") in due


@pytest.mark.asyncio
async def test_record_docs_catchup_swept_upserts_on_conflict(pool):
    await _insert_installation(pool, 507, "upsert-org", plan="indie")
    insert_repo_history(TEST_DATABASE_URL, 507, "upsert-org/repo", datetime.now(timezone.utc), {"v": 1})

    record_docs_catchup_swept(TEST_DATABASE_URL, 507, "upsert-org/repo")
    first = await pool.fetchval(
        "SELECT last_swept_at FROM docs_catchup_sweeps WHERE installation_id = $1", 507
    )
    record_docs_catchup_swept(TEST_DATABASE_URL, 507, "upsert-org/repo")
    second = await pool.fetchval(
        "SELECT last_swept_at FROM docs_catchup_sweeps WHERE installation_id = $1", 507
    )

    count = await pool.fetchval(
        "SELECT count(*) FROM docs_catchup_sweeps WHERE installation_id = $1", 507
    )
    assert count == 1
    assert second >= first


@pytest.mark.asyncio
async def test_wiki_catchup_due_list_excludes_free_plan_repos(pool):
    await _insert_installation(pool, 518, "free-wiki-org", plan="free")
    insert_repo_history(TEST_DATABASE_URL, 518, "free-wiki-org/repo", datetime.now(timezone.utc), {"v": 1})

    due = list_paid_repos_due_for_wiki_catchup(TEST_DATABASE_URL, 48 * 60 * 60)

    assert (518, "free-wiki-org/repo") not in due


@pytest.mark.asyncio
async def test_wiki_catchup_due_list_includes_paid_repo_never_swept(pool):
    await _insert_installation(pool, 512, "paid-wiki-org", plan="indie")
    insert_repo_history(TEST_DATABASE_URL, 512, "paid-wiki-org/repo", datetime.now(timezone.utc), {"v": 1})

    due = list_paid_repos_due_for_wiki_catchup(TEST_DATABASE_URL, 48 * 60 * 60)

    assert (512, "paid-wiki-org/repo") in due


@pytest.mark.asyncio
async def test_wiki_catchup_due_list_excludes_paid_repo_with_no_scan_history(pool):
    await _insert_installation(pool, 513, "unscanned-wiki-org", plan="indie")

    due = list_paid_repos_due_for_wiki_catchup(TEST_DATABASE_URL, 48 * 60 * 60)

    assert all(installation_id != 513 for installation_id, _ in due)


@pytest.mark.asyncio
async def test_wiki_catchup_due_list_excludes_repo_swept_within_cooldown(pool):
    await _insert_installation(pool, 514, "recent-wiki-org", plan="indie")
    insert_repo_history(TEST_DATABASE_URL, 514, "recent-wiki-org/repo", datetime.now(timezone.utc), {"v": 1})
    record_wiki_catchup_swept(TEST_DATABASE_URL, 514, "recent-wiki-org/repo")

    due = list_paid_repos_due_for_wiki_catchup(TEST_DATABASE_URL, 48 * 60 * 60)

    assert (514, "recent-wiki-org/repo") not in due


@pytest.mark.asyncio
async def test_wiki_catchup_due_list_excludes_repo_swept_long_ago_with_no_new_activity(pool):
    await _insert_installation(pool, 515, "stale-wiki-org", plan="indie")
    old_scan = datetime.now(timezone.utc) - timedelta(days=10)
    insert_repo_history(TEST_DATABASE_URL, 515, "stale-wiki-org/repo", old_scan, {"v": 1})
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO wiki_catchup_sweeps (installation_id, repo_full_name, last_swept_at)
            VALUES ($1, $2, now() - interval '5 days')
            """,
            515,
            "stale-wiki-org/repo",
        )

    due = list_paid_repos_due_for_wiki_catchup(TEST_DATABASE_URL, 48 * 60 * 60)

    assert (515, "stale-wiki-org/repo") not in due


@pytest.mark.asyncio
async def test_wiki_catchup_due_list_includes_repo_swept_long_ago_with_new_activity_since(pool):
    await _insert_installation(pool, 516, "active-wiki-org", plan="indie")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO wiki_catchup_sweeps (installation_id, repo_full_name, last_swept_at)
            VALUES ($1, $2, now() - interval '5 days')
            """,
            516,
            "active-wiki-org/repo",
        )
    insert_repo_history(TEST_DATABASE_URL, 516, "active-wiki-org/repo", datetime.now(timezone.utc), {"v": 1})

    due = list_paid_repos_due_for_wiki_catchup(TEST_DATABASE_URL, 48 * 60 * 60)

    assert (516, "active-wiki-org/repo") in due


@pytest.mark.asyncio
async def test_record_wiki_catchup_swept_upserts_on_conflict(pool):
    await _insert_installation(pool, 517, "upsert-wiki-org", plan="indie")
    insert_repo_history(TEST_DATABASE_URL, 517, "upsert-wiki-org/repo", datetime.now(timezone.utc), {"v": 1})

    record_wiki_catchup_swept(TEST_DATABASE_URL, 517, "upsert-wiki-org/repo")
    first = await pool.fetchval(
        "SELECT last_swept_at FROM wiki_catchup_sweeps WHERE installation_id = $1", 517
    )
    record_wiki_catchup_swept(TEST_DATABASE_URL, 517, "upsert-wiki-org/repo")
    second = await pool.fetchval(
        "SELECT last_swept_at FROM wiki_catchup_sweeps WHERE installation_id = $1", 517
    )

    count = await pool.fetchval(
        "SELECT count(*) FROM wiki_catchup_sweeps WHERE installation_id = $1", 517
    )
    assert count == 1
    assert second >= first


@pytest.mark.asyncio
async def test_email_already_sent_is_false_until_recorded(pool):
    await _insert_installation(pool, 601, "f")

    assert email_already_sent(TEST_DATABASE_URL, "welcome:octocat") is False

    record_sent_email(TEST_DATABASE_URL, "welcome:octocat", "welcome", "o@example.com", 601, "msg_1")

    assert email_already_sent(TEST_DATABASE_URL, "welcome:octocat") is True


@pytest.mark.asyncio
async def test_record_sent_email_ignores_duplicate_dedupe_key(pool):
    await _insert_installation(pool, 602, "g")

    record_sent_email(TEST_DATABASE_URL, "payment_failed:evt_1:a@x.com", "payment_failed", "a@x.com", 602, "msg_1")
    # Same dedupe_key again - must not raise (ON CONFLICT DO NOTHING), and
    # must not create a second row.
    record_sent_email(TEST_DATABASE_URL, "payment_failed:evt_1:a@x.com", "payment_failed", "a@x.com", 602, "msg_2")

    count = await pool.fetchval(
        "SELECT count(*) FROM sent_emails WHERE dedupe_key = $1", "payment_failed:evt_1:a@x.com"
    )
    assert count == 1


@pytest.mark.asyncio
async def test_list_paid_installations_due_for_digest_excludes_free_plan(pool):
    await _insert_installation(pool, 800, "paid-co", plan="indie")
    await _insert_installation(pool, 801, "free-co", plan="free")

    due = list_paid_installations_due_for_digest(TEST_DATABASE_URL, interval_seconds=7 * 86400)

    assert 800 in due
    assert 801 not in due


@pytest.mark.asyncio
async def test_list_paid_installations_due_for_digest_excludes_recently_sent(pool):
    await _insert_installation(pool, 802, "paid-co", plan="indie")
    record_digest_sent(TEST_DATABASE_URL, 802)

    due = list_paid_installations_due_for_digest(TEST_DATABASE_URL, interval_seconds=7 * 86400)

    assert 802 not in due


@pytest.mark.asyncio
async def test_list_paid_installations_due_for_digest_includes_installation_never_sent_to(pool):
    await _insert_installation(pool, 803, "paid-co", plan="indie")

    due = list_paid_installations_due_for_digest(TEST_DATABASE_URL, interval_seconds=7 * 86400)

    assert 803 in due


@pytest.mark.asyncio
async def test_list_paid_installations_due_for_digest_includes_after_cooldown_elapses(pool):
    await _insert_installation(pool, 804, "paid-co", plan="indie")
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO digest_sends (installation_id, last_sent_at) VALUES ($1, now() - interval '8 days')",
            804,
        )

    due = list_paid_installations_due_for_digest(TEST_DATABASE_URL, interval_seconds=7 * 86400)

    assert 804 in due


@pytest.mark.asyncio
async def test_count_repo_scans_since_only_counts_recent_scans(pool):
    await _insert_installation(pool, 805, "co")
    insert_repo_history(
        TEST_DATABASE_URL, 805, "co/repo", datetime.now(timezone.utc) - timedelta(days=10), {}
    )
    insert_repo_history(TEST_DATABASE_URL, 805, "co/repo", datetime.now(timezone.utc), {})

    count = count_repo_scans_since(TEST_DATABASE_URL, 805, datetime.now(timezone.utc) - timedelta(days=7))

    assert count == 1


@pytest.mark.asyncio
async def test_get_endpoint_health_summary_counts_reachable_and_total(pool):
    await _insert_installation(pool, 806, "co")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO endpoint_health
                (installation_id, repo_full_name, endpoint_method, endpoint_path, reachable, status_code, latency_ms)
            VALUES
                (806, 'co/repo', 'GET', '/a', true, 200, 5.0),
                (806, 'co/repo', 'GET', '/b', false, NULL, NULL)
            """
        )

    summary = get_endpoint_health_summary(TEST_DATABASE_URL, 806)

    assert summary == {"total": 2, "reachable": 1}


@pytest.mark.asyncio
async def test_get_endpoint_health_summary_excludes_stale_rows(pool):
    await _insert_installation(pool, 807, "co")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO endpoint_health
                (installation_id, repo_full_name, endpoint_method, endpoint_path, reachable, status_code, checked_at)
            VALUES
                (807, 'co/repo', 'GET', '/a', true, 200, now() - interval '1 hour')
            """
        )

    summary = get_endpoint_health_summary(TEST_DATABASE_URL, 807, stale_after_seconds=900)

    assert summary == {"total": 0, "reachable": 0}


@pytest.mark.asyncio
async def test_list_installation_member_emails_sync_matches_async_semantics(pool):
    await _insert_installation(pool, 808, "co")
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO installation_members (installation_id, github_login, added_by_github_login) "
            "VALUES (808, 'alice', 'alice'), (808, 'bob', 'alice')"
        )
        await conn.execute(
            "INSERT INTO github_user_emails (github_login, email) VALUES ('alice', 'alice@example.com')"
        )
        # bob was added by username but has never logged in - no email on
        # file, deliberately excluded.

    emails = list_installation_member_emails(TEST_DATABASE_URL, 808)

    assert emails == ["alice@example.com"]


@pytest.mark.asyncio
async def test_get_dismissed_identity_keys_sync_matches_async_writer(pool):
    from app_server.dismissed_findings import dismiss_finding, finding_identity_key

    await _insert_installation(pool, 809, "co")
    secret_finding = {"path": "config.py", "pattern": "aws_access_key_id", "match_preview": "AKIA****...MNOP"}
    await dismiss_finding(pool, 809, "co/repo", "secret", secret_finding, "octocat")

    dismissed = get_dismissed_identity_keys(TEST_DATABASE_URL, 809, "co/repo")

    assert finding_identity_key("secret", secret_finding) in dismissed["secret"]
    assert dismissed["vulnerability"] == set()


@pytest.mark.asyncio
async def test_get_dismissed_identity_keys_sync_returns_empty_sets_when_none_dismissed(pool):
    await _insert_installation(pool, 810, "co")

    dismissed = get_dismissed_identity_keys(TEST_DATABASE_URL, 810, "co/repo")

    assert dismissed == {"secret": set(), "vulnerability": set()}


@pytest.mark.asyncio
async def test_get_seconds_since_last_health_check_returns_none_when_no_rows(pool):
    result = get_seconds_since_last_health_check(TEST_DATABASE_URL)

    assert result is None


@pytest.mark.asyncio
async def test_get_seconds_since_last_health_check_reports_elapsed_time(pool):
    await _insert_installation(pool, 811, "co")
    checked_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO endpoint_health "
            "(installation_id, repo_full_name, endpoint_method, endpoint_path, reachable, checked_at) "
            "VALUES (811, 'co/repo', 'GET', '/healthz', true, $1)",
            checked_at,
        )

    result = get_seconds_since_last_health_check(TEST_DATABASE_URL)

    assert result is not None
    # ~300s elapsed - generous bounds so this isn't flaky on a slow CI runner.
    assert 290 <= result <= 320


@pytest.mark.asyncio
async def test_get_seconds_since_last_health_check_uses_the_most_recent_row(pool):
    await _insert_installation(pool, 812, "co")
    old_check = datetime.now(timezone.utc) - timedelta(hours=1)
    recent_check = datetime.now(timezone.utc) - timedelta(seconds=5)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO endpoint_health "
            "(installation_id, repo_full_name, endpoint_method, endpoint_path, reachable, checked_at) "
            "VALUES (812, 'co/repo-a', 'GET', '/a', true, $1), "
            "(812, 'co/repo-b', 'GET', '/b', true, $2)",
            old_check,
            recent_check,
        )

    result = get_seconds_since_last_health_check(TEST_DATABASE_URL)

    assert result is not None
    assert result < 60
