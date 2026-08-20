from decimal import Decimal

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
) -> None:
    """paddle_transaction_id is UNIQUE, so a retried transaction.completed
    delivery (Paddle retries on any non-2xx response) can't double-count
    the same commission."""
    await pool.execute(
        """
        INSERT INTO affiliate_commissions
            (affiliate_id, installation_id, paddle_transaction_id, amount_usd, transaction_date)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (paddle_transaction_id) DO NOTHING
        """,
        affiliate_id,
        installation_id,
        paddle_transaction_id,
        amount_usd,
        transaction_date,
    )


async def reverse_commission(pool: asyncpg.Pool, paddle_transaction_id: str) -> bool:
    """Mark a commission reversed while retaining its payment audit trail."""
    result = await pool.execute(
        """
        UPDATE affiliate_commissions
        SET reversed = true
        WHERE paddle_transaction_id = $1 AND NOT reversed
        """,
        paddle_transaction_id,
    )
    return result.endswith(" 1")


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
                (SELECT SUM(amount_usd) FROM affiliate_commissions c
                 WHERE c.affiliate_id = a.id AND NOT c.paid AND NOT c.reversed),
                0
            ) AS total_owed_usd,
            COALESCE(
                (SELECT SUM(amount_usd) FROM affiliate_commissions c
                 WHERE c.affiliate_id = a.id AND c.paid AND NOT c.reversed),
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
    # AND NOT reversed, matching list_affiliates_with_totals's own
    # total_owed_usd/total_paid_usd queries - without it, a commission
    # reversed via the Paddle refund/chargeback webhook path (correctly
    # already excluded from total_owed_usd) could still be matched here and
    # flipped to paid=true, after which it's also excluded from
    # total_paid_usd (same NOT reversed filter). The row would silently
    # disappear from both totals while sitting in the database marked
    # paid - a commission never actually paid out, with no trace in either
    # report.
    result = await pool.execute(
        "UPDATE affiliate_commissions SET paid = true WHERE affiliate_id = $1 AND NOT paid AND NOT reversed",
        affiliate_id,
    )
    return int(result.split()[-1])
