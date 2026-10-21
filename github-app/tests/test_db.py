import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from aletheore.evidence import EVIDENCE_VERSION
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
    create_personal_token_within_limit,
    create_session,
    delete_installation,
    delete_session,
    get_extra_seats,
    get_installation_by_token_hash,
    get_installation,
    get_latest_evidence,
    get_llm_spend_this_month,
    get_or_create_personal_identity,
    get_personal_identity_by_token_hash,
    get_recent_history,
    get_max_tokens,
    get_session,
    insert_repo_history,
    is_installation_member,
    reconcile_installation_member_identity,
    list_api_tokens,
    list_health_check_targets,
    list_installation_member_emails,
    list_installation_members,
    remove_health_check_target,
    remove_installation_member,
    revoke_all_personal_tokens,
    revoke_api_token,
    revoke_api_tokens_created_by,
    revoke_personal_token,
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
        await insert_repo_history(
            pool, 123, "octocat/repo", start + timedelta(minutes=i),
            {"aletheore_version": EVIDENCE_VERSION, "n": i},
        )

    history = await get_recent_history(pool, 123, "octocat/repo", limit=100)
    assert len(history) == 20
    assert history[0]["evidence"]["n"] == 20
    assert history[-1]["evidence"]["n"] == 1


@pytest.mark.asyncio
async def test_get_latest_evidence_returns_none_for_an_incompatible_version(pool):
    # Real bug this guards: the hosted dashboard's own evidence-read path
    # (this async get_latest_evidence, distinct from scan_worker.db's
    # already-guarded sync version) never checked aletheore_version at
    # all - a repo_history row written by an older, schema-incompatible
    # build would surface as a raw, unhandled exception deep in dashboard
    # rendering instead of a clean "awaiting re-scan".
    await upsert_installation(pool, 123, "octocat")
    await insert_repo_history(
        pool, 123, "octocat/repo", datetime.now(timezone.utc), {"aletheore_version": "0.1.0", "n": 1}
    )
    assert await get_latest_evidence(pool, 123, "octocat/repo") is None


@pytest.mark.asyncio
async def test_get_latest_evidence_returns_the_evidence_for_a_compatible_version(pool):
    await upsert_installation(pool, 123, "octocat")
    await insert_repo_history(
        pool, 123, "octocat/repo", datetime.now(timezone.utc),
        {"aletheore_version": EVIDENCE_VERSION, "n": 1},
    )
    evidence = await get_latest_evidence(pool, 123, "octocat/repo")
    assert evidence["n"] == 1


@pytest.mark.asyncio
async def test_get_recent_history_drops_rows_with_an_incompatible_version(pool):
    # A version-incompatible row is dropped from the list entirely, not
    # included with evidence=None - a history entry whose evidence dict
    # is missing keys the frontend expects to render would fail there
    # instead of here, on a row the next scan will overwrite anyway.
    await upsert_installation(pool, 123, "octocat")
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await insert_repo_history(
        pool, 123, "octocat/repo", start, {"aletheore_version": "0.1.0", "n": "old-shape"}
    )
    await insert_repo_history(
        pool, 123, "octocat/repo", start + timedelta(minutes=1),
        {"aletheore_version": EVIDENCE_VERSION, "n": "current-shape"},
    )
    history = await get_recent_history(pool, 123, "octocat/repo", limit=100)
    assert len(history) == 1
    assert history[0]["evidence"]["n"] == "current-shape"


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
async def test_revoke_api_tokens_created_by_only_revokes_that_creators_tokens(pool):
    # Regression test for a real bug (2026-10-12 Strix security review):
    # a token authenticates purely by hash + revoked_at IS NULL, with
    # nothing tying its continued validity to its creator still being an
    # installation member - removing someone used to leave every token
    # they'd personally created fully usable indefinitely. This is the DB
    # primitive remove_member now calls to close that.
    await upsert_installation(pool, 101, "octocat")
    await create_api_token(pool, 101, "alice-hash-1", "alice-laptop", "alice")
    await create_api_token(pool, 101, "alice-hash-2", "alice-ci", "alice")
    await create_api_token(pool, 101, "bob-hash-1", "bob-laptop", "bob")

    revoked_count = await revoke_api_tokens_created_by(pool, 101, "alice")

    assert revoked_count == 2
    assert await get_installation_by_token_hash(pool, "alice-hash-1") is None
    assert await get_installation_by_token_hash(pool, "alice-hash-2") is None
    # Bob was not removed - his own token must keep working.
    assert (await get_installation_by_token_hash(pool, "bob-hash-1"))["installation_id"] == 101


@pytest.mark.asyncio
async def test_revoke_api_tokens_created_by_is_a_noop_for_a_login_with_no_tokens(pool):
    await upsert_installation(pool, 102, "octocat")
    revoked_count = await revoke_api_tokens_created_by(pool, 102, "nobody")
    assert revoked_count == 0


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
async def test_reconcile_installation_member_identity_binds_an_unbound_row(pool):
    # A row granted before this reconciliation existed (or before this
    # login's holder ever logged in) has no github_user_id yet - the first
    # login under that login string is trusted to bind it, since a login
    # string is the only identity admin.py's add_member had to begin with.
    await upsert_installation(pool, 700, "octocat")
    await add_installation_member(pool, 700, "octocat", "octocat")

    await reconcile_installation_member_identity(pool, "octocat", 99001)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT github_user_id FROM installation_members WHERE installation_id = $1 AND github_login = $2",
            700,
            "octocat",
        )
    assert row["github_user_id"] == 99001
    # Still a member - binding must not evict the legitimate first holder.
    assert await is_installation_member(pool, 700, "octocat") is True


@pytest.mark.asyncio
async def test_reconcile_installation_member_identity_evicts_a_reassigned_login(pool):
    # Real gap this closes: "octocat" was already bound to user 99001 (the
    # original member). The login is later abandoned and claimed by a
    # different real GitHub account (99002) - that new account logging in
    # must not inherit the original member's seat just by sharing the login
    # string.
    await upsert_installation(pool, 701, "octocat")
    await add_installation_member(pool, 701, "octocat", "octocat")
    await reconcile_installation_member_identity(pool, "octocat", 99001)
    assert await is_installation_member(pool, 701, "octocat") is True

    await reconcile_installation_member_identity(pool, "octocat", 99002)

    assert await is_installation_member(pool, 701, "octocat") is False


@pytest.mark.asyncio
async def test_reconcile_installation_member_identity_is_a_noop_for_the_same_user(pool):
    await upsert_installation(pool, 702, "octocat")
    await add_installation_member(pool, 702, "octocat", "octocat")
    await reconcile_installation_member_identity(pool, "octocat", 99001)

    # The original member logging in again, repeatedly, must not evict
    # their own seat.
    await reconcile_installation_member_identity(pool, "octocat", 99001)
    await reconcile_installation_member_identity(pool, "octocat", 99001)

    assert await is_installation_member(pool, 702, "octocat") is True


@pytest.mark.asyncio
async def test_reconcile_installation_member_identity_only_touches_the_matching_login(pool):
    await upsert_installation(pool, 703, "octocat")
    await add_installation_member(pool, 703, "octocat", "octocat")
    await add_installation_member(pool, 703, "otheruser", "octocat")
    await reconcile_installation_member_identity(pool, "octocat", 99001)
    await reconcile_installation_member_identity(pool, "otheruser", 99003)

    await reconcile_installation_member_identity(pool, "octocat", 99002)

    assert await is_installation_member(pool, 703, "octocat") is False
    assert await is_installation_member(pool, 703, "otheruser") is True


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
async def test_create_personal_token_within_limit_evicts_oldest_on_overflow(pool):
    # 2026-10-18 fix: `aletheore login` mints a brand new personal token on
    # every login and nothing ever revokes one (no revoke endpoint exists),
    # so the old cap-and-reject-at-10 design permanently locked out any
    # identity that had logged in 10+ times. Minting past the cap must now
    # succeed by rotating out the single OLDEST non-revoked token, not by
    # rejecting or revoking everything (multi-machine sync needs every other
    # token to stay valid).
    identity = await get_or_create_personal_identity(pool, 70001, "octocat")

    token_ids = [
        await create_personal_token_within_limit(pool, identity["id"], f"hash-{index}", f"machine-{index}", limit=10)
        for index in range(10)
    ]

    # Backdate the first-inserted token so it is unambiguously the oldest by
    # created_at, independent of how close together these awaited inserts
    # actually land in wall-clock time.
    oldest_id = token_ids[0]
    await pool.execute(
        "UPDATE personal_api_tokens SET created_at = now() - interval '1 hour' WHERE id = $1",
        oldest_id,
    )

    new_id = await create_personal_token_within_limit(pool, identity["id"], "hash-overflow", "machine-overflow", limit=10)

    assert new_id is not None
    rows = await pool.fetch(
        "SELECT id, revoked_at FROM personal_api_tokens WHERE personal_identity_id = $1",
        identity["id"],
    )
    assert len(rows) == 11
    revoked_ids = {row["id"] for row in rows if row["revoked_at"] is not None}
    non_revoked_count = sum(1 for row in rows if row["revoked_at"] is None)
    assert revoked_ids == {oldest_id}
    assert non_revoked_count == 10


@pytest.mark.asyncio
async def test_create_personal_token_within_limit_evicted_token_no_longer_authenticates(pool):
    identity = await get_or_create_personal_identity(pool, 70002, "octocat")

    for index in range(10):
        await create_personal_token_within_limit(pool, identity["id"], f"hash-{index}", f"m-{index}", limit=10)

    oldest = await pool.fetchrow(
        "SELECT id, token_hash FROM personal_api_tokens WHERE personal_identity_id = $1 "
        "ORDER BY created_at ASC, id ASC LIMIT 1",
        identity["id"],
    )
    # Sanity check: still valid before the overflow mint.
    assert await get_personal_identity_by_token_hash(pool, oldest["token_hash"]) is not None

    await create_personal_token_within_limit(pool, identity["id"], "hash-overflow", "m-overflow", limit=10)

    assert await get_personal_identity_by_token_hash(pool, oldest["token_hash"]) is None


@pytest.mark.asyncio
async def test_create_personal_token_within_limit_is_concurrency_safe(pool):
    # Same regression class as test_create_api_token_within_limit_is_concurrency_safe,
    # but for the rotate-oldest personal-token cap: once an identity is
    # already at the limit, several near-simultaneous logins must never let
    # the non-revoked count exceed the limit and must never double-evict the
    # same row (the advisory xact lock this mirrors from
    # create_api_token_within_limit serializes the count-check, evict, and
    # insert into one atomic unit per caller).
    identity = await get_or_create_personal_identity(pool, 70003, "octocat")
    for index in range(10):
        await create_personal_token_within_limit(pool, identity["id"], f"hash-{index}", f"m-{index}", limit=10)

    results = await asyncio.gather(
        *(
            create_personal_token_within_limit(pool, identity["id"], f"hash-new-{index}", f"m-new-{index}", limit=10)
            for index in range(5)
        )
    )

    assert all(token_id is not None for token_id in results)
    assert len(set(results)) == 5

    rows = await pool.fetch(
        "SELECT id, revoked_at FROM personal_api_tokens WHERE personal_identity_id = $1",
        identity["id"],
    )
    non_revoked = [row for row in rows if row["revoked_at"] is None]
    revoked = [row for row in rows if row["revoked_at"] is not None]
    assert len(rows) == 15
    assert len(non_revoked) == 10
    assert len(revoked) == 5


@pytest.mark.asyncio
async def test_revoke_personal_token_is_idempotent_at_db_layer(pool):
    # Covers the DB layer directly, with no HTTP/auth gate in between. The
    # HTTP-layer idempotency test (two DELETE /v1/personal-tokens calls with
    # the same token) can't exercise this path: the second HTTP call 401s
    # on auth before revoke_personal_token is ever reached, since the first
    # call already revoked the only credential that could authorize it. A
    # future refactor that calls revoke_personal_token directly (e.g. a
    # retry path, or reordering auth vs. revoke) needs this guarded here.
    identity = await get_or_create_personal_identity(pool, 70004, "octocat")
    await create_personal_token_within_limit(pool, identity["id"], "hash-revoke-once", "machine-revoke-once", limit=10)

    first = await revoke_personal_token(pool, "hash-revoke-once")
    second = await revoke_personal_token(pool, "hash-revoke-once")

    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_revoke_all_personal_tokens_is_idempotent_at_db_layer(pool):
    # Same DB-layer idempotency gap as the test above, for the revoke-all
    # path: calling revoke_all_personal_tokens twice in a row, with no HTTP
    # auth gate between the two calls, must return the real revoked count
    # the first time and 0 the second time, with no exception either time.
    identity = await get_or_create_personal_identity(pool, 70005, "octocat")
    await create_personal_token_within_limit(pool, identity["id"], "hash-revoke-all-1", "machine-a", limit=10)
    await create_personal_token_within_limit(pool, identity["id"], "hash-revoke-all-2", "machine-b", limit=10)

    first = await revoke_all_personal_tokens(pool, identity["id"])
    second = await revoke_all_personal_tokens(pool, identity["id"])

    assert first == 2
    assert second == 0


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


@pytest.mark.asyncio
async def test_relogin_from_the_same_machine_replaces_its_own_token_only(pool):
    # Repeated logins on one laptop must not pile up and push the cap, which
    # would silently revoke a different machine's token.
    identity = await get_or_create_personal_identity(pool, 70002, "octocat2")
    other = await create_personal_token_within_limit(pool, identity["id"], "hash-other", "desktop (device flow)", limit=3)
    first = await create_personal_token_within_limit(pool, identity["id"], "hash-l1", "laptop (device flow)", limit=3)
    second = await create_personal_token_within_limit(pool, identity["id"], "hash-l2", "laptop (device flow)", limit=3)
    third = await create_personal_token_within_limit(pool, identity["id"], "hash-l3", "laptop (device flow)", limit=3)

    rows = {r["id"]: r["revoked_at"] for r in await pool.fetch(
        "SELECT id, revoked_at FROM personal_api_tokens WHERE personal_identity_id = $1", identity["id"]
    )}
    assert rows[other] is None  # the other machine is untouched
    assert rows[first] is not None and rows[second] is not None
    assert rows[third] is None
