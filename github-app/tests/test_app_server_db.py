import pytest

from app_server.db import add_paddle_ids_to_installation, get_installation, get_review_history


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
async def test_get_review_history_orders_most_recent_first_and_limits(pool):
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (410, 'flash-org', 'flash')"
    )
    await pool.execute(
        """
        INSERT INTO flash_review_history
            (installation_id, repo_full_name, pr_number, outcome, finding_count, skip_reason, reviewed_at)
        VALUES
            (410, 'flash-org/repo', 1, 'posted', 3, NULL, now() - interval '2 hours'),
            (410, 'flash-org/repo', 2, 'clean', 0, NULL, now() - interval '1 hour'),
            (410, 'flash-org/repo', 3, 'skipped', 0, 'AI credit exhausted', now())
        """
    )
    # A different installation's rows must never leak into another's history.
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (411, 'other-org', 'flash')"
    )
    await pool.execute(
        """
        INSERT INTO flash_review_history (installation_id, repo_full_name, pr_number, outcome)
        VALUES (411, 'other-org/repo', 1, 'clean')
        """
    )

    rows = await get_review_history(pool, 410, limit=2)
    assert [r["pr_number"] for r in rows] == [3, 2]
    assert rows[0]["outcome"] == "skipped"
    assert rows[0]["skip_reason"] == "AI credit exhausted"
    assert rows[1]["finding_count"] == 0
