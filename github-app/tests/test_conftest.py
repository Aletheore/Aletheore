import pytest

# Real bug this reproduces: the `pool` fixture re-applies every migration
# file on each test's setup (they're idempotent CREATE/ALTER statements,
# safe to replay). Migration 058 narrows dismissed_findings.finding_type's
# CHECK constraint, then migration 068 widens it again to include
# 'static_analysis'. A test that inserts a 'static_analysis' row (anything
# exercising the static-analysis dismissal feature) used to leave that row
# in the real, persistent test Postgres when `pool` only truncated on
# setup, not on teardown. The *next* test's own setup then replayed
# migration 058's DROP/ADD CONSTRAINT against a table that still had that
# leftover 'static_analysis' row, and the narrowed constraint (4 values, no
# 'static_analysis') rejected it with a CheckViolationError - a failure
# with no relation to whatever that next test actually asserted.
#
# These two tests run in file order within the same pytest session and
# both depend on the real `pool` fixture, so the first test's fixture
# teardown genuinely runs (via pytest's own fixture lifecycle, not a direct
# call) before the second test's fixture setup replays the migrations -
# exactly the real cross-test sequence that broke in CI.


@pytest.mark.asyncio
async def test_a_leaves_a_static_analysis_dismissal_row_for_the_next_tests_setup_to_contend_with(pool):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO installations (installation_id, account_login) VALUES (424242, 'acme-conftest-test')"
        )
        await conn.execute(
            "INSERT INTO dismissed_findings "
            "(installation_id, repo_full_name, finding_type, identity_key, dismissed_by) "
            "VALUES (424242, 'acme/repo', 'static_analysis', 'bandit:B607:app.py:10', 'user')"
        )


@pytest.mark.asyncio
async def test_b_setup_does_not_choke_on_the_previous_tests_leftover_row(pool):
    # If test A's row survived into this test's `pool` setup (migration
    # replay), setup itself would have already raised CheckViolationError
    # before this test body ever ran - reaching this line is the assertion.
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT count(*) AS n FROM dismissed_findings")
    assert row["n"] == 0
