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
    reverse_commission_partial,
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
    # No charged_total_minor recorded (the pre-migration shape) - nothing to
    # prorate against, so the first adjustment takes the whole commission,
    # same as the old all-or-nothing reverse_commission.
    affiliate = await create_affiliate(pool, "REV10", "dsc_rev", "Referred")
    await upsert_installation(pool, 907, "acme")
    await record_referral(pool, 907, affiliate["id"])
    await record_commission(
        pool, affiliate["id"], 907, "txn_refund", Decimal("4.50"), datetime.now(timezone.utc)
    )

    first = await reverse_commission_partial(pool, "adj_refund", "txn_refund", Decimal("999999"))
    assert first == {"status": "reversed", "reversed_usd": Decimal("4.50"), "remaining_usd": Decimal("0")}
    duplicate = await reverse_commission_partial(pool, "adj_refund", "txn_refund", Decimal("999999"))
    assert duplicate == {"status": "duplicate"}
    totals = {row["id"]: row for row in await list_affiliates_with_totals(pool)}
    assert totals[affiliate["id"]]["total_owed_usd"] == Decimal("0")
    assert await pool.fetchval(
        "SELECT reversed FROM affiliate_commissions WHERE paddle_transaction_id = 'txn_refund'"
    ) is True


@pytest.mark.asyncio
async def test_migration_071_backfills_reversed_usd_for_a_legacy_reversed_row(pool):
    # Real gap found by Flash Review on this same PR: a row already
    # reversed=true under the old all-or-nothing reverse_commission has
    # reversed_usd=0 from the column's own DEFAULT, not amount_usd. Left
    # unbackfilled, a later adjustment on the same transaction would compute
    # remaining = amount_usd - 0 and reverse the whole amount a second time.
    # This test re-runs migration 071's own backfill statement (verbatim)
    # against a row manually put into that exact pre-migration shape, since
    # by the time any test body runs, _apply_migrations has already applied
    # 071 to an empty table and there is no way to observe a real
    # historical row to backfill.
    affiliate = await create_affiliate(pool, "LEGACY10", "dsc_legacy", "Legacy")
    await upsert_installation(pool, 960, "acme")
    await record_referral(pool, 960, affiliate["id"])
    await record_commission(
        pool, affiliate["id"], 960, "txn_legacy", Decimal("4.50"), datetime.now(timezone.utc)
    )
    await pool.execute(
        "UPDATE affiliate_commissions SET reversed = true WHERE paddle_transaction_id = 'txn_legacy'"
    )
    assert await pool.fetchval(
        "SELECT reversed_usd FROM affiliate_commissions WHERE paddle_transaction_id = 'txn_legacy'"
    ) == Decimal("0.00")

    await pool.execute("UPDATE affiliate_commissions SET reversed_usd = amount_usd WHERE reversed")

    assert await pool.fetchval(
        "SELECT reversed_usd FROM affiliate_commissions WHERE paddle_transaction_id = 'txn_legacy'"
    ) == Decimal("4.50")
    # With the backfill applied, a later adjustment (e.g. a second,
    # unrelated chargeback event on the same transaction) correctly finds
    # nothing left to reverse instead of taking the full amount again.
    result = await reverse_commission_partial(pool, "adj_after_legacy", "txn_legacy", Decimal("999999"))
    assert result == {"status": "nothing_remaining", "reversed_usd": Decimal("0"), "remaining_usd": Decimal("0.00")}
    totals = {row["id"]: row for row in await list_affiliates_with_totals(pool)}
    assert totals[affiliate["id"]]["total_owed_usd"] == Decimal("0")


@pytest.mark.asyncio
async def test_a_redundant_adjustment_after_full_reversal_reports_nothing_remaining(pool):
    affiliate = await create_affiliate(pool, "REDUN10", "dsc_redun", "Redundant")
    await upsert_installation(pool, 961, "acme")
    await record_referral(pool, 961, affiliate["id"])
    await record_commission(
        pool, affiliate["id"], 961, "txn_redundant", Decimal("3.00"), datetime.now(timezone.utc)
    )
    first = await reverse_commission_partial(pool, "adj_redundant_1", "txn_redundant", Decimal("999999"))
    assert first["status"] == "reversed"

    second = await reverse_commission_partial(pool, "adj_redundant_2", "txn_redundant", Decimal("999999"))

    assert second == {"status": "nothing_remaining", "reversed_usd": Decimal("0"), "remaining_usd": Decimal("0")}


@pytest.mark.asyncio
async def test_a_zero_charged_total_minor_does_not_crash_and_reverses_in_full(pool):
    # A legitimately recorded charged_total_minor of exactly 0 (a
    # 100%-discounted checkout) must not be mistaken for "no total was ever
    # recorded" by truthiness, but it also can't be prorated against (any
    # share of 0 is undefined) - falls back to the same full-reversal path
    # as a pre-migration NULL row, not a ZeroDivisionError.
    affiliate = await create_affiliate(pool, "ZERO10", "dsc_zero", "Zero")
    await upsert_installation(pool, 962, "acme")
    await record_referral(pool, 962, affiliate["id"])
    await record_commission(
        pool, affiliate["id"], 962, "txn_zero_total", Decimal("1.00"), datetime.now(timezone.utc),
        charged_total_minor=Decimal("0"),
    )

    result = await reverse_commission_partial(pool, "adj_zero", "txn_zero_total", Decimal("500"))

    assert result == {"status": "reversed", "reversed_usd": Decimal("1.00"), "remaining_usd": Decimal("0")}


@pytest.mark.asyncio
async def test_a_partial_refund_prorates_the_commission_instead_of_zeroing_it(pool):
    # $29.99 charged, $29.99 * 0.15 = $4.50 commissioned, a $2.00 partial
    # refund (a billing-correction credit note) should take back ~15% of
    # that refund's share, not the whole commission.
    affiliate = await create_affiliate(pool, "PRO10", "dsc_pro", "Prorated")
    await upsert_installation(pool, 950, "acme")
    await record_referral(pool, 950, affiliate["id"])
    await record_commission(
        pool, affiliate["id"], 950, "txn_partial", Decimal("4.50"), datetime.now(timezone.utc),
        charged_total_minor=Decimal("2999"),
    )

    result = await reverse_commission_partial(pool, "adj_partial_1", "txn_partial", Decimal("200"))

    assert result["status"] == "reversed"
    # share = 200/2999 = 0.0667.., 4.50 * share rounds to 0.30
    assert result["reversed_usd"] == Decimal("0.30")
    assert result["remaining_usd"] == Decimal("4.20")
    totals = {row["id"]: row for row in await list_affiliates_with_totals(pool)}
    assert totals[affiliate["id"]]["total_owed_usd"] == Decimal("4.20")
    assert await pool.fetchval(
        "SELECT reversed FROM affiliate_commissions WHERE paddle_transaction_id = 'txn_partial'"
    ) is False


@pytest.mark.asyncio
async def test_multiple_partial_refunds_accumulate_and_cap_at_the_full_commission(pool):
    affiliate = await create_affiliate(pool, "ACC10", "dsc_acc", "Accumulated")
    await upsert_installation(pool, 951, "acme")
    await record_referral(pool, 951, affiliate["id"])
    await record_commission(
        pool, affiliate["id"], 951, "txn_multi", Decimal("4.50"), datetime.now(timezone.utc),
        charged_total_minor=Decimal("2999"),
    )

    first = await reverse_commission_partial(pool, "adj_multi_1", "txn_multi", Decimal("1500"))
    assert first["reversed_usd"] == Decimal("2.25")
    # A second, much larger refund than what's left charged must not take
    # back more than the commission has remaining.
    second = await reverse_commission_partial(pool, "adj_multi_2", "txn_multi", Decimal("1499"))
    assert second["reversed_usd"] == Decimal("2.25")
    assert second["remaining_usd"] == Decimal("0")
    totals = {row["id"]: row for row in await list_affiliates_with_totals(pool)}
    assert totals[affiliate["id"]]["total_owed_usd"] == Decimal("0")
    assert await pool.fetchval(
        "SELECT reversed FROM affiliate_commissions WHERE paddle_transaction_id = 'txn_multi'"
    ) is True


@pytest.mark.asyncio
async def test_reverse_commission_partial_is_a_noop_for_an_uncommissioned_transaction(pool):
    result = await reverse_commission_partial(pool, "adj_none", "txn_never_commissioned", Decimal("500"))
    assert result == {"status": "not_found"}


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
async def test_mark_commissions_paid_does_not_touch_a_reversed_commission(pool):
    # Regression test: a commission reversed via the Paddle
    # refund/chargeback webhook path (reverse_commission_partial) was already
    # correctly excluded from total_owed_usd, but mark_commissions_paid's
    # UPDATE had no NOT reversed filter, so an admin's "mark everything
    # paid" click could still flip it to paid=true - after which it's ALSO
    # excluded from total_paid_usd (same NOT reversed filter on that
    # query), silently vanishing from both totals while sitting in the
    # database marked paid. A commission never actually paid out, with no
    # trace in either report.
    affiliate = await create_affiliate(pool, "NIA10", "dsc_nia", "Nia")
    await upsert_installation(pool, 910, "acme")
    await record_referral(pool, 910, affiliate["id"])
    now = datetime.now(timezone.utc)
    await record_commission(pool, affiliate["id"], 910, "txn_reversed", Decimal("4.00"), now)
    await reverse_commission_partial(pool, "adj_reversed", "txn_reversed", Decimal("999999"))

    marked = await mark_commissions_paid(pool, affiliate["id"])

    assert marked == 0
    totals = {row["id"]: row for row in await list_affiliates_with_totals(pool)}
    assert totals[affiliate["id"]]["total_owed_usd"] == Decimal("0")
    assert totals[affiliate["id"]]["total_paid_usd"] == Decimal("0")


@pytest.mark.asyncio
async def test_mark_commissions_paid_still_pays_unreversed_commissions_alongside_a_reversed_one(pool):
    affiliate = await create_affiliate(pool, "OWEN10", "dsc_owen", "Owen")
    await upsert_installation(pool, 911, "acme")
    await record_referral(pool, 911, affiliate["id"])
    now = datetime.now(timezone.utc)
    await record_commission(pool, affiliate["id"], 911, "txn_good", Decimal("5.00"), now)
    await record_commission(pool, affiliate["id"], 911, "txn_bad", Decimal("4.00"), now)
    await reverse_commission_partial(pool, "adj_bad", "txn_bad", Decimal("999999"))

    marked = await mark_commissions_paid(pool, affiliate["id"])

    assert marked == 1
    totals = {row["id"]: row for row in await list_affiliates_with_totals(pool)}
    assert totals[affiliate["id"]]["total_paid_usd"] == Decimal("5.00")
    assert totals[affiliate["id"]]["total_owed_usd"] == Decimal("0")


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
