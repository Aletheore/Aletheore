import pytest

from app_server.db import add_paddle_ids_to_installation, get_installation


@pytest.mark.asyncio
async def test_add_paddle_ids_to_installation(pool):
    await pool.execute(
        "INSERT INTO installations (installation_id, account_login, plan) VALUES (2, 'acme', 'free')"
    )
    await add_paddle_ids_to_installation(pool, 2, "sub_789", "ctm_3")
    row = await get_installation(pool, 2)
    assert row["paddle_subscription_id"] == "sub_789"
    assert row["paddle_customer_id"] == "ctm_3"
