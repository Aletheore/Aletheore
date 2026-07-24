import pytest

from app_server.db import (
    add_paddle_ids_to_installation,
    get_installation,
    get_pending_subscription_claim_by_token,
    insert_pending_subscription_claim,
    mark_subscription_claim_claimed,
)


@pytest.mark.asyncio
async def test_insert_pending_subscription_claim_upserts_on_subscription_id(pool):
    await insert_pending_subscription_claim(pool, "tok_a", "sub_123", "ctm_1", "buyer@example.com", "team")
    row = await get_pending_subscription_claim_by_token(pool, "tok_a")
    assert row["plan"] == "team"
    assert row["claimed_at"] is None

    await insert_pending_subscription_claim(pool, "tok_a", "sub_123", "ctm_1", "buyer@example.com", "team")
    row_again = await get_pending_subscription_claim_by_token(pool, "tok_a")
    assert row_again["id"] == row["id"]


@pytest.mark.asyncio
async def test_mark_subscription_claim_claimed(pool):
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (1, 'acme', 'free')"
    )
    await insert_pending_subscription_claim(pool, "tok_b", "sub_456", "ctm_2", None, "indie")

    await mark_subscription_claim_claimed(pool, "tok_b", 1)

    row = await get_pending_subscription_claim_by_token(pool, "tok_b")
    assert row["claimed_at"] is not None
    assert row["claimed_by_installation_id"] == 1


@pytest.mark.asyncio
async def test_add_paddle_ids_to_installation(pool):
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (2, 'acme', 'free')"
    )
    await add_paddle_ids_to_installation(pool, 2, "sub_789", "ctm_3")
    row = await get_installation(pool, 2)
    assert row["paddle_subscription_id"] == "sub_789"
    assert row["paddle_customer_id"] == "ctm_3"
