from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app_server.affiliates import (
    create_affiliate,
    get_affiliate_by_discount_id,
    get_referral,
    list_affiliates_with_totals,
    mark_commissions_paid,
    record_commission,
    record_referral,
    reverse_commission,
)
from app_server.db import upsert_installation


@pytest.mark.asyncio
async def test_create_affiliate_returns_the_inserted_row(pool):
    affiliate = await create_affiliate(pool, "SARAH10", "dsc_sarah", "Sarah")
    assert affiliate["code"] == "SARAH10"
    assert affiliate["paddle_discount_id"] == "dsc_sarah"
    assert affiliate["name"] == "Sarah"
    assert affiliate["id"] is not None


@pytest.mark.asyncio
async def test_get_affiliate_by_discount_id_finds_a_match(pool):
    created = await create_affiliate(pool, "MAYA10", "dsc_maya", "Maya")
    found = await get_affiliate_by_discount_id(pool, "dsc_maya")
    assert found["id"] == created["id"]


@pytest.mark.asyncio
async def test_get_affiliate_by_discount_id_returns_none_for_unknown_id(pool):
    assert await get_affiliate_by_discount_id(pool, "dsc_unknown") is None


@pytest.mark.asyncio
async def test_record_referral_creates_a_row(pool):
    affiliate = await create_affiliate(pool, "TOM10", "dsc_tom", "Tom")
    await upsert_installation(pool, 900, "acme")

    await record_referral(pool, 900, affiliate["id"])

    referral = await get_referral(pool, 900)
    assert referral["affiliate_id"] == affiliate["id"]


@pytest.mark.asyncio
async def test_second_referral_for_the_same_installation_is_a_no_op(pool):
    # First-touch attribution: installation_id is the table's primary key,
    # so a later event (e.g. a re-delivered webhook, or a second
    # subscription for the same installation) can't steal credit from
    # whichever affiliate referred it first.
    first_affiliate = await create_affiliate(pool, "FIRST10", "dsc_first", "First")
    second_affiliate = await create_affiliate(pool, "SECOND10", "dsc_second", "Second")
    await upsert_installation(pool, 901, "acme")

    await record_referral(pool, 901, first_affiliate["id"])
    await record_referral(pool, 901, second_affiliate["id"])

    referral = await get_referral(pool, 901)
    assert referral["affiliate_id"] == first_affiliate["id"]


@pytest.mark.asyncio
async def test_get_referral_returns_none_when_unreferred(pool):
    await upsert_installation(pool, 902, "acme")
    assert await get_referral(pool, 902) is None


@pytest.mark.asyncio
async def test_record_commission_creates_a_row_reflected_in_totals(pool):
    affiliate = await create_affiliate(pool, "NINA10", "dsc_nina", "Nina")
    await upsert_installation(pool, 903, "acme")
    await record_referral(pool, 903, affiliate["id"])

    await record_commission(
        pool, affiliate["id"], 903, "txn_1", Decimal("4.50"), datetime.now(timezone.utc)
    )

    totals = {row["id"]: row for row in await list_affiliates_with_totals(pool)}
    assert totals[affiliate["id"]]["total_owed_usd"] == Decimal("4.50")
    assert totals[affiliate["id"]]["total_paid_usd"] == Decimal("0")


@pytest.mark.asyncio
async def test_duplicate_paddle_transaction_id_does_not_double_count(pool):
    # Paddle retries webhook delivery on any non-2xx response, re-sending
    # the same transaction id - this must not double the commission.
    affiliate = await create_affiliate(pool, "OMAR10", "dsc_omar", "Omar")
    await upsert_installation(pool, 904, "acme")
    await record_referral(pool, 904, affiliate["id"])
    now = datetime.now(timezone.utc)

    await record_commission(pool, affiliate["id"], 904, "txn_dupe", Decimal("4.50"), now)
    await record_commission(pool, affiliate["id"], 904, "txn_dupe", Decimal("4.50"), now)

    totals = {row["id"]: row for row in await list_affiliates_with_totals(pool)}
    assert totals[affiliate["id"]]["total_owed_usd"] == Decimal("4.50")


@pytest.mark.asyncio
async def test_reversed_commission_is_preserved_but_excluded_from_totals(pool):
    affiliate = await create_affiliate(pool, "REV10", "dsc_rev", "Referred")
    await upsert_installation(pool, 907, "acme")
    await record_referral(pool, 907, affiliate["id"])
    await record_commission(
        pool, affiliate["id"], 907, "txn_refund", Decimal("4.50"), datetime.now(timezone.utc)
    )

    assert await reverse_commission(pool, "txn_refund") is True
    assert await reverse_commission(pool, "txn_refund") is False
    totals = {row["id"]: row for row in await list_affiliates_with_totals(pool)}
    assert totals[affiliate["id"]]["total_owed_usd"] == Decimal("0")
    assert await pool.fetchval(
        "SELECT reversed FROM affiliate_commissions WHERE paddle_transaction_id = 'txn_refund'"
    ) is True


@pytest.mark.asyncio
async def test_list_affiliates_with_totals_counts_distinct_referrals(pool):
    affiliate = await create_affiliate(pool, "PAT10", "dsc_pat", "Pat")
    await upsert_installation(pool, 905, "acme")
    await upsert_installation(pool, 906, "beta")
    await record_referral(pool, 905, affiliate["id"])
    await record_referral(pool, 906, affiliate["id"])

    totals = {row["id"]: row for row in await list_affiliates_with_totals(pool)}
    assert totals[affiliate["id"]]["referral_count"] == 2


@pytest.mark.asyncio
async def test_list_affiliates_with_totals_includes_affiliates_with_no_referrals(pool):
    affiliate = await create_affiliate(pool, "QUINN10", "dsc_quinn", "Quinn")
    totals = {row["id"]: row for row in await list_affiliates_with_totals(pool)}
    assert totals[affiliate["id"]]["referral_count"] == 0
    assert totals[affiliate["id"]]["total_owed_usd"] == Decimal("0")


@pytest.mark.asyncio
async def test_mark_commissions_paid_moves_owed_to_paid(pool):
    affiliate = await create_affiliate(pool, "RIA10", "dsc_ria", "Ria")
    await upsert_installation(pool, 907, "acme")
    await record_referral(pool, 907, affiliate["id"])
    now = datetime.now(timezone.utc)
    await record_commission(pool, affiliate["id"], 907, "txn_a", Decimal("3.00"), now)
    await record_commission(pool, affiliate["id"], 907, "txn_b", Decimal("2.00"), now)

    marked = await mark_commissions_paid(pool, affiliate["id"])

    assert marked == 2
    totals = {row["id"]: row for row in await list_affiliates_with_totals(pool)}
    assert totals[affiliate["id"]]["total_owed_usd"] == Decimal("0")
    assert totals[affiliate["id"]]["total_paid_usd"] == Decimal("5.00")


@pytest.mark.asyncio
async def test_mark_commissions_paid_does_not_touch_other_affiliates(pool):
    affiliate_a = await create_affiliate(pool, "SAM10", "dsc_sam", "Sam")
    affiliate_b = await create_affiliate(pool, "UMA10", "dsc_uma", "Uma")
    await upsert_installation(pool, 908, "acme")
    await upsert_installation(pool, 909, "beta")
    await record_referral(pool, 908, affiliate_a["id"])
    await record_referral(pool, 909, affiliate_b["id"])
    now = datetime.now(timezone.utc)
    await record_commission(pool, affiliate_a["id"], 908, "txn_c", Decimal("3.00"), now)
    await record_commission(pool, affiliate_b["id"], 909, "txn_d", Decimal("7.00"), now)

    await mark_commissions_paid(pool, affiliate_a["id"])

    totals = {row["id"]: row for row in await list_affiliates_with_totals(pool)}
    assert totals[affiliate_a["id"]]["total_paid_usd"] == Decimal("3.00")
    assert totals[affiliate_b["id"]]["total_owed_usd"] == Decimal("7.00")
    assert totals[affiliate_b["id"]]["total_paid_usd"] == Decimal("0")


@pytest.mark.asyncio
async def test_totals_are_not_multiplied_by_referral_count(pool):
    """Regression for a real bug: joining affiliate_referrals and
    affiliate_commissions onto affiliates in one query is a cartesian
    product between the two (R referrals x C commissions = R*C rows), so a
    plain SUM(amount_usd) counted every commission once per referral
    instead of once. The multiplier is exactly the referral count, which is
    why a fixture with one referral - test_mark_commissions_paid_moves_owed_to_paid
    above, R=1 - cannot catch it: the multiplication factor is 1 either
    way. This needs at least two referrals and at least one commission to
    expose it. Reproduced before the fix: 3 referrals + $30 of real
    commissions reported $90.00 owed."""
    affiliate = await create_affiliate(pool, "VERA10", "dsc_vera", "Vera")
    await upsert_installation(pool, 910, "acme")
    await upsert_installation(pool, 911, "beta")
    await upsert_installation(pool, 912, "gamma")
    await record_referral(pool, 910, affiliate["id"])
    await record_referral(pool, 911, affiliate["id"])
    await record_referral(pool, 912, affiliate["id"])
    now = datetime.now(timezone.utc)
    await record_commission(pool, affiliate["id"], 910, "txn_e", Decimal("10.00"), now)
    await record_commission(pool, affiliate["id"], 911, "txn_f", Decimal("20.00"), now)

    totals = {row["id"]: row for row in await list_affiliates_with_totals(pool)}

    assert totals[affiliate["id"]]["referral_count"] == 3
    assert totals[affiliate["id"]]["total_owed_usd"] == Decimal("30.00")
