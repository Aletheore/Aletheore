from decimal import ROUND_HALF_UP, Decimal

import asyncpg


async def create_affiliate(pool: asyncpg.Pool, code: str, paddle_discount_id: str, name: str) -> dict:
    row = await pool.fetchrow(
        """
        INSERT INTO affiliates (code, paddle_discount_id, name)
        VALUES ($1, $2, $3)
        RETURNING id, code, paddle_discount_id, name, created_at
        """,
        code,
        paddle_discount_id,
        name,
    )
    return dict(row)


async def get_affiliate_by_discount_id(pool: asyncpg.Pool, paddle_discount_id: str) -> dict | None:
    row = await pool.fetchrow(
        "SELECT id, code, paddle_discount_id, name, created_at FROM affiliates WHERE paddle_discount_id = $1",
        paddle_discount_id,
    )
    return dict(row) if row is not None else None


async def record_referral(pool: asyncpg.Pool, installation_id: int, affiliate_id: int) -> None:
    """First-touch, permanent attribution. installation_id is the table's
    primary key, so a second referral for an installation that already has
    one (e.g. a re-delivered webhook, or a later subscription.created for
    the same installation) is a no-op rather than overwriting who gets
    credit."""
    await pool.execute(
        """
        INSERT INTO affiliate_referrals (installation_id, affiliate_id)
        VALUES ($1, $2)
        ON CONFLICT (installation_id) DO NOTHING
        """,
        installation_id,
        affiliate_id,
    )


async def get_referral(pool: asyncpg.Pool, installation_id: int) -> dict | None:
    row = await pool.fetchrow(
        "SELECT installation_id, affiliate_id, referred_at FROM affiliate_referrals WHERE installation_id = $1",
        installation_id,
    )
    return dict(row) if row is not None else None


async def record_commission(
    pool: asyncpg.Pool,
    affiliate_id: int,
    installation_id: int,
    paddle_transaction_id: str,
    amount_usd: Decimal,
    transaction_date,
    charged_total_minor: Decimal | None = None,
) -> None:
    """paddle_transaction_id is UNIQUE, so a retried transaction.completed
    delivery (Paddle retries on any non-2xx response) can't double-count
    the same commission.

    charged_total_minor (the transaction's own details.totals.total, in its
    original currency) is what reverse_commission_partial prorates a later
    refund/chargeback against - see its own docstring."""
    await pool.execute(
        """
        INSERT INTO affiliate_commissions
            (affiliate_id, installation_id, paddle_transaction_id, amount_usd, transaction_date,
             charged_total_minor)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (paddle_transaction_id) DO NOTHING
        """,
        affiliate_id,
        installation_id,
        paddle_transaction_id,
        amount_usd,
        transaction_date,
        charged_total_minor,
    )


async def reverse_commission_partial(
    pool: asyncpg.Pool,
    adjustment_id: str | None,
    paddle_transaction_id: str,
    refunded_total_minor: Decimal | None,
) -> dict:
    """Reduces a commission by the same share of it that a refund or
    chargeback adjustment returned, instead of zeroing the whole thing for a
    partial refund - same proration as claw_back_topup_credit: share =
    refunded_total_minor over the transaction's own charged_total_minor, in
    the transaction's original currency.

    A commission recorded before charged_total_minor existed (NULL) has
    nothing to prorate against, so it keeps the old behavior: the first
    adjustment reverses it in full.

    adjustment_id/refunded_total_minor may be None (an unexpectedly-shaped
    Paddle payload missing an id or a usable totals.total) - the row lookup
    happens before either is required, so a transaction with no commission
    at all (not a referred installation, e.g. most transactions) still
    returns "not_found" rather than a status implying a commission was
    affected. This lets the caller alert only when a real commission
    couldn't be resolved, not on every malformed-looking payload.

    Returns {"status": "reversed", reversed_usd, remaining_usd};
    "nothing_remaining" (same shape as "reversed", reversed_usd=0, but the
    commission was already fully reversed - e.g. a legacy row backfilled by
    migration 071, or a later adjustment arriving after an earlier one
    already zeroed it); "duplicate" (this adjustment_id was already
    applied); "missing_adjustment_id" or "unknown_amount" (a real
    commission exists for this transaction, but the payload didn't carry
    what's needed to apply or dedupe the reversal); or "not_found" (no
    commission for this transaction at all)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT amount_usd, charged_total_minor, reversed_usd FROM affiliate_commissions "
                "WHERE paddle_transaction_id = $1 FOR UPDATE",
                paddle_transaction_id,
            )
            if row is None:
                return {"status": "not_found"}
            if adjustment_id is None:
                return {"status": "missing_adjustment_id"}
            if refunded_total_minor is None:
                return {"status": "unknown_amount"}
            claimed = await conn.fetchrow(
                "INSERT INTO affiliate_commission_adjustments (adjustment_id, transaction_id) "
                "VALUES ($1, $2) ON CONFLICT (adjustment_id) DO NOTHING RETURNING adjustment_id",
                adjustment_id, paddle_transaction_id,
            )
            if claimed is None:
                return {"status": "duplicate"}

            remaining = row["amount_usd"] - row["reversed_usd"]
            if remaining <= 0:
                return {"status": "nothing_remaining", "reversed_usd": Decimal(0), "remaining_usd": remaining}
            # `is not None` rather than truthiness: a legitimately recorded
            # charged_total_minor of exactly 0 (a 100%-discounted checkout)
            # must not be treated the same as a legacy NULL row - both would
            # otherwise take the same "can't prorate, take it all" path, but
            # only NULL actually means "no total was ever recorded".
            if row["charged_total_minor"] is not None and row["charged_total_minor"] > 0:
                share = min(Decimal(1), refunded_total_minor / row["charged_total_minor"])
                reverse = (
                    remaining
                    if share >= 1
                    else min(remaining, (row["amount_usd"] * share).quantize(Decimal("0.01"), ROUND_HALF_UP))
                )
            else:
                # No original-currency total recorded (pre-migration row, or
                # a recorded zero) - can't prorate, so the first adjustment
                # takes it all, same as the old all-or-nothing
                # reverse_commission.
                reverse = remaining

            new_reversed_usd = row["reversed_usd"] + reverse
            await conn.execute(
                "UPDATE affiliate_commissions SET reversed_usd = $2, reversed = ($2 >= amount_usd) "
                "WHERE paddle_transaction_id = $1",
                paddle_transaction_id, new_reversed_usd,
            )
            await conn.execute(
                "UPDATE affiliate_commission_adjustments SET reversed_usd = $2 WHERE adjustment_id = $1",
                adjustment_id, reverse,
            )
            return {
                "status": "reversed",
                "reversed_usd": reverse,
                "remaining_usd": row["amount_usd"] - new_reversed_usd,
            }


async def list_affiliates_with_totals(pool: asyncpg.Pool) -> list[dict]:
    """One row per affiliate for the admin report page: how many
    installations they've referred, and total commission accrued vs. paid
    so far.

    Each total is a scalar subquery, not a JOIN, deliberately: joining
    affiliate_referrals and affiliate_commissions onto affiliates in one
    query is a cartesian product between the two (R referrals x C
    commissions for one affiliate = R*C rows), and while
    COUNT(DISTINCT r.installation_id) survives that fan-out, SUM(amount_usd)
    does not - every commission was summed once per referral. Reproduced:
    an affiliate with 3 referrals and $30 of real commissions reported
    $90.00 owed. A single referral (R=1) hides it completely, which is
    exactly what a one-referral manual check would show as correct.

    SUM(amount_usd - reversed_usd), not a NOT reversed filter: a partial
    refund (reverse_commission_partial) lowers reversed_usd without ever
    setting reversed (that only flips once reversed_usd reaches amount_usd),
    so filtering the row out entirely would still count its full, no-longer-
    owed amount.
    """
    rows = await pool.fetch(
        """
        SELECT
            a.id,
            a.code,
            a.name,
            a.created_at,
            (SELECT COUNT(*) FROM affiliate_referrals r WHERE r.affiliate_id = a.id) AS referral_count,
            COALESCE(
                (SELECT SUM(amount_usd - reversed_usd) FROM affiliate_commissions c
                 WHERE c.affiliate_id = a.id AND NOT c.paid),
                0
            ) AS total_owed_usd,
            COALESCE(
                (SELECT SUM(amount_usd - reversed_usd) FROM affiliate_commissions c
                 WHERE c.affiliate_id = a.id AND c.paid),
                0
            ) AS total_paid_usd
        FROM affiliates a
        ORDER BY a.created_at
        """
    )
    return [dict(row) for row in rows]


async def mark_commissions_paid(pool: asyncpg.Pool, affiliate_id: int) -> int:
    """Marks every currently-unpaid commission for one affiliate as paid,
    after the admin has sent that amount manually outside the app. Returns
    the number of rows updated, for the route to confirm back to the
    caller."""
    # AND NOT reversed - true only once reverse_commission_partial has taken
    # the whole commission back (reversed_usd reached amount_usd), so this
    # just keeps a fully-reversed, nothing-owed row from being flipped to
    # paid=true for an amount that was never sent. A partially reversed row
    # (reversed=false, reversed_usd > 0) still gets marked paid here, at its
    # now-reduced owed amount - list_affiliates_with_totals's SUM(amount_usd
    # - reversed_usd) already reflects that reduction in both totals.
    result = await pool.execute(
        "UPDATE affiliate_commissions SET paid = true WHERE affiliate_id = $1 AND NOT paid AND NOT reversed",
        affiliate_id,
    )
    return int(result.split()[-1])
