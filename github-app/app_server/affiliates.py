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
    amount_usd: float,
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


async def list_affiliates_with_totals(pool: asyncpg.Pool) -> list[dict]:
    """One row per affiliate for the admin report page: how many
    installations they've referred, and total commission accrued vs. paid
    so far. LEFT JOINs so an affiliate with no referrals/commissions yet
    still shows up with zeroes rather than being dropped."""
    rows = await pool.fetch(
        """
        SELECT
            a.id,
            a.code,
            a.name,
            a.created_at,
            COUNT(DISTINCT r.installation_id) AS referral_count,
            COALESCE(SUM(c.amount_usd) FILTER (WHERE NOT c.paid), 0) AS total_owed_usd,
            COALESCE(SUM(c.amount_usd) FILTER (WHERE c.paid), 0) AS total_paid_usd
        FROM affiliates a
        LEFT JOIN affiliate_referrals r ON r.affiliate_id = a.id
        LEFT JOIN affiliate_commissions c ON c.affiliate_id = a.id
        GROUP BY a.id, a.code, a.name, a.created_at
        ORDER BY a.created_at
        """
    )
    return [dict(row) for row in rows]


async def mark_commissions_paid(pool: asyncpg.Pool, affiliate_id: int) -> int:
    """Marks every currently-unpaid commission for one affiliate as paid,
    after the admin has sent that amount manually outside the app. Returns
    the number of rows updated, for the route to confirm back to the
    caller."""
    result = await pool.execute(
        "UPDATE affiliate_commissions SET paid = true WHERE affiliate_id = $1 AND NOT paid",
        affiliate_id,
    )
    return int(result.split()[-1])
