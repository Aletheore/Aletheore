import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app_server.evidence_limits import EvidenceTooLargeError, MAX_EVIDENCE_BYTES
from app_server.db import (
    add_installation_member_within_seat_limit,
    add_health_check_target,
    add_health_check_target_within_limit,
    add_installation_member,
    check_and_reserve_managed_audit,
    count_active_tokens,
    count_health_check_targets,
    count_installation_members,
    create_api_token,
    create_api_token_within_limit,
    create_session,
    delete_installation,
    delete_session,
    get_extra_seats,
    get_installation_by_token_hash,
    get_installation,
    get_llm_spend_this_month,
    get_recent_history,
    get_max_tokens,
    get_session,
    insert_repo_history,
    is_installation_member,
    list_api_tokens,
    list_health_check_targets,
    list_installation_member_emails,
    list_installation_members,
    remove_health_check_target,
    remove_installation_member,
    revoke_api_token,
    record_llm_spend,
    set_installation_plan,
    set_webhook_url,
    touch_api_token,
    upsert_github_user_email,
    upsert_installation,
)


@pytest.mark.asyncio
async def test_upsert_installation_creates_row(pool):
    await upsert_installation(pool, 123, "octocat")
    row = await get_installation(pool, 123)
    assert row["account_login"] == "octocat"
    assert row["plan"] == "free"


@pytest.mark.asyncio
async def test_upsert_installation_is_idempotent(pool):
    await upsert_installation(pool, 123, "octocat")
    await upsert_installation(pool, 123, "octocat")
    row = await get_installation(pool, 123)
    assert row["account_login"] == "octocat"


@pytest.mark.asyncio
async def test_set_installation_plan_updates_plan(pool):
    await upsert_installation(pool, 123, "octocat")
    await set_installation_plan(pool, 123, "indie")
    row = await get_installation(pool, 123)
    assert row["plan"] == "indie"


@pytest.mark.asyncio
async def test_delete_installation_removes_row(pool):
    await upsert_installation(pool, 123, "octocat")
    await delete_installation(pool, 123)
    assert await get_installation(pool, 123) is None


@pytest.mark.asyncio
async def test_delete_installation_cascades_to_history(pool):
    await upsert_installation(pool, 123, "octocat")
    await insert_repo_history(pool, 123, "octocat/repo", datetime.now(timezone.utc), {"x": 1})
    await delete_installation(pool, 123)
    assert await get_recent_history(pool, 123, "octocat/repo") == []


@pytest.mark.asyncio
async def test_insert_repo_history_rejects_oversized_evidence(pool):
    await upsert_installation(pool, 123, "octocat")
    oversized = {"padding": "x" * (MAX_EVIDENCE_BYTES + 1)}
    with pytest.raises(EvidenceTooLargeError):
        await insert_repo_history(pool, 123, "octocat/repo", datetime.now(timezone.utc), oversized)
    assert await get_recent_history(pool, 123, "octocat/repo") == []


@pytest.mark.asyncio
async def test_repo_history_rotation_keeps_only_20(pool):
    await upsert_installation(pool, 123, "octocat")
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(21):
        await insert_repo_history(pool, 123, "octocat/repo", start + timedelta(minutes=i), {"n": i})

    history = await get_recent_history(pool, 123, "octocat/repo", limit=100)
    assert len(history) == 20
    assert history[0]["evidence"]["n"] == 20
    assert history[-1]["evidence"]["n"] == 1


@pytest.mark.asyncio
async def test_session_lifecycle(pool):
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    await create_session(pool, "sess-1", 42, "octocat", "encrypted", expires)
    row = await get_session(pool, "sess-1")
    assert row["github_login"] == "octocat"
    await delete_session(pool, "sess-1")
    assert await get_session(pool, "sess-1") is None


@pytest.mark.asyncio
async def test_webhook_url_and_token_lifecycle(pool):
    await upsert_installation(pool, 100, "octocat")
    await set_webhook_url(pool, 100, "https://hooks.slack.com/services/x")
    installation = await get_installation(pool, 100)
    assert installation["webhook_url"] == "https://hooks.slack.com/services/x"
    assert await get_max_tokens(pool, 100) == 3

    await create_api_token(pool, 100, "hash1", "laptop", "octocat")
    assert await count_active_tokens(pool, 100) == 1
    assert (await get_installation_by_token_hash(pool, "hash1"))["installation_id"] == 100
    await touch_api_token(pool, "hash1")
    tokens = await list_api_tokens(pool, 100)
    assert tokens[0]["last_used_at"] is not None
    assert "token_hash" not in tokens[0]
    await revoke_api_token(pool, 100, tokens[0]["id"])
    assert await count_active_tokens(pool, 100) == 0
    assert await get_installation_by_token_hash(pool, "hash1") is None




@pytest.mark.asyncio
async def test_check_and_reserve_managed_audit_allows_first_run(pool):
    await upsert_installation(pool, 400, "octocat")
    allowed = await check_and_reserve_managed_audit(pool, 400, "octocat/widgets", cooldown_seconds=3600)
    assert allowed is True


@pytest.mark.asyncio
async def test_check_and_reserve_managed_audit_blocks_second_run_within_cooldown(pool):
    await upsert_installation(pool, 400, "octocat")
    assert await check_and_reserve_managed_audit(pool, 400, "octocat/widgets", cooldown_seconds=3600) is True
    assert await check_and_reserve_managed_audit(pool, 400, "octocat/widgets", cooldown_seconds=3600) is False


@pytest.mark.asyncio
async def test_check_and_reserve_managed_audit_allows_after_cooldown_elapses(pool):
    await upsert_installation(pool, 400, "octocat")
    old_run = datetime.now(timezone.utc) - timedelta(hours=2)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO managed_audit_rate_limits (installation_id, repo_full_name, last_run_at)
            VALUES ($1, $2, $3)
            """,
            400,
            "octocat/widgets",
            old_run,
        )
    allowed = await check_and_reserve_managed_audit(pool, 400, "octocat/widgets", cooldown_seconds=3600)
    assert allowed is True


@pytest.mark.asyncio
async def test_check_and_reserve_managed_audit_is_independent_per_repo(pool):
    await upsert_installation(pool, 400, "octocat")
    assert await check_and_reserve_managed_audit(pool, 400, "octocat/widgets", cooldown_seconds=3600) is True
    assert await check_and_reserve_managed_audit(pool, 400, "octocat/gizmos", cooldown_seconds=3600) is True


@pytest.mark.asyncio
async def test_get_llm_spend_this_month_returns_zero_when_no_rows(pool):
    await upsert_installation(pool, 500, "octocat")
    assert await get_llm_spend_this_month(pool, 500) == 0.0


@pytest.mark.asyncio
async def test_record_llm_spend_accumulates_within_the_same_month(pool):
    await upsert_installation(pool, 500, "octocat")
    await record_llm_spend(pool, 500, 0.05)
    await record_llm_spend(pool, 500, 0.03)
    assert await get_llm_spend_this_month(pool, 500) == pytest.approx(0.08)


@pytest.mark.asyncio
async def test_record_llm_spend_is_independent_per_installation(pool):
    await upsert_installation(pool, 500, "octocat")
    await upsert_installation(pool, 501, "acme")
    await record_llm_spend(pool, 500, 1.00)
    await record_llm_spend(pool, 501, 2.00)
    assert await get_llm_spend_this_month(pool, 500) == pytest.approx(1.00)
    assert await get_llm_spend_this_month(pool, 501) == pytest.approx(2.00)


@pytest.mark.asyncio
async def test_record_llm_spend_without_monthly_cap_never_warns(pool, caplog):
    """Existing callers that predate the cap-warning parameter must keep
    working unchanged - omitting monthly_cap skips the check entirely."""
    await upsert_installation(pool, 500, "octocat")
    with caplog.at_level("WARNING"):
        await record_llm_spend(pool, 500, 10.00)
    assert caplog.records == []


@pytest.mark.asyncio
async def test_record_llm_spend_warns_once_when_crossing_the_threshold(pool, caplog):
    await upsert_installation(pool, 500, "octocat")
    with caplog.at_level("WARNING"):
        await record_llm_spend(pool, 500, 2.00, monthly_cap=15.00)  # under 30% ($4.50)
        await record_llm_spend(pool, 500, 5.00, monthly_cap=15.00)  # $7 total - crosses it
        await record_llm_spend(pool, 500, 1.00, monthly_cap=15.00)  # already over - no refire

    warnings = [r for r in caplog.records if "installation=500" in r.message]
    assert len(warnings) == 1
    assert "30%" in warnings[0].message


@pytest.mark.asyncio
async def test_get_extra_seats_defaults_to_zero(pool):
    await upsert_installation(pool, 500, "octocat")
    assert await get_extra_seats(pool, 500) == 0


@pytest.mark.asyncio
async def test_get_extra_seats_reads_the_real_column(pool):
    await upsert_installation(pool, 500, "octocat")
    async with pool.acquire() as conn:
        await conn.execute("UPDATE installations SET extra_seats = 3 WHERE installation_id = $1", 500)
    assert await get_extra_seats(pool, 500) == 3


@pytest.mark.asyncio
async def test_installation_member_lifecycle(pool):
    await upsert_installation(pool, 600, "octocat")
    assert await is_installation_member(pool, 600, "octocat") is False

    await add_installation_member(pool, 600, "octocat", "octocat")
    assert await is_installation_member(pool, 600, "octocat") is True
    assert await count_installation_members(pool, 600) == 1

    members = await list_installation_members(pool, 600)
    assert members[0]["github_login"] == "octocat"
    assert members[0]["added_by_github_login"] == "octocat"

    await remove_installation_member(pool, 600, "octocat")
    assert await is_installation_member(pool, 600, "octocat") is False
    assert await count_installation_members(pool, 600) == 0


@pytest.mark.asyncio
async def test_add_installation_member_is_idempotent(pool):
    await upsert_installation(pool, 600, "octocat")
    await add_installation_member(pool, 600, "octocat", "octocat")
    await add_installation_member(pool, 600, "octocat", "octocat")
    assert await count_installation_members(pool, 600) == 1


@pytest.mark.asyncio
async def test_add_installation_member_within_seat_limit_is_concurrency_safe(pool):
    await upsert_installation(pool, 610, "octocat")
    await add_installation_member(pool, 610, "octocat", "octocat")

    results = await asyncio.gather(
        *(
            add_installation_member_within_seat_limit(
                pool, 610, f"member-{index}", "octocat", seat_limit=2
            )
            for index in range(10)
        )
    )

    assert sum(inserted for allowed, inserted in results) == 1
    assert sum(allowed for allowed, inserted in results) == 1
    assert await count_installation_members(pool, 610) == 2


@pytest.mark.asyncio
async def test_concurrent_api_token_creation_returns_each_inserted_id(pool):
    await upsert_installation(pool, 611, "octocat")
    results = await asyncio.gather(
        *(
            create_api_token(pool, 611, f"hash-{index}", f"token-{index}", "octocat")
            for index in range(10)
        )
    )

    assert len(set(results)) == 10
    rows = await pool.fetch(
        "SELECT id, token_hash FROM api_tokens WHERE installation_id = $1",
        611,
    )
    assert {row["id"] for row in rows} == set(results)
    assert {row["token_hash"] for row in rows} == {f"hash-{index}" for index in range(10)}


@pytest.mark.asyncio
async def test_create_api_token_within_limit_is_concurrency_safe(pool):
    # Real bug this guards (Claude_Audit.md finding 24): the route-level
    # count-then-create_api_token sequence let two concurrent requests both
    # read the same under-limit count before either insert committed,
    # letting an installation end up over its plan's token limit. Mirrors
    # test_add_installation_member_within_seat_limit_is_concurrency_safe's
    # shape - 10 concurrent callers, limit 2, exactly 2 must win.
    await upsert_installation(pool, 612, "octocat")

    results = await asyncio.gather(
        *(
            create_api_token_within_limit(pool, 612, f"hash-{index}", f"token-{index}", "octocat", limit=2)
            for index in range(10)
        )
    )

    assert sum(1 for token_id in results if token_id is not None) == 2
    rows = await pool.fetch("SELECT id FROM api_tokens WHERE installation_id = $1", 612)
    assert len(rows) == 2
    assert {row["id"] for row in rows} == {token_id for token_id in results if token_id is not None}


@pytest.mark.asyncio
async def test_add_health_check_target_within_limit_is_concurrency_safe(pool):
    # Same fix, same reasoning as test_create_api_token_within_limit_is_concurrency_safe,
    # the health-check-target quota.
    await upsert_installation(pool, 613, "octocat")

    results = await asyncio.gather(
        *(
            add_health_check_target_within_limit(
                pool, 613, "octocat/repo1", f"target-{index}", f"https://{index}.example.com", None, limit=2
            )
            for index in range(10)
        )
    )

    assert sum(1 for target_id in results if target_id is not None) == 2
    assert await count_health_check_targets(pool, 613, "octocat/repo1") == 2


@pytest.mark.asyncio
async def test_add_health_check_target_within_limit_allows_updating_an_existing_target_over_the_limit(pool):
    # An existing target (same installation/repo/base_url) must always be
    # updatable - only a genuinely new insert counts against the limit,
    # matching add_health_check_target's existing upsert semantics.
    await upsert_installation(pool, 614, "octocat")
    first_id = await add_health_check_target_within_limit(
        pool, 614, "octocat/repo1", "Primary", "https://a.example.com", None, limit=1
    )
    assert first_id is not None

    updated_id = await add_health_check_target_within_limit(
        pool, 614, "octocat/repo1", "Primary (renamed)", "https://a.example.com", 500, limit=1
    )

    assert updated_id == first_id
    row = await pool.fetchrow("SELECT label, latency_threshold_ms FROM health_check_targets WHERE id = $1", first_id)
    assert row["label"] == "Primary (renamed)"
    assert row["latency_threshold_ms"] == 500


@pytest.mark.asyncio
async def test_installation_members_are_independent_per_installation(pool):
    await upsert_installation(pool, 600, "octocat")
    await upsert_installation(pool, 601, "acme")
    await add_installation_member(pool, 600, "alice", "octocat")
    await add_installation_member(pool, 601, "bob", "acme")
    assert await is_installation_member(pool, 600, "bob") is False
    assert await is_installation_member(pool, 601, "alice") is False


@pytest.mark.asyncio
async def test_removing_installation_cascades_to_members(pool):
    await upsert_installation(pool, 600, "octocat")
    await add_installation_member(pool, 600, "alice", "octocat")
    await delete_installation(pool, 600)
    await upsert_installation(pool, 600, "octocat")
    assert await count_installation_members(pool, 600) == 0


@pytest.mark.asyncio
async def test_health_check_target_lifecycle(pool):
    await upsert_installation(pool, 700, "octocat")
    assert await count_health_check_targets(pool, 700, "octocat/repo1") == 0

    target_id = await add_health_check_target(pool, 700, "octocat/repo1", "Staging", "https://staging.example.com", 2000)
    assert await count_health_check_targets(pool, 700, "octocat/repo1") == 1

    targets = await list_health_check_targets(pool, 700, "octocat/repo1")
    assert targets[0]["id"] == target_id
    assert targets[0]["label"] == "Staging"
    assert targets[0]["base_url"] == "https://staging.example.com"
    assert targets[0]["latency_threshold_ms"] == 2000

    await remove_health_check_target(pool, 700, "octocat/repo1", target_id)
    assert await count_health_check_targets(pool, 700, "octocat/repo1") == 0


@pytest.mark.asyncio
async def test_health_check_targets_are_independent_per_repo(pool):
    await upsert_installation(pool, 700, "octocat")
    await add_health_check_target(pool, 700, "octocat/repo1", "Primary", "https://a.example.com", None)
    await add_health_check_target(pool, 700, "octocat/repo2", "Primary", "https://b.example.com", None)
    assert await count_health_check_targets(pool, 700, "octocat/repo1") == 1
    assert await count_health_check_targets(pool, 700, "octocat/repo2") == 1


@pytest.mark.asyncio
async def test_add_health_check_target_upserts_on_duplicate_url(pool):
    await upsert_installation(pool, 700, "octocat")
    await add_health_check_target(pool, 700, "octocat/repo1", "First label", "https://a.example.com", None)
    await add_health_check_target(pool, 700, "octocat/repo1", "Updated label", "https://a.example.com", 500)

    targets = await list_health_check_targets(pool, 700, "octocat/repo1")
    assert len(targets) == 1
    assert targets[0]["label"] == "Updated label"
    assert targets[0]["latency_threshold_ms"] == 500


@pytest.mark.asyncio
async def test_remove_health_check_target_is_scoped_to_installation_and_repo(pool):
    await upsert_installation(pool, 700, "octocat")
    await upsert_installation(pool, 701, "acme")
    target_id = await add_health_check_target(pool, 700, "octocat/repo1", "Primary", "https://a.example.com", None)

    # A different installation cannot delete someone else's target by id.
    await remove_health_check_target(pool, 701, "octocat/repo1", target_id)
    assert await count_health_check_targets(pool, 700, "octocat/repo1") == 1


@pytest.mark.asyncio
async def test_upsert_github_user_email_returns_true_only_on_first_capture(pool):
    is_new_first = await upsert_github_user_email(pool, "octocat", "octocat@example.com")
    is_new_second = await upsert_github_user_email(pool, "octocat", "octocat@example.com")

    assert is_new_first is True
    assert is_new_second is False


@pytest.mark.asyncio
async def test_upsert_github_user_email_self_heals_on_email_change(pool):
    await upsert_github_user_email(pool, "octocat", "old@example.com")
    await upsert_github_user_email(pool, "octocat", "new@example.com")

    row = await pool.fetchrow(
        "SELECT email FROM github_user_emails WHERE github_login = $1", "octocat"
    )
    assert row["email"] == "new@example.com"


@pytest.mark.asyncio
async def test_list_installation_member_emails_only_includes_members_who_have_logged_in(pool):
    await upsert_installation(pool, 700, "acme")
    await add_installation_member(pool, 700, "alice", "alice")
    await add_installation_member(pool, 700, "bob", "alice")
    # bob was added by username but has never logged in, so has no
    # captured email - this is deliberate v1 scope, not a bug.
    await upsert_github_user_email(pool, "alice", "alice@example.com")

    emails = await list_installation_member_emails(pool, 700)

    assert emails == ["alice@example.com"]
