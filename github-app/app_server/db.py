import json
import logging
from datetime import datetime

import asyncpg

from app_server.evidence_limits import check_evidence_size
from app_server.llm_cost import WARN_FRACTION_OF_CAP, crossed_spend_warning_threshold

logger = logging.getLogger(__name__)


async def create_pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn)


async def upsert_installation(pool: asyncpg.Pool, installation_id: int, account_login: str) -> None:
    await pool.execute(
        """
        INSERT INTO installations (installation_id, account_login)
        VALUES ($1, $2)
        ON CONFLICT (installation_id)
        DO UPDATE SET account_login = EXCLUDED.account_login, updated_at = now()
        """,
        installation_id,
        account_login,
    )


async def get_installation(pool: asyncpg.Pool, installation_id: int) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT installation_id, account_login, plan, webhook_url, alert_email,
               pushover_user_key, max_api_tokens, health_check_base_url,
               health_check_latency_threshold_ms, paddle_subscription_id,
               paddle_customer_id, llm_suggestions_enabled
        FROM installations
        WHERE installation_id = $1
        """,
        installation_id,
    )
    return dict(row) if row else None


async def get_installation_by_account_login(pool: asyncpg.Pool, account_login: str) -> dict | None:
    # Relies on installations_account_login_unique (migration 042) for a
    # well-defined result - before that constraint existed, a duplicate
    # row here would have made this an arbitrary pick.
    row = await pool.fetchrow(
        """
        SELECT installation_id, account_login, plan, webhook_url, alert_email,
               pushover_user_key, max_api_tokens, health_check_base_url,
               health_check_latency_threshold_ms, paddle_subscription_id,
               paddle_customer_id, llm_suggestions_enabled
        FROM installations
        WHERE account_login = $1
        """,
        account_login,
    )
    return dict(row) if row else None


async def set_installation_plan(pool: asyncpg.Pool, installation_id: int, plan: str) -> None:
    await pool.execute(
        "UPDATE installations SET plan = $2, updated_at = now() WHERE installation_id = $1",
        installation_id,
        plan,
    )


async def claim_free_to_paid_plan(
    pool: asyncpg.Pool, installation_id: int, plan: str
) -> bool:
    """Atomically claim the first free-to-paid transition for an installation.

    Also resets paid_setup_completed_at to NULL in the same UPDATE - see
    claim_paid_setup. This is the one place setup should ever become
    "pending": a genuine, freshly-observed free->paid transition, not an
    installation that was already paid for some other reason (migrated
    data, a direct insert, a paid->paid plan change).
    """
    row = await pool.fetchrow(
        """
        UPDATE installations
        SET plan = $2, updated_at = now(), paid_setup_completed_at = NULL
        WHERE installation_id = $1 AND plan = 'free'
        RETURNING installation_id
        """,
        installation_id,
        plan,
    )
    return row is not None


async def claim_paid_setup(pool: asyncpg.Pool, installation_id: int) -> bool:
    """Atomically claim the one-time paid setup (initial wiki/docs build,
    affiliate attribution) for an installation.

    Deliberately independent of claim_free_to_paid_plan's own transition
    check: if a crash lands between that write committing and setup
    actually running, a Paddle retry finds plan already non-free and
    claim_free_to_paid_plan correctly returns False - but setup still never
    ran. Gating setup on this claim instead of on that transition boolean
    means the retry still runs it exactly once, rather than skipping it
    forever because the plan write it depended on already happened.
    """
    row = await pool.fetchrow(
        """
        UPDATE installations
        SET paid_setup_completed_at = now()
        WHERE installation_id = $1 AND paid_setup_completed_at IS NULL
        RETURNING installation_id
        """,
        installation_id,
    )
    return row is not None


async def set_paid_installation_plan(
    pool: asyncpg.Pool, installation_id: int, plan: str
) -> None:
    """Update a paid plan without resurrecting an installation already downgraded."""
    await pool.execute(
        """
        UPDATE installations
        SET plan = $2, updated_at = now()
        WHERE installation_id = $1 AND plan <> 'free'
        """,
        installation_id,
        plan,
    )


async def add_paddle_ids_to_installation(
    pool: asyncpg.Pool,
    installation_id: int,
    paddle_subscription_id: str,
    paddle_customer_id: str,
) -> int:
    return await pool.fetchval(
        """
        UPDATE installations
        SET paddle_subscription_id = $2, paddle_customer_id = $3, updated_at = now()
        WHERE installation_id = $1
        RETURNING installation_id
        """,
        installation_id,
        paddle_subscription_id,
        paddle_customer_id,
    )


async def list_installations_for_ids(pool: asyncpg.Pool, installation_ids: list[int]) -> list[dict]:
    if not installation_ids:
        return []
    rows = await pool.fetch(
        """
        SELECT installation_id, account_login, plan, paddle_customer_id
        FROM installations
        WHERE installation_id = ANY($1::bigint[])
        ORDER BY account_login ASC
        """,
        installation_ids,
    )
    return [dict(row) for row in rows]


async def delete_installation(pool: asyncpg.Pool, installation_id: int) -> None:
    """Drop the installations row and everything cascading off it.

    Prefer purge_installation_data() for anything customer-facing: this
    leaves behind the member email addresses and sessions that are keyed by
    github_login rather than installation_id, and writes no audit row. It
    remains as the raw primitive for cascade tests.
    """
    await pool.execute("DELETE FROM installations WHERE installation_id = $1", installation_id)


async def set_public_status_enabled(
    pool: asyncpg.Pool, installation_id: int, repo_full_name: str, enabled: bool
) -> None:
    """Opts one specific repo into (or out of) the public, unauthenticated
    /v1/health/{org}/{repo} status API. Off by default (see migration 043),
    and scoped per repo (see migration 047) - endpoint paths, reachability,
    and latency derived from a customer's private repository must never be
    exposed without an explicit, repo-specific choice to do so. This must
    stay per-repo: the admin route that calls this is repo-scoped
    (/admin/{org}/{repo}/public-status), and an account-wide flag here
    would silently expose every other repo in the installation the moment
    one repo opted in (see F21)."""
    await pool.execute(
        """
        INSERT INTO repo_public_status (installation_id, repo_full_name, enabled, updated_at)
        VALUES ($1, $2, $3, now())
        ON CONFLICT (installation_id, repo_full_name)
        DO UPDATE SET enabled = EXCLUDED.enabled, updated_at = now()
        """,
        installation_id,
        repo_full_name,
        enabled,
    )


async def get_public_status_enabled(pool: asyncpg.Pool, installation_id: int, repo_full_name: str) -> bool:
    enabled = await pool.fetchval(
        "SELECT enabled FROM repo_public_status WHERE installation_id = $1 AND repo_full_name = $2",
        installation_id,
        repo_full_name,
    )
    return bool(enabled)


async def set_llm_suggestions_enabled(
    pool: asyncpg.Pool, installation_id: int, enabled: bool
) -> None:
    """Turn the non-evidence-backed suggestion section of managed audits on or off.

    Off means a managed audit contains only cited, evidence-backed findings -
    which is what the product promises, and what some customers need in order
    to hand a signed report to an auditor without caveats.
    """
    await pool.execute(
        "UPDATE installations SET llm_suggestions_enabled = $2, updated_at = now() "
        "WHERE installation_id = $1",
        installation_id,
        enabled,
    )


async def claim_webhook_delivery(
    pool: asyncpg.Pool, source: str, delivery_id: str, event: str
) -> bool:
    """Try to claim one inbound webhook delivery. True means this caller won
    it and should process the event; False means it has already been handled
    and this is a retry, a replay, or a concurrent duplicate.

    `source` namespaces the id ("github" for X-GitHub-Delivery GUIDs,
    "paddle" for event ids) so the two providers can't collide.

    Claims older than fifteen minutes are reclaimable. This is the recovery
    path for a process killed after claiming but before completing a webhook;
    ordinary retries remain deduplicated.

    A single INSERT ... ON CONFLICT DO NOTHING does the whole thing
    atomically. A read-then-write would leave a window where two concurrent
    deliveries of the same id both see "not seen yet" and both proceed -
    which is the exact duplicate-work outcome this exists to prevent.
    """
    row = await pool.fetchrow(
        """
        INSERT INTO webhook_deliveries (source, delivery_id, event, claimed_at)
        VALUES ($1, $2, $3, now())
        ON CONFLICT (source, delivery_id) DO UPDATE
        SET received_at = now(), claimed_at = now()
        WHERE webhook_deliveries.claimed_at < now() - interval '15 minutes'
        RETURNING delivery_id
        """,
        source,
        delivery_id,
        event,
    )
    return row is not None


async def release_webhook_delivery(pool: asyncpg.Pool, source: str, delivery_id: str) -> None:
    """Give up a claim so the provider's own retry of the same id can be
    processed later.

    Without this, a handler that raised would leave the delivery marked as
    handled forever and the retry - the thing that would have rescued it -
    would be silently discarded. Losing events outright is a worse failure
    than processing one twice.
    """
    await pool.execute(
        "DELETE FROM webhook_deliveries WHERE source = $1 AND delivery_id = $2",
        source,
        delivery_id,
    )


async def record_installation_access(
    pool: asyncpg.Pool, installation_id: int, github_login: str
) -> None:
    """Note that this login has passed _require_authorized_installation for
    this installation - on every plan, not just paid seats.

    This is purge_installation_data's actual source of truth for "who might
    have PII tied to this installation": installation_members is populated
    only for paid-plan seat holders (_require_seat_if_paid skips free plans
    entirely), so a free-plan installation always has zero rows there even
    though its real users have real sessions and captured emails. This
    table is separate from and doesn't affect installation_members, which
    remains exactly what it was - seat/billing bookkeeping.
    """
    await pool.execute(
        """
        INSERT INTO installation_access_log (installation_id, github_login)
        VALUES ($1, $2)
        ON CONFLICT (installation_id, github_login) DO UPDATE SET last_seen_at = now()
        """,
        installation_id,
        github_login,
    )


async def record_admin_action(
    pool: asyncpg.Pool,
    installation_id: int,
    actor_login: str,
    action: str,
    detail: dict | None = None,
) -> None:
    """Record one admin-mutating dashboard action - member/token/setting
    changes, not the data-deletion path, which already writes its own
    permanent data_deletion_log row.

    `detail` is for context that helps read the log later (a target login,
    a token label, a changed setting's new value) - never a secret. Callers
    must not pass a raw API token or webhook URL containing credentials.
    """
    await pool.execute(
        """
        INSERT INTO admin_action_log (installation_id, actor_login, action, detail)
        VALUES ($1, $2, $3, $4::jsonb)
        """,
        installation_id,
        actor_login,
        action,
        json.dumps(detail) if detail is not None else None,
    )


async def list_admin_actions(
    pool: asyncpg.Pool, installation_id: int, limit: int = 200
) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT id, actor_login, action, detail, created_at
        FROM admin_action_log
        WHERE installation_id = $1
        ORDER BY created_at DESC
        LIMIT $2
        """,
        installation_id,
        limit,
    )
    actions = []
    for row in rows:
        action = dict(row)
        detail = action["detail"]
        action["detail"] = json.loads(detail) if isinstance(detail, str) else detail
        actions.append(action)
    return actions


async def purge_installation_data(
    pool: asyncpg.Pool, installation_id: int, actor_login: str
) -> dict | None:
    """Erase everything the hosted service holds for one installation, and
    write an audit row proving it happened. Returns None if the
    installation was already gone (the caller asked for a no-op), otherwise
    a summary dict.

    Deleting the installations row cascades to every installation-scoped
    table. Two kinds of row don't cascade, because they're keyed by
    github_login rather than installation_id:

      - github_user_emails - a real email address, no TTL
      - sessions           - an encrypted GitHub access token

    Those are account-level, not installation-level, so they're only purged
    for people left with no *other* installation after this one goes. A
    user who administers two orgs shouldn't be logged out of the second one
    because the first deleted itself. "Left with no other installation" is
    decided from installation_access_log, not installation_members - the
    latter only covers paid seats and would silently skip every free-plan
    user's PII (see record_installation_access).

    The whole thing runs in one transaction: a partial purge that dropped
    the evidence but kept the email - or wrote the audit row for a delete
    that then rolled back - is worse than either outcome cleanly.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            installation = await conn.fetchrow(
                "SELECT account_login FROM installations WHERE installation_id = $1",
                installation_id,
            )
            if installation is None:
                return None

            # Read the access log and repo count before the cascade takes
            # both away - after the DELETE there is nothing left to count.
            member_logins = [
                row["github_login"]
                for row in await conn.fetch(
                    "SELECT github_login FROM installation_access_log WHERE installation_id = $1",
                    installation_id,
                )
            ]
            repos_deleted = await conn.fetchval(
                "SELECT count(DISTINCT repo_full_name) FROM repo_history WHERE installation_id = $1",
                installation_id,
            )

            await conn.execute(
                "DELETE FROM installations WHERE installation_id = $1", installation_id
            )

            users_purged = 0
            for login in member_logins:
                # installation_access_log rows for this installation are
                # gone with the cascade, so anything still here is another
                # installation this person has accessed.
                still_a_member = await conn.fetchval(
                    "SELECT count(*) FROM installation_access_log WHERE github_login = $1",
                    login,
                )
                if still_a_member:
                    continue
                await conn.execute(
                    "DELETE FROM github_user_emails WHERE github_login = $1", login
                )
                await conn.execute("DELETE FROM sessions WHERE github_login = $1", login)
                users_purged += 1

            await conn.execute(
                """
                INSERT INTO data_deletion_log
                    (installation_id, account_login, actor_login, repos_deleted, users_purged)
                VALUES ($1, $2, $3, $4, $5)
                """,
                installation_id,
                installation["account_login"],
                actor_login,
                repos_deleted,
                users_purged,
            )

    return {
        "installation_id": installation_id,
        "account_login": installation["account_login"],
        "repos_deleted": repos_deleted,
        "users_purged": users_purged,
    }


async def insert_repo_history(
    pool: asyncpg.Pool,
    installation_id: int,
    repo_full_name: str,
    scanned_at: datetime,
    evidence: dict,
    keep: int = 20,
) -> None:
    encoded = json.dumps(evidence)
    check_evidence_size(encoded)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO repo_history (installation_id, repo_full_name, scanned_at, evidence)
                VALUES ($1, $2, $3, $4::jsonb)
                """,
                installation_id,
                repo_full_name,
                scanned_at,
                encoded,
            )
            await conn.execute(
                """
                DELETE FROM repo_history
                WHERE id IN (
                    SELECT id
                    FROM repo_history
                    WHERE installation_id = $1 AND repo_full_name = $2
                    ORDER BY scanned_at DESC, id DESC
                    OFFSET $3
                )
                """,
                installation_id,
                repo_full_name,
                keep,
            )


# Pro plan: unlimited repos may be connected, but only this many distinct
# repos per installation may actually be scanned (PR scan, Flash review,
# managed audit - any of them count against the same shared cap) per
# calendar month. Free plan is not subject to this cap. Both
# scan_worker.jobs (sync, single sequential worker) and this module
# (async, genuinely concurrent HTTP callers via the managed-audit API)
# enforce the same cap against the same monthly_scanned_repos table.
MAX_SCANNED_REPOS_PER_MONTH = 10
# Advisory locks use the same Postgres global key space across app-server and
# scan-worker connections - pg_advisory_lock/pg_advisory_xact_lock key on the
# literal (namespace, key) pair regardless of which function or file took
# the lock, so every namespace value claimed in either file must stay
# disjoint from every namespace claimed in the other, not just internally
# consistent within one file. Live-verified: two unrelated locks sharing a
# namespace genuinely block each other whenever their second key (an
# installation id here, hashtext(f"{installation_id}:{repo}") in
# scan_worker's REPO_CHECKOUT_LOCK_NAMESPACE) happens to collide.
#
# scan_worker/db.py claims 1 (SCAN_SLOT_LOCK_NAMESPACE, shared/intentional -
# the same monthly-scan-slot reservation, taken from either process) and 2
# (SPEND_LOCK_NAMESPACE). 3 used to be claimed by both SEAT_LOCK_NAMESPACE
# here and scan_worker's REPO_CHECKOUT_LOCK_NAMESPACE - an independent,
# unintentional collision (docs/audits/Claude_Audit.md finding 30,
# confirmed live: a held checkout lock made a concurrent seat-admission
# call block for its full lock_timeout and then fail). Moved to 6, the
# first value neither file had claimed, rather than reusing 4 or 5 below
# (chosen after the collision was found, specifically to avoid it for new
# locks - but never applied to the original clash until now).
SCAN_SLOT_LOCK_NAMESPACE = 1
HEALTH_CHECK_TARGET_LOCK_NAMESPACE = 4
API_TOKEN_LOCK_NAMESPACE = 5
SEAT_LOCK_NAMESPACE = 6
ADVISORY_LOCK_TIMEOUT = "5s"


async def count_monthly_scanned_repos(pool: asyncpg.Pool, installation_id: int) -> int:
    row = await pool.fetchrow(
        """
        SELECT COUNT(*) AS count FROM monthly_scanned_repos
        WHERE installation_id = $1 AND month = date_trunc('month', now())::date
        """,
        installation_id,
    )
    return row["count"]


async def check_and_reserve_monthly_repo_scan_slot(
    pool: asyncpg.Pool, installation_id: int, repo_full_name: str, limit: int
) -> bool:
    """True if repo_full_name may be scanned this calendar month - either
    it's already one of this installation's counted repos this month, or
    there's still room under `limit` distinct repos and a slot gets
    reserved for it now. False means the monthly distinct-repo cap has
    already been reached by other repos, so this (new) repo must wait for
    next month.

    Wrapped in a per-installation advisory lock (released automatically
    at transaction end) rather than a plain check-then-insert: two
    concurrent managed-audit API requests for two different new repos on
    an installation right at its cap must not both read "still room" and
    both get let through.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('lock_timeout', $1, true)", ADVISORY_LOCK_TIMEOUT)
            # Namespace 1 is reserved for monthly scan-slot reservations.
            await conn.execute(
                "SELECT pg_advisory_xact_lock($1, $2)",
                SCAN_SLOT_LOCK_NAMESPACE,
                installation_id,
            )

            existing = await conn.fetchval(
                """
                SELECT 1 FROM monthly_scanned_repos
                WHERE installation_id = $1 AND repo_full_name = $2
                  AND month = date_trunc('month', now())::date
                """,
                installation_id,
                repo_full_name,
            )
            if existing is not None:
                return True

            count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM monthly_scanned_repos
                WHERE installation_id = $1 AND month = date_trunc('month', now())::date
                """,
                installation_id,
            )
            if count >= limit:
                return False

            await conn.execute(
                """
                INSERT INTO monthly_scanned_repos (installation_id, repo_full_name, month)
                VALUES ($1, $2, date_trunc('month', now())::date)
                ON CONFLICT (installation_id, repo_full_name, month) DO NOTHING
                """,
                installation_id,
                repo_full_name,
            )
            return True


async def check_and_reserve_managed_audit(
    pool: asyncpg.Pool,
    installation_id: int,
    repo_full_name: str,
    cooldown_seconds: int,
) -> bool:
    # A single atomic INSERT .. ON CONFLICT .. WHERE is required here rather than a
    # separate SELECT-then-UPDATE: two concurrent requests for the same repo must not
    # both read "cooldown expired" before either commits, or both would be allowed
    # through. The WHERE clause only lets the UPDATE (and therefore the RETURNING row)
    # through when the cooldown has actually elapsed - one row back means allowed and
    # already recorded, no row means still cooling down.
    row = await pool.fetchrow(
        """
        INSERT INTO managed_audit_rate_limits (installation_id, repo_full_name, last_run_at)
        VALUES ($1, $2, now())
        ON CONFLICT (installation_id, repo_full_name) DO UPDATE
        SET last_run_at = EXCLUDED.last_run_at
        WHERE managed_audit_rate_limits.last_run_at <= now() - make_interval(secs => $3)
        RETURNING last_run_at
        """,
        installation_id,
        repo_full_name,
        cooldown_seconds,
    )
    return row is not None


async def check_and_reserve_demo_scan(
    pool: asyncpg.Pool,
    client_ip: str,
    cooldown_seconds: int,
) -> bool:
    # Same atomic INSERT .. ON CONFLICT .. WHERE pattern as
    # check_and_reserve_managed_audit, keyed on IP instead of installation -
    # the public demo has no installation, only an anonymous caller.
    row = await pool.fetchrow(
        """
        INSERT INTO demo_scan_rate_limits (client_ip, last_run_at)
        VALUES ($1, now())
        ON CONFLICT (client_ip) DO UPDATE
        SET last_run_at = EXCLUDED.last_run_at
        WHERE demo_scan_rate_limits.last_run_at <= now() - make_interval(secs => $2)
        RETURNING last_run_at
        """,
        client_ip,
        cooldown_seconds,
    )
    return row is not None


async def get_llm_spend_this_month(pool: asyncpg.Pool, installation_id: int) -> float:
    row = await pool.fetchrow(
        """
        SELECT total_cost_usd FROM llm_spend
        WHERE installation_id = $1 AND month = date_trunc('month', now())::date
        """,
        installation_id,
    )
    return float(row["total_cost_usd"]) if row else 0.0


async def record_llm_spend(
    pool: asyncpg.Pool,
    installation_id: int,
    cost_usd: float,
    monthly_cap: float | None = None,
) -> None:
    """monthly_cap: when given, logs a one-time warning if this call is the
    one that pushes the installation's spend this month past
    WARN_FRACTION_OF_CAP of it - see llm_cost.crossed_spend_warning_threshold.
    Omit it (as existing callers that predate this did) to skip the check
    entirely; it has no effect on what gets recorded."""
    row = await pool.fetchrow(
        """
        INSERT INTO llm_spend (installation_id, month, total_cost_usd)
        VALUES ($1, date_trunc('month', now())::date, $2)
        ON CONFLICT (installation_id, month) DO UPDATE
        SET total_cost_usd = llm_spend.total_cost_usd + EXCLUDED.total_cost_usd
        RETURNING total_cost_usd
        """,
        installation_id,
        cost_usd,
    )
    if monthly_cap is not None and row is not None:
        new_total = float(row["total_cost_usd"])
        previous_total = new_total - cost_usd
        if crossed_spend_warning_threshold(previous_total, new_total, monthly_cap):
            logger.warning(
                "llm spend crossed %.0f%% of monthly cap: installation=%s $%.2f of $%.2f",
                WARN_FRACTION_OF_CAP * 100,
                installation_id,
                new_total,
                monthly_cap,
            )


async def get_flash_review_count_this_month(pool: asyncpg.Pool, installation_id: int) -> int:
    row = await pool.fetchrow(
        """
        SELECT review_count FROM flash_review_monthly_count
        WHERE installation_id = $1 AND month = date_trunc('month', now())::date
        """,
        installation_id,
    )
    return row["review_count"] if row else 0


async def get_extra_seats(pool: asyncpg.Pool, installation_id: int) -> int:
    row = await pool.fetchrow(
        "SELECT extra_seats FROM installations WHERE installation_id = $1",
        installation_id,
    )
    return row["extra_seats"] if row else 0


async def set_extra_seats(pool: asyncpg.Pool, installation_id: int, extra_seats: int) -> None:
    # The Paddle subscription's extra-seat line item quantity is the source
    # of truth, reconciled here from webhook events - never set directly by
    # the buy/remove-seat button, the same pattern installations.plan
    # already follows for the base subscription price.
    await pool.execute(
        "UPDATE installations SET extra_seats = $2 WHERE installation_id = $1",
        installation_id,
        extra_seats,
    )


INCLUDED_SEATS = {"air": 3}
DEFAULT_SEAT_LIMIT = 5


async def add_installation_member(
    pool: asyncpg.Pool, installation_id: int, github_login: str, added_by_github_login: str
) -> None:
    await pool.execute(
        """
        INSERT INTO installation_members (installation_id, github_login, added_by_github_login)
        VALUES ($1, $2, $3)
        ON CONFLICT (installation_id, github_login) DO NOTHING
        """,
        installation_id,
        github_login,
        added_by_github_login,
    )


async def add_installation_member_within_seat_limit(
    pool: asyncpg.Pool,
    installation_id: int,
    github_login: str,
    added_by_github_login: str,
    seat_limit: int,
) -> tuple[bool, bool]:
    """Atomically add github_login if the installation still has a seat.

    The route-level read/count/insert sequence is race-prone: concurrent
    requests for distinct logins can all read the same below-limit count
    before any insert commits. A per-installation advisory transaction lock
    serializes the count-bound insert, matching
    check_and_reserve_monthly_repo_scan_slot's concurrency pattern.

    Returns (allowed, inserted). Existing members are allowed but not newly
    inserted; a full installation returns (False, False).
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('lock_timeout', $1, true)", ADVISORY_LOCK_TIMEOUT)
            # Namespace 3 is reserved for installation seat admission.
            await conn.execute(
                "SELECT pg_advisory_xact_lock($1, $2)",
                SEAT_LOCK_NAMESPACE,
                installation_id,
            )
            row = await conn.fetchrow(
                """
                WITH existing AS (
                    SELECT 1
                    FROM installation_members
                    WHERE installation_id = $1 AND github_login = $2
                ),
                inserted AS (
                    INSERT INTO installation_members
                        (installation_id, github_login, added_by_github_login)
                    SELECT $1, $2, $3
                    WHERE NOT EXISTS (SELECT 1 FROM existing)
                      AND (
                          SELECT count(*)
                          FROM installation_members
                          WHERE installation_id = $1
                      ) < $4
                    ON CONFLICT (installation_id, github_login) DO NOTHING
                    RETURNING 1
                )
                SELECT
                    EXISTS (SELECT 1 FROM existing) AS already_member,
                    EXISTS (SELECT 1 FROM inserted) AS inserted
                """,
                installation_id,
                github_login,
                added_by_github_login,
                seat_limit,
            )
    return row["already_member"] or row["inserted"], row["inserted"]


async def add_initial_installation_member_if_empty(
    pool: asyncpg.Pool,
    installation_id: int,
    github_login: str,
    added_by_github_login: str,
) -> bool:
    """Seat exactly one first admin for a paid installation with no members."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('lock_timeout', $1, true)", ADVISORY_LOCK_TIMEOUT)
            # Namespace 3 is reserved for installation seat admission.
            await conn.execute(
                "SELECT pg_advisory_xact_lock($1, $2)",
                SEAT_LOCK_NAMESPACE,
                installation_id,
            )
            row = await conn.fetchrow(
                """
                INSERT INTO installation_members
                    (installation_id, github_login, added_by_github_login)
                SELECT $1, $2, $3
                WHERE NOT EXISTS (
                    SELECT 1 FROM installation_members WHERE installation_id = $1
                )
                ON CONFLICT (installation_id, github_login) DO NOTHING
                RETURNING 1
                """,
                installation_id,
                github_login,
                added_by_github_login,
            )
    return row is not None


async def remove_installation_member(pool: asyncpg.Pool, installation_id: int, github_login: str) -> None:
    await pool.execute(
        "DELETE FROM installation_members WHERE installation_id = $1 AND github_login = $2",
        installation_id,
        github_login,
    )


async def list_installation_members(pool: asyncpg.Pool, installation_id: int) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT github_login, added_by_github_login, added_at
        FROM installation_members
        WHERE installation_id = $1
        ORDER BY added_at ASC
        """,
        installation_id,
    )
    return [dict(row) for row in rows]


async def count_installation_members(pool: asyncpg.Pool, installation_id: int) -> int:
    row = await pool.fetchrow(
        "SELECT count(*) AS n FROM installation_members WHERE installation_id = $1",
        installation_id,
    )
    return row["n"]


async def is_installation_member(pool: asyncpg.Pool, installation_id: int, github_login: str) -> bool:
    row = await pool.fetchrow(
        "SELECT 1 FROM installation_members WHERE installation_id = $1 AND github_login = $2",
        installation_id,
        github_login,
    )
    return row is not None


async def list_installation_member_emails(pool: asyncpg.Pool, installation_id: int) -> list[str]:
    """Emails for every member of this installation who has logged in at
    least once (and so has a captured row in github_user_emails). Members
    added by username alone (see add_installation_member) but who've
    never signed in have no email on file yet, by design - inviting a
    not-yet-logged-in seat by email is an explicit v2, not v1, for
    transactional email.
    """
    rows = await pool.fetch(
        """
        SELECT e.email
        FROM installation_members m
        JOIN github_user_emails e ON e.github_login = m.github_login
        WHERE m.installation_id = $1
        """,
        installation_id,
    )
    return [row["email"] for row in rows]


# Health check targets live behind the same paid-plan gate as the rest of
# Settings (_require_admin_installation rejects free plans before any of
# this is ever reached), so there is no meaningful "free" entry here.
INCLUDED_HEALTH_CHECK_TARGETS = {"air": 5}
DEFAULT_HEALTH_CHECK_TARGET_LIMIT = 5


async def add_health_check_target(
    pool: asyncpg.Pool,
    installation_id: int,
    repo_full_name: str,
    label: str,
    base_url: str,
    latency_threshold_ms: int | None,
) -> int:
    row = await pool.fetchrow(
        """
        INSERT INTO health_check_targets (installation_id, repo_full_name, label, base_url, latency_threshold_ms)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (installation_id, repo_full_name, base_url) DO UPDATE
        SET label = EXCLUDED.label, latency_threshold_ms = EXCLUDED.latency_threshold_ms
        RETURNING id
        """,
        installation_id,
        repo_full_name,
        label,
        base_url,
        latency_threshold_ms,
    )
    return row["id"]


async def add_health_check_target_within_limit(
    pool: asyncpg.Pool,
    installation_id: int,
    repo_full_name: str,
    label: str,
    base_url: str,
    latency_threshold_ms: int | None,
    limit: int,
) -> int | None:
    """Atomic version of the route's former count-then-add_health_check_target
    sequence, same concurrency pattern as add_installation_member_within_seat_limit.

    The route-level read/count/insert was race-prone: two concurrent
    requests for two different URLs, both already one below the limit,
    could both read the same under-limit count before either insert
    committed, leaving the installation over its plan's health-check-target
    limit. A per-installation advisory transaction lock serializes the
    count-bound upsert instead.

    Returns the target's id (new or updated) if allowed, None if a
    genuinely new target would have exceeded the limit. An existing target
    (matched on installation_id, repo_full_name, base_url) is always
    allowed to update - only a real new insert counts against the limit,
    matching add_health_check_target's existing upsert semantics.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('lock_timeout', $1, true)", ADVISORY_LOCK_TIMEOUT)
            await conn.execute(
                "SELECT pg_advisory_xact_lock($1, $2)",
                HEALTH_CHECK_TARGET_LOCK_NAMESPACE,
                installation_id,
            )
            row = await conn.fetchrow(
                """
                WITH existing AS (
                    SELECT id
                    FROM health_check_targets
                    WHERE installation_id = $1 AND repo_full_name = $2 AND base_url = $3
                ),
                updated AS (
                    UPDATE health_check_targets
                    SET label = $4, latency_threshold_ms = $5
                    WHERE id IN (SELECT id FROM existing)
                    RETURNING id
                ),
                inserted AS (
                    INSERT INTO health_check_targets
                        (installation_id, repo_full_name, label, base_url, latency_threshold_ms)
                    SELECT $1, $2, $4, $3, $5
                    WHERE NOT EXISTS (SELECT 1 FROM existing)
                      AND (
                          SELECT count(*)
                          FROM health_check_targets
                          WHERE installation_id = $1 AND repo_full_name = $2
                      ) < $6
                    RETURNING id
                )
                SELECT id FROM updated
                UNION ALL
                SELECT id FROM inserted
                """,
                installation_id,
                repo_full_name,
                base_url,
                label,
                latency_threshold_ms,
                limit,
            )
    return row["id"] if row is not None else None


async def remove_health_check_target(pool: asyncpg.Pool, installation_id: int, repo_full_name: str, target_id: int) -> None:
    await pool.execute(
        "DELETE FROM health_check_targets WHERE id = $1 AND installation_id = $2 AND repo_full_name = $3",
        target_id,
        installation_id,
        repo_full_name,
    )


async def list_health_check_targets(pool: asyncpg.Pool, installation_id: int, repo_full_name: str) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT id, label, base_url, latency_threshold_ms, created_at
        FROM health_check_targets
        WHERE installation_id = $1 AND repo_full_name = $2
        ORDER BY created_at ASC
        """,
        installation_id,
        repo_full_name,
    )
    return [dict(row) for row in rows]


async def list_health_check_targets_for_installation(pool: asyncpg.Pool, installation_id: int) -> list[dict]:
    """Every health check target across every repo this installation has,
    for the data-export route - list_health_check_targets is scoped to one
    repo_full_name and would silently return nothing if called without one,
    not every target the installation actually has.
    """
    rows = await pool.fetch(
        """
        SELECT id, repo_full_name, label, base_url, latency_threshold_ms, created_at
        FROM health_check_targets
        WHERE installation_id = $1
        ORDER BY repo_full_name ASC, created_at ASC
        """,
        installation_id,
    )
    return [dict(row) for row in rows]


async def count_health_check_targets(pool: asyncpg.Pool, installation_id: int, repo_full_name: str) -> int:
    row = await pool.fetchrow(
        "SELECT count(*) AS n FROM health_check_targets WHERE installation_id = $1 AND repo_full_name = $2",
        installation_id,
        repo_full_name,
    )
    return row["n"]


async def get_recent_history(
    pool: asyncpg.Pool,
    installation_id: int,
    repo_full_name: str,
    limit: int = 20,
) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT scanned_at, evidence
        FROM repo_history
        WHERE installation_id = $1 AND repo_full_name = $2
        ORDER BY scanned_at DESC, id DESC
        LIMIT $3
        """,
        installation_id,
        repo_full_name,
        limit,
    )
    history = []
    for row in rows:
        evidence = row["evidence"]
        history.append(
            {
                "scanned_at": row["scanned_at"],
                "evidence": json.loads(evidence) if isinstance(evidence, str) else evidence,
            }
        )
    return history


async def get_latest_evidence(
    pool: asyncpg.Pool, installation_id: int, repo_full_name: str
) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT evidence
        FROM repo_history
        WHERE installation_id = $1 AND repo_full_name = $2
        ORDER BY scanned_at DESC, id DESC
        LIMIT 1
        """,
        installation_id,
        repo_full_name,
    )
    if row is None:
        return None
    evidence = row["evidence"]
    return json.loads(evidence) if isinstance(evidence, str) else evidence


async def get_recent_endpoint_health(
    pool: asyncpg.Pool, installation_id: int, repo_full_name: str
) -> list[dict]:
    # DISTINCT ON must include target_id, not just method+path - otherwise
    # two targets checking the exact same endpoint (e.g. staging and
    # production) collapse into a single row and one target's results
    # silently disappear.
    rows = await pool.fetch(
        """
        SELECT DISTINCT ON (eh.target_id, eh.endpoint_method, eh.endpoint_path)
            eh.target_id, t.label AS target_label, eh.endpoint_method, eh.endpoint_path,
            eh.reachable, eh.status_code, eh.latency_ms, eh.checked_at
        FROM endpoint_health eh
        LEFT JOIN health_check_targets t ON t.id = eh.target_id
        WHERE eh.installation_id = $1 AND eh.repo_full_name = $2
        ORDER BY eh.target_id, eh.endpoint_method, eh.endpoint_path, eh.checked_at DESC, eh.id DESC
        """,
        installation_id,
        repo_full_name,
    )
    return [dict(row) for row in rows]


MAX_ENDPOINT_HEALTH_HISTORY_ROWS = 100


async def get_endpoint_health_history(
    pool: asyncpg.Pool,
    installation_id: int,
    repo_full_name: str,
    target_id: int | None,
    endpoint_method: str,
    endpoint_path: str,
    limit: int = 50,
) -> list[dict]:
    # Every sweep persists a row per (target, endpoint) check, but until
    # this every read path only ever surfaced the single latest one -
    # a customer paying for "endpoint health monitoring" had no way to
    # see a trend, only a live dot. target_id can legitimately be NULL
    # (rows written before multi-target support existed), so this can't
    # just be "= $3" - IS NOT DISTINCT FROM treats two NULLs as equal.
    limit = min(max(limit, 1), MAX_ENDPOINT_HEALTH_HISTORY_ROWS)
    rows = await pool.fetch(
        """
        SELECT reachable, status_code, latency_ms, checked_at
        FROM endpoint_health
        WHERE installation_id = $1 AND repo_full_name = $2
          AND target_id IS NOT DISTINCT FROM $3
          AND endpoint_method = $4 AND endpoint_path = $5
        ORDER BY checked_at DESC, id DESC
        LIMIT $6
        """,
        installation_id,
        repo_full_name,
        target_id,
        endpoint_method,
        endpoint_path,
        limit,
    )
    return [dict(row) for row in rows]


async def get_endpoint_uptime_pct_since(
    pool: asyncpg.Pool,
    installation_id: int,
    repo_full_name: str,
    since: datetime,
) -> dict[tuple[str, str], float]:
    # Backs the public status API's trend signal. Deliberately an
    # aggregate percentage rather than the raw per-check history exposed
    # on the authenticated dashboard endpoint - an unauthenticated,
    # CORS-open route shouldn't hand out granular check-by-check timing
    # data to anyone who asks.
    rows = await pool.fetch(
        """
        SELECT endpoint_method, endpoint_path,
               (count(*) FILTER (WHERE reachable))::float / count(*) AS uptime_pct
        FROM endpoint_health
        WHERE installation_id = $1 AND repo_full_name = $2 AND checked_at >= $3
        GROUP BY endpoint_method, endpoint_path
        """,
        installation_id,
        repo_full_name,
        since,
    )
    return {(row["endpoint_method"], row["endpoint_path"]): row["uptime_pct"] for row in rows}


async def get_endpoint_health_summary_since(
    pool: asyncpg.Pool,
    installation_id: int,
    repo_full_name: str,
    since: datetime,
) -> dict[tuple[str, str], dict]:
    rows = await pool.fetch(
        """
        SELECT endpoint_method, endpoint_path, bool_or(reachable) AS ever_reachable, count(*) AS check_count
        FROM endpoint_health
        WHERE installation_id = $1 AND repo_full_name = $2 AND checked_at >= $3
        GROUP BY endpoint_method, endpoint_path
        """,
        installation_id,
        repo_full_name,
        since,
    )
    return {
        (row["endpoint_method"], row["endpoint_path"]): {
            "ever_reachable": row["ever_reachable"],
            "check_count": row["check_count"],
        }
        for row in rows
    }


async def create_session(
    pool: asyncpg.Pool,
    session_id: str,
    github_user_id: int,
    github_login: str,
    access_token: str,
    expires_at: datetime,
    refresh_token: str | None = None,
) -> None:
    await pool.execute(
        """
        INSERT INTO sessions (id, github_user_id, github_login, github_access_token, expires_at, github_refresh_token)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        session_id,
        github_user_id,
        github_login,
        access_token,
        expires_at,
        refresh_token,
    )


async def upsert_github_user_email(pool: asyncpg.Pool, github_login: str, email: str) -> bool:
    """Upserts the email captured via GitHub's user:email OAuth scope on
    every login - self-heals if the user's GitHub email changes, and
    deliberately kept separate from sessions (which expire and get pruned
    by run_session_cleanup_job) since transactional email needs an
    address that outlives any one session. Returns True only the first
    time an email is ever recorded for this login, which auth.py's
    callback uses to decide whether to enqueue the one-time welcome email.
    """
    row = await pool.fetchrow(
        """
        INSERT INTO github_user_emails (github_login, email, updated_at)
        VALUES ($1, $2, now())
        ON CONFLICT (github_login) DO UPDATE SET email = $2, updated_at = now()
        RETURNING (xmax = 0) AS inserted
        """,
        github_login,
        email,
    )
    return row["inserted"]


async def get_github_user_email(pool: asyncpg.Pool, github_login: str) -> str | None:
    return await pool.fetchval(
        "SELECT email FROM github_user_emails WHERE github_login = $1", github_login
    )


async def create_deletion_otp_code(
    pool: asyncpg.Pool,
    installation_id: int,
    requested_by: str,
    code_hash: str,
    expires_at: datetime,
) -> None:
    await pool.execute(
        """
        INSERT INTO deletion_otp_codes (installation_id, requested_by, code_hash, expires_at)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """,
        installation_id,
        requested_by,
        code_hash,
        expires_at,
    )


async def consume_deletion_otp_code(pool: asyncpg.Pool, installation_id: int, code_hash: str) -> bool:
    """Atomically claims one matching, unused, unexpired code. True means
    this call won it; a second call with the same code (a replay, or a
    double-submit) gets False, since used_at is now set.
    """
    row = await pool.fetchrow(
        """
        UPDATE deletion_otp_codes
        SET used_at = now()
        WHERE id = (
            SELECT id FROM deletion_otp_codes
            WHERE installation_id = $1 AND code_hash = $2
              AND used_at IS NULL AND expires_at > now()
            ORDER BY created_at DESC
            LIMIT 1
        )
        RETURNING id
        """,
        installation_id,
        code_hash,
    )
    return row is not None


async def get_session(pool: asyncpg.Pool, session_id: str) -> dict | None:
    # expires_at is also enforced by the signed cookie's own max_age, but
    # checking it here too means a session explicitly expired early (a
    # manual revocation, not just the periodic cleanup job catching up)
    # takes effect immediately rather than whenever cleanup next runs.
    row = await pool.fetchrow(
        """
        SELECT id, github_user_id, github_login, github_access_token, github_refresh_token, expires_at
        FROM sessions
        WHERE id = $1 AND expires_at > now()
        """,
        session_id,
    )
    return dict(row) if row else None


async def update_session_tokens(
    pool: asyncpg.Pool,
    session_id: str,
    access_token: str,
    refresh_token: str | None,
) -> None:
    await pool.execute(
        "UPDATE sessions SET github_access_token = $2, github_refresh_token = $3 WHERE id = $1",
        session_id,
        access_token,
        refresh_token,
    )


async def delete_session(pool: asyncpg.Pool, session_id: str) -> None:
    await pool.execute("DELETE FROM sessions WHERE id = $1", session_id)


async def set_webhook_url(pool: asyncpg.Pool, installation_id: int, url: str | None) -> None:
    await pool.execute(
        "UPDATE installations SET webhook_url = $2, updated_at = now() WHERE installation_id = $1",
        installation_id,
        url,
    )


async def set_alert_email(pool: asyncpg.Pool, installation_id: int, email: str | None) -> None:
    await pool.execute(
        "UPDATE installations SET alert_email = $2, updated_at = now() WHERE installation_id = $1",
        installation_id,
        email,
    )


async def set_pushover_user_key(pool: asyncpg.Pool, installation_id: int, user_key: str | None) -> None:
    await pool.execute(
        "UPDATE installations SET pushover_user_key = $2, updated_at = now() WHERE installation_id = $1",
        installation_id,
        user_key,
    )


async def get_max_tokens(pool: asyncpg.Pool, installation_id: int) -> int:
    row = await pool.fetchrow(
        "SELECT max_api_tokens FROM installations WHERE installation_id = $1",
        installation_id,
    )
    return row["max_api_tokens"] if row else 0


async def count_active_tokens(pool: asyncpg.Pool, installation_id: int) -> int:
    row = await pool.fetchrow(
        """
        SELECT count(*) AS n
        FROM api_tokens
        WHERE installation_id = $1 AND revoked_at IS NULL
        """,
        installation_id,
    )
    return row["n"]


async def create_api_token(
    pool: asyncpg.Pool,
    installation_id: int,
    token_hash: str,
    label: str,
    created_by_github_login: str,
) -> int:
    return await pool.fetchval(
        """
        INSERT INTO api_tokens (installation_id, token_hash, label, created_by_github_login)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """,
        installation_id,
        token_hash,
        label,
        created_by_github_login,
    )


async def create_api_token_within_limit(
    pool: asyncpg.Pool,
    installation_id: int,
    token_hash: str,
    label: str,
    created_by_github_login: str,
    limit: int,
) -> int | None:
    """Atomic version of the route's former count-then-create_api_token
    sequence, same concurrency pattern as add_installation_member_within_seat_limit
    and add_health_check_target_within_limit. Unlike those two, every call
    here is a genuinely new row (no natural key to upsert against - each
    token is unique by its random hash), so the CTE only needs the
    count-bound insert, not an existing/insert split.

    Returns the new token's id if allowed, None if it would have exceeded
    the limit.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('lock_timeout', $1, true)", ADVISORY_LOCK_TIMEOUT)
            await conn.execute(
                "SELECT pg_advisory_xact_lock($1, $2)",
                API_TOKEN_LOCK_NAMESPACE,
                installation_id,
            )
            row = await conn.fetchrow(
                """
                INSERT INTO api_tokens (installation_id, token_hash, label, created_by_github_login)
                SELECT $1, $2, $3, $4
                WHERE (SELECT count(*) FROM api_tokens WHERE installation_id = $1 AND revoked_at IS NULL) < $5
                RETURNING id
                """,
                installation_id,
                token_hash,
                label,
                created_by_github_login,
                limit,
            )
    return row["id"] if row is not None else None


async def revoke_api_token(pool: asyncpg.Pool, installation_id: int, token_id: int) -> None:
    await pool.execute(
        """
        UPDATE api_tokens SET revoked_at = now()
        WHERE id = $1 AND installation_id = $2 AND revoked_at IS NULL
        """,
        token_id,
        installation_id,
    )


async def list_api_tokens(pool: asyncpg.Pool, installation_id: int) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT id, label, created_by_github_login, created_at, last_used_at, revoked_at
        FROM api_tokens
        WHERE installation_id = $1
        ORDER BY created_at DESC, id DESC
        """,
        installation_id,
    )
    return [dict(row) for row in rows]


async def get_installation_by_token_hash(pool: asyncpg.Pool, token_hash: str) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT i.installation_id, i.account_login, i.plan
        FROM api_tokens t
        JOIN installations i ON i.installation_id = t.installation_id
        WHERE t.token_hash = $1 AND t.revoked_at IS NULL
        """,
        token_hash,
    )
    return dict(row) if row else None


async def touch_api_token(pool: asyncpg.Pool, token_hash: str) -> None:
    await pool.execute(
        "UPDATE api_tokens SET last_used_at = now() WHERE token_hash = $1",
        token_hash,
    )


async def get_audit_report_by_token(
    pool: asyncpg.Pool,
    verification_token: str,
) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT repo_full_name, report_text, content_hash, signature, signing_public_key, created_at
        FROM audit_reports
        WHERE verification_token = $1
        """,
        verification_token,
    )
    return dict(row) if row else None


async def list_repos_for_installations(pool: asyncpg.Pool, installation_ids: list[int]) -> list[dict]:
    if not installation_ids:
        return []
    rows = await pool.fetch(
        """
        SELECT DISTINCT rh.installation_id, rh.repo_full_name, i.account_login, i.plan
        FROM repo_history rh
        JOIN installations i ON i.installation_id = rh.installation_id
        LEFT JOIN hidden_repos hr
            ON hr.installation_id = rh.installation_id AND hr.repo_full_name = rh.repo_full_name
        WHERE rh.installation_id = ANY($1::bigint[])
          AND hr.installation_id IS NULL
        ORDER BY i.account_login ASC, rh.repo_full_name ASC
        """,
        installation_ids,
    )
    return [dict(row) for row in rows]


async def hide_repo(pool: asyncpg.Pool, installation_id: int, repo_full_name: str) -> None:
    """Marks a repo as soft-removed: hidden from the dashboard, and a no-op
    target for any new webhook/scheduled trigger (see is_repo_hidden). Fired
    from installation_repositories/removed - the customer deselected this
    one repo without uninstalling the app, so nothing here is deleted; see
    unhide_repo for the reverse.
    """
    await pool.execute(
        """
        INSERT INTO hidden_repos (installation_id, repo_full_name)
        VALUES ($1, $2)
        ON CONFLICT (installation_id, repo_full_name) DO NOTHING
        """,
        installation_id,
        repo_full_name,
    )


async def unhide_repo(pool: asyncpg.Pool, installation_id: int, repo_full_name: str) -> None:
    """Reverses hide_repo - fired from installation_repositories/added, in
    case the repo being (re-)added was previously deselected under this
    same installation.
    """
    await pool.execute(
        "DELETE FROM hidden_repos WHERE installation_id = $1 AND repo_full_name = $2",
        installation_id,
        repo_full_name,
    )


async def is_repo_hidden(pool: asyncpg.Pool, installation_id: int, repo_full_name: str) -> bool:
    row = await pool.fetchrow(
        "SELECT 1 FROM hidden_repos WHERE installation_id = $1 AND repo_full_name = $2",
        installation_id,
        repo_full_name,
    )
    return row is not None


async def get_wiki_build_status(pool: asyncpg.Pool, installation_id: int, repo_full_name: str) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT status, error_message, updated_at
        FROM wiki_build_status
        WHERE installation_id = $1 AND repo_full_name = $2
        """,
        installation_id,
        repo_full_name,
    )
    return dict(row) if row else None


async def get_wiki_overview(pool: asyncpg.Pool, installation_id: int, repo_full_name: str) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT description, diagram_mermaid, source_commit, updated_at
        FROM wiki_overview
        WHERE installation_id = $1 AND repo_full_name = $2
        """,
        installation_id,
        repo_full_name,
    )
    return dict(row) if row else None


async def list_wiki_subsystems(pool: asyncpg.Pool, installation_id: int, repo_full_name: str) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT subsystem_id, name, description, files, diagram_mermaid, source_commit, updated_at
        FROM wiki_subsystems
        WHERE installation_id = $1 AND repo_full_name = $2
        ORDER BY name ASC
        """,
        installation_id,
        repo_full_name,
    )
    result = []
    for row in rows:
        entry = dict(row)
        if isinstance(entry["files"], str):
            entry["files"] = json.loads(entry["files"])
        result.append(entry)
    return result


async def get_wiki_subsystem(
    pool: asyncpg.Pool, installation_id: int, repo_full_name: str, subsystem_id: str
) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT subsystem_id, name, description, files, diagram_mermaid, source_commit, updated_at
        FROM wiki_subsystems
        WHERE installation_id = $1 AND repo_full_name = $2 AND subsystem_id = $3
        """,
        installation_id,
        repo_full_name,
        subsystem_id,
    )
    if row is None:
        return None
    entry = dict(row)
    if isinstance(entry["files"], str):
        entry["files"] = json.loads(entry["files"])
    return entry


async def get_docs_build_status(pool: asyncpg.Pool, installation_id: int, repo_full_name: str) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT status, error_message, updated_at
        FROM docs_build_status
        WHERE installation_id = $1 AND repo_full_name = $2
        """,
        installation_id,
        repo_full_name,
    )
    return dict(row) if row else None


async def get_docs_repo_commit_settings(pool: asyncpg.Pool, installation_id: int, repo_full_name: str) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT enabled, pr_number, updated_at
        FROM docs_repo_commit_settings
        WHERE installation_id = $1 AND repo_full_name = $2
        """,
        installation_id,
        repo_full_name,
    )
    return dict(row) if row else None


async def set_docs_repo_commit_enabled(pool: asyncpg.Pool, installation_id: int, repo_full_name: str, enabled: bool) -> None:
    await pool.execute(
        """
        INSERT INTO docs_repo_commit_settings (installation_id, repo_full_name, enabled, updated_at)
        VALUES ($1, $2, $3, now())
        ON CONFLICT (installation_id, repo_full_name) DO UPDATE
        SET enabled = EXCLUDED.enabled, updated_at = now()
        """,
        installation_id,
        repo_full_name,
        enabled,
    )


async def list_docs_symbols(pool: asyncpg.Pool, installation_id: int, repo_full_name: str) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT module_path, symbol_name, description, mode, source_commit, updated_at
        FROM docs_symbols
        WHERE installation_id = $1 AND repo_full_name = $2
        ORDER BY module_path ASC, symbol_name ASC
        """,
        installation_id,
        repo_full_name,
    )
    return [dict(row) for row in rows]
