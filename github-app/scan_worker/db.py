import logging
import json
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache

import psycopg
import psycopg.rows
from psycopg_pool import ConnectionPool

from app_server.evidence_limits import check_evidence_size
from app_server.llm_cost import WARN_FRACTION_OF_CAP, crossed_spend_warning_threshold

logger = logging.getLogger(__name__)

# Advisory locks share one Postgres key space across app-server and
# scan-worker. Keep namespace 1 identical to app_server.db; namespace 2 is
# reserved for the session-scoped spend lock. The installation id is key 2.
SCAN_SLOT_LOCK_NAMESPACE = 1
SPEND_LOCK_NAMESPACE = 2
# Namespace 3 is reserved for the per-repo checkout lock (see
# repo_checkout_lock) - key 2 is hashtext(installation_id:repo_full_name)
# rather than a bare int, since the resource being protected is a
# composite (installation, repo) pair, not a single id.
REPO_CHECKOUT_LOCK_NAMESPACE = 3
ADVISORY_LOCK_TIMEOUT = "5s"
INSTALLATION_SPEND_LOCK_MAX_ATTEMPTS = 4
INSTALLATION_SPEND_LOCK_RETRY_DELAY_SECONDS = 3


@lru_cache(maxsize=None)
def get_db_pool(dsn: str) -> ConnectionPool:
    return ConnectionPool(conninfo=dsn, min_size=0, max_size=4, open=True)

from aletheore.evidence import is_evidence_version_compatible


def insert_repo_history(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    scanned_at: datetime,
    evidence: dict,
    keep: int = 20,
) -> int:
    encoded = json.dumps(evidence)
    check_evidence_size(encoded)

    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO repo_history (installation_id, repo_full_name, scanned_at, evidence)
                VALUES (%s, %s, %s, %s::jsonb)
                RETURNING id
                """,
                (installation_id, repo_full_name, scanned_at, encoded),
            )
            new_id = cur.fetchone()[0]
            cur.execute(
                """
                DELETE FROM repo_history
                WHERE id IN (
                    SELECT id
                    FROM repo_history
                    WHERE installation_id = %s AND repo_full_name = %s
                    ORDER BY scanned_at DESC, id DESC
                    OFFSET %s
                )
                """,
                (installation_id, repo_full_name, keep),
            )
        conn.commit()
    return new_id


def managed_audit_definitely_still_cooling_down(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    min_cooldown_seconds: int,
) -> bool:
    """Cheap, read-only, conservative pre-check for run_managed_audit_pr_job
    to run before cloning/scanning - the real cooldown is only known after
    a scan (it's derived from the evidence that scan produces, see
    app_server.rate_limit.cooldown_seconds_for_loc), so it can't be
    checked before doing that work. But every tier is at least
    min_cooldown_seconds, so a last run more recent than that is
    guaranteed to still be cooling down regardless of what the real
    duration turns out to be. False means "maybe allowed" (the real,
    authoritative check is check_and_reserve_managed_audit, after the
    scan) - this only ever turns away requests that would certainly have
    been rejected anyway, so it can't produce a false rejection.
    """
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM managed_audit_rate_limits
                WHERE installation_id = %s AND repo_full_name = %s
                  AND last_run_at > now() - %s * interval '1 second'
                """,
                (installation_id, repo_full_name, min_cooldown_seconds),
            )
            return cur.fetchone() is not None


def check_and_reserve_managed_audit(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    cooldown_seconds: int,
) -> bool:
    # Mirrors app_server.db.check_and_reserve_managed_audit's atomic
    # INSERT .. ON CONFLICT .. WHERE - the RETURNING row only appears when the
    # cooldown has actually elapsed, so a single round trip both checks and
    # records the attempt with no race window for concurrent callers.
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO managed_audit_rate_limits (installation_id, repo_full_name, last_run_at)
                VALUES (%s, %s, now())
                ON CONFLICT (installation_id, repo_full_name) DO UPDATE
                SET last_run_at = EXCLUDED.last_run_at
                WHERE managed_audit_rate_limits.last_run_at <= now() - %s * interval '1 second'
                RETURNING last_run_at
                """,
                (installation_id, repo_full_name, cooldown_seconds),
            )
            row = cur.fetchone()
        conn.commit()
    return row is not None


def check_and_reserve_monthly_repo_scan_slot(
    dsn: str, installation_id: int, repo_full_name: str, limit: int
) -> bool:
    """True if repo_full_name may be scanned this calendar month - either
    it's already one of this installation's counted repos this month, or
    there's still room under `limit` distinct repos and a slot gets
    reserved for it now. False means the monthly distinct-repo cap has
    already been reached by other repos, so this (new) repo must wait
    for next month.

    This is a real cost-control gate shared by every scan type (PR scan,
    Flash review, managed audit), reachable both from this single
    sequential scan-worker process and, via the managed-audit API's own
    HTTP-concurrent path (see app_server.db's async mirror of this
    function), from genuinely concurrent callers - so the check-then-insert
    is wrapped in a per-installation advisory lock rather than left as a
    racy read-then-write, matching check_and_reserve_managed_audit's
    atomicity elsewhere in this module.
    """
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('lock_timeout', %s, true)", (ADVISORY_LOCK_TIMEOUT,))
            # Namespace 1 is reserved for monthly scan-slot reservations.
            cur.execute(
                "SELECT pg_advisory_xact_lock(%s, %s)",
                (SCAN_SLOT_LOCK_NAMESPACE, installation_id),
            )
            cur.execute(
                """
                SELECT 1 FROM monthly_scanned_repos
                WHERE installation_id = %s AND repo_full_name = %s
                  AND month = date_trunc('month', now())::date
                """,
                (installation_id, repo_full_name),
            )
            if cur.fetchone() is not None:
                conn.commit()
                return True

            cur.execute(
                """
                SELECT COUNT(*) FROM monthly_scanned_repos
                WHERE installation_id = %s AND month = date_trunc('month', now())::date
                """,
                (installation_id,),
            )
            if cur.fetchone()[0] >= limit:
                conn.commit()
                return False

            cur.execute(
                """
                INSERT INTO monthly_scanned_repos (installation_id, repo_full_name, month)
                VALUES (%s, %s, date_trunc('month', now())::date)
                ON CONFLICT (installation_id, repo_full_name, month) DO NOTHING
                """,
                (installation_id, repo_full_name),
            )
        conn.commit()
    return True


def get_llm_spend_this_month(dsn: str, installation_id: int) -> float:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT total_cost_usd FROM llm_spend
                WHERE installation_id = %s AND month = date_trunc('month', now())::date
                """,
                (installation_id,),
            )
            row = cur.fetchone()
            return float(row[0]) if row else 0.0


def record_llm_spend(
    dsn: str,
    installation_id: int,
    cost_usd: float,
    monthly_cap: float | None = None,
    feature: str = "unknown",
) -> None:
    """monthly_cap: when given, logs a one-time warning if this call is the
    one that pushes the installation's spend this month past
    WARN_FRACTION_OF_CAP of it - see llm_cost.crossed_spend_warning_threshold.
    Omit it (as existing callers that predate this did) to skip the check
    entirely; it has no effect on what gets recorded.

    feature: which surface this spend came from (e.g. "flash_review",
    "managed_audit", "airview_full_build", "docs_incremental") - llm_spend
    itself only stores one aggregate total per (installation_id, month), so
    without this logged breakdown there is no way to later reconstruct
    which feature is actually driving an installation's spend. Every
    caller should pass a real label; "unknown" exists only so this doesn't
    hard-fail if a future call site forgets to set it."""
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO llm_spend (installation_id, month, total_cost_usd)
                VALUES (%s, date_trunc('month', now())::date, %s)
                ON CONFLICT (installation_id, month) DO UPDATE
                SET total_cost_usd = llm_spend.total_cost_usd + EXCLUDED.total_cost_usd
                RETURNING total_cost_usd
                """,
                (installation_id, cost_usd),
            )
            row = cur.fetchone()
        conn.commit()

    if cost_usd > 0:
        logger.info(
            "llm_spend: installation=%s feature=%s cost_usd=%.4f",
            installation_id, feature, cost_usd,
        )

    if monthly_cap is not None and row is not None:
        new_total = float(row[0])
        previous_total = new_total - cost_usd
        if crossed_spend_warning_threshold(previous_total, new_total, monthly_cap):
            logger.warning(
                "llm spend crossed %.0f%% of monthly cap: installation=%s $%.2f of $%.2f",
                WARN_FRACTION_OF_CAP * 100,
                installation_id,
                new_total,
                monthly_cap,
            )


def get_flash_review_count_this_month(dsn: str, installation_id: int) -> int:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT review_count FROM flash_review_monthly_count
                WHERE installation_id = %s AND month = date_trunc('month', now())::date
                """,
                (installation_id,),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0


def reserve_flash_review_count(dsn: str, installation_id: int, limit: int) -> bool:
    """Atomically checks the review-count cap and reserves a slot in one
    statement, the same INSERT...ON CONFLICT...WHERE...RETURNING shape as
    check_and_reserve_flash_review_attempt below. installation_spend_lock's
    two-phase check-then-later-increment used to leave a real window open:
    two concurrent reviews for the same installation could each read the
    count as under-cap before either recorded an attempt, overshooting
    `limit`. This closes it completely rather than narrowing it - Postgres
    serializes concurrent UPSERTs on the same (installation_id, month) row
    via its own row-level lock, so the second caller's WHERE clause always
    evaluates against the first's already-applied increment. No advisory
    lock needed.

    Returns True (and increments) if a slot was available, False (no
    increment - nothing to undo) if the cap was already reached. Call
    release_flash_review_count_reservation if the reserved review then
    never actually runs (e.g. every free-tier provider failed, or an
    unrelated exception aborted the job before it produced a result)."""
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO flash_review_monthly_count (installation_id, month, review_count)
                VALUES (%s, date_trunc('month', now())::date, 1)
                ON CONFLICT (installation_id, month) DO UPDATE
                SET review_count = flash_review_monthly_count.review_count + 1
                WHERE flash_review_monthly_count.review_count < %s
                RETURNING review_count
                """,
                (installation_id, limit),
            )
            row = cur.fetchone()
        conn.commit()
    return row is not None


def release_flash_review_count_reservation(dsn: str, installation_id: int) -> None:
    """Undoes one reserve_flash_review_count reservation for a review that
    was counted against the cap but never actually ran. GREATEST(...,0)
    guards against a double-release ever taking the count negative."""
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE flash_review_monthly_count
                SET review_count = GREATEST(review_count - 1, 0)
                WHERE installation_id = %s AND month = date_trunc('month', now())::date
                """,
                (installation_id,),
            )
        conn.commit()


def reserve_llm_spend(dsn: str, installation_id: int, reserve_usd: float, monthly_cap: float) -> bool:
    """Same atomic reserve-before-spend pattern as
    reserve_flash_review_count, for the dollar cap - reserves a
    conservative flat estimate (see jobs.py's FLASH_REVIEW_SPEND_RESERVE_USD)
    up front, since the real cost of a Flash Review isn't known until the
    LLM call and grounding pass complete. record_llm_spend's own additive
    upsert then trues the reservation up to the real cost afterward (pass
    `real_cost - reserve_usd` as the delta - negative if the real cost came
    in under the reservation, which is the common case).

    Note: on the very first reservation of a new month (no llm_spend row
    yet for this installation), the ON CONFLICT...WHERE clause only gates
    the UPDATE branch, not the INSERT branch, so that first reservation
    always succeeds regardless of monthly_cap. This matches the pre-existing
    check-then-act behavior, which also couldn't reject a single review
    whose cost alone would exceed the cap - not a new gap, and not reachable
    in practice since reserve_usd is always small relative to any real
    monthly_cap.

    Returns True (and reserves) if under cap, False (no reservation) if
    already at/over it. Call release_llm_spend_reservation if the review
    then never actually runs."""
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO llm_spend (installation_id, month, total_cost_usd)
                VALUES (%s, date_trunc('month', now())::date, %s)
                ON CONFLICT (installation_id, month) DO UPDATE
                SET total_cost_usd = llm_spend.total_cost_usd + %s
                WHERE llm_spend.total_cost_usd + %s <= %s
                RETURNING total_cost_usd
                """,
                (installation_id, reserve_usd, reserve_usd, reserve_usd, monthly_cap),
            )
            row = cur.fetchone()
        conn.commit()
    return row is not None


def release_llm_spend_reservation(dsn: str, installation_id: int, reserve_usd: float) -> None:
    """Undoes one reserve_llm_spend reservation for a review that was
    charged against the cap but never actually ran."""
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE llm_spend
                SET total_cost_usd = GREATEST(total_cost_usd - %s, 0)
                WHERE installation_id = %s AND month = date_trunc('month', now())::date
                """,
                (reserve_usd, installation_id),
            )
        conn.commit()


def insert_audit_report(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    verification_token: str,
    report_text: str,
    content_hash: str,
    signature: str,
    signing_public_key: str,
) -> None:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_reports
                    (installation_id, repo_full_name, verification_token, report_text,
                     content_hash, signature, signing_public_key)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    installation_id,
                    repo_full_name,
                    verification_token,
                    report_text,
                    content_hash,
                    signature,
                    signing_public_key,
                ),
            )
        conn.commit()


def get_extra_seats(dsn: str, installation_id: int) -> int:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT extra_seats FROM installations WHERE installation_id = %s",
                (installation_id,),
            )
            row = cur.fetchone()
            return row[0] if row else 0


@contextmanager
def installation_spend_lock(dsn: str, installation_id: int):
    # A single scan-worker process handles jobs sequentially today, so the
    # check-then-record spend cap is accidentally safe. This advisory lock
    # makes that safety explicit: it serializes the check/run/record cycle
    # per installation so scaling scan-worker to multiple replicas later
    # can't let concurrent jobs for the same installation both pass the
    # cap check before either has recorded its cost.
    conn = psycopg.connect(dsn, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('lock_timeout', %s, false)", (ADVISORY_LOCK_TIMEOUT,))
            # Namespace 2 is reserved for the session-scoped LLM spend lock.
            # Retries a transient LockNotAvailable rather than failing the
            # whole job on the first attempt. Confirmed in production that
            # every observed timeout here fell inside a Postgres checkpoint
            # write window (60-160s, ~5min apart) - a genuinely transient
            # condition, not another job holding this lock too long (that
            # was a separate, now-fixed bug - see run_flash_review_job's
            # comment). Each attempt already blocks up to
            # ADVISORY_LOCK_TIMEOUT waiting for the lock, so this doesn't
            # change behavior when the lock is actually contended by another
            # job - only when the acquisition itself is being slowed by
            # unrelated DB I/O pressure.
            for attempt in range(1, INSTALLATION_SPEND_LOCK_MAX_ATTEMPTS + 1):
                try:
                    cur.execute(
                        "SELECT pg_advisory_lock(%s, %s)",
                        (SPEND_LOCK_NAMESPACE, installation_id),
                    )
                    break
                except psycopg.errors.LockNotAvailable:
                    if attempt == INSTALLATION_SPEND_LOCK_MAX_ATTEMPTS:
                        raise
                    time.sleep(INSTALLATION_SPEND_LOCK_RETRY_DELAY_SECONDS)
        yield
    finally:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_unlock(%s, %s)",
                (SPEND_LOCK_NAMESPACE, installation_id),
            )
        conn.close()


@contextmanager
def repo_checkout_lock(dsn: str, installation_id: int, repo_full_name: str):
    """Serializes concurrent scan-worker replicas' use of one repo's
    persistent, reused-across-scans checkout (see _ensure_persistent_checkout
    in jobs.py), which has no filesystem-level locking of its own: two
    replicas racing `git checkout -f`/`git clean -fdx` against the same
    working tree can corrupt it, and racing `git remote set-url` (which
    briefly writes a live access token into .git/config, then resets it
    back) can leave one replica's fetch using the other's credentials or
    a URL with no credentials at all.

    Deliberately blocking (no lock_timeout, unlike the quick check-then-write
    locks above) - a second job for the same repo should wait its turn and
    still run once the first finishes, not fail fast and drop a real PR
    scan or push reconciliation. A crashed holder releases automatically:
    Postgres advisory locks are tied to the session/connection, which
    always closes (killed or not) before the lock could leak. Different
    repos, and different installations, are completely unaffected and run
    in true parallel across replicas - this only narrows the pre-existing
    single-worker-wide serialization down to "same repo only".
    """
    key = f"{installation_id}:{repo_full_name}"
    conn = psycopg.connect(dsn, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_lock(%s, hashtext(%s))",
                (REPO_CHECKOUT_LOCK_NAMESPACE, key),
            )
        yield
    finally:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_unlock(%s, hashtext(%s))",
                (REPO_CHECKOUT_LOCK_NAMESPACE, key),
            )
        conn.close()


def check_and_reserve_flash_review_attempt(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    pr_number: int,
    debounce_seconds: int = 120,
) -> bool:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO flash_review_state
                    (installation_id, repo_full_name, pr_number, last_attempted_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (installation_id, repo_full_name, pr_number) DO UPDATE
                SET last_attempted_at = EXCLUDED.last_attempted_at
                WHERE flash_review_state.last_attempted_at <= now() - %s * interval '1 second'
                RETURNING last_attempted_at
                """,
                (installation_id, repo_full_name, pr_number, debounce_seconds),
            )
            row = cur.fetchone()
        conn.commit()
    return row is not None


def get_last_reviewed_sha(
    dsn: str, installation_id: int, repo_full_name: str, pr_number: int
) -> str | None:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT last_reviewed_sha FROM flash_review_state
                WHERE installation_id = %s AND repo_full_name = %s AND pr_number = %s
                """,
                (installation_id, repo_full_name, pr_number),
            )
            row = cur.fetchone()
            return row[0] if row and row[0] else None


def set_last_reviewed_sha(
    dsn: str, installation_id: int, repo_full_name: str, pr_number: int, sha: str
) -> None:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE flash_review_state SET last_reviewed_sha = %s
                WHERE installation_id = %s AND repo_full_name = %s AND pr_number = %s
                """,
                (sha, installation_id, repo_full_name, pr_number),
            )
        conn.commit()


def get_installation(dsn: str, installation_id: int) -> dict | None:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT installation_id, account_login, plan, webhook_url, alert_email,
                       pushover_user_key, health_check_base_url,
                       health_check_latency_threshold_ms, llm_suggestions_enabled
                FROM installations
                WHERE installation_id = %s
                """,
                (installation_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            columns = [description[0] for description in cur.description]
            return dict(zip(columns, row))


def get_dismissed_identity_keys(dsn: str, installation_id: int, repo_full_name: str) -> dict[str, set[str]]:
    """Sync counterpart to app_server/dismissed_findings.py's async version
    of the same read, for use in RQ job code (which runs synchronously, not
    on the app_server's asyncpg pool). Used by the PR-scan job to filter
    already-dismissed findings out of a diff before posting a PR comment -
    see app_server/dismissed_findings.py's filter_dismissed()."""
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT finding_type, identity_key FROM dismissed_findings
                WHERE installation_id = %s AND repo_full_name = %s
                """,
                (installation_id, repo_full_name),
            )
            result: dict[str, set[str]] = {"secret": set(), "vulnerability": set()}
            for finding_type, identity_key in cur.fetchall():
                result[finding_type].add(identity_key)
            return result


def list_health_check_targets_all(dsn: str) -> list[dict]:
    """Every configured health check target across every paid installation -
    the health sweep job's worklist. One row per target, not per
    installation, since an installation's repos can each have their own
    monitored URL(s) now instead of a single shared one.
    """
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT t.id AS target_id, t.installation_id, t.repo_full_name, t.label,
                       t.base_url, t.latency_threshold_ms, i.webhook_url, i.alert_email,
                       i.pushover_user_key
                FROM health_check_targets t
                JOIN installations i ON i.installation_id = t.installation_id
                LEFT JOIN hidden_repos hr
                    ON hr.installation_id = t.installation_id
                   AND hr.repo_full_name = t.repo_full_name
                WHERE i.plan != 'free'
                  AND hr.installation_id IS NULL
                """
            )
            columns = [description[0] for description in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]


def list_repos_for_installation(dsn: str, installation_id: int) -> list[str]:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT repo_full_name FROM repo_history WHERE installation_id = %s",
                (installation_id,),
            )
            return [row[0] for row in cur.fetchall()]


def _version_gated_evidence(
    installation_id: int, repo_full_name: str, raw: object
) -> dict | None:
    """Shared by get_latest_evidence and get_evidence_by_id.

    repo_history rows outlive the schema that wrote them. The CLI, MCP
    server and dashboard all version-check evidence before reading it; this
    path did not, so after an EVIDENCE_VERSION bump every consumer here
    (AIRview, Flash review, health checks - 5+ call sites) would keep
    reading old-shaped rows as if current, and KeyError the moment new code
    indexed a key the old shape lacks. That is exactly the silent drift
    AIR-SCHEMA.md's migration rules describe.

    Treated as "no evidence yet" rather than raising: every caller already
    handles None (it is the normal never-scanned-yet case) and the next
    scan overwrites the row anyway, so a stale row costs one skipped
    enrichment rather than a failed job.
    """
    evidence = json.loads(raw) if isinstance(raw, str) else raw
    if not is_evidence_version_compatible(
        evidence.get("aletheore_version") if isinstance(evidence, dict) else None
    ):
        logging.getLogger("scan_worker.db").info(
            "ignoring stored evidence for installation=%s repo=%s - written by "
            "aletheore_version=%r, incompatible with this build; awaiting re-scan",
            installation_id,
            repo_full_name,
            evidence.get("aletheore_version") if isinstance(evidence, dict) else None,
        )
        return None
    return evidence


def get_latest_evidence(dsn: str, installation_id: int, repo_full_name: str) -> dict | None:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT evidence
                FROM repo_history
                WHERE installation_id = %s AND repo_full_name = %s
                ORDER BY scanned_at DESC, id DESC
                LIMIT 1
                """,
                (installation_id, repo_full_name),
            )
            row = cur.fetchone()
            if row is None:
                return None
    return _version_gated_evidence(installation_id, repo_full_name, row[0])


def get_evidence_by_id(
    dsn: str, installation_id: int, repo_full_name: str, history_id: int
) -> dict | None:
    """Fetch the exact evidence row a scan persisted, not whatever is
    currently latest for this repo.

    Exists so a queued follow-up job (e.g. a live-wiki/docs incremental
    update enqueued separately from the scan that computed its evidence -
    see run_live_wiki_incremental_update_job) can reload the specific
    evidence that scan produced, rather than get_latest_evidence's
    "whatever is newest right now." Without this, a second scan for the
    same repo persisting before the queued job runs would make it combine
    that newer evidence with the older scan's changed_files/head_sha -
    applying an incremental update against a mismatched revision. Scoped
    by installation_id and repo_full_name in addition to history_id (not
    just the id) so a caller can never read another installation's row
    even if history_id were somehow wrong.
    """
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT evidence
                FROM repo_history
                WHERE id = %s AND installation_id = %s AND repo_full_name = %s
                """,
                (history_id, installation_id, repo_full_name),
            )
            row = cur.fetchone()
            if row is None:
                return None
    return _version_gated_evidence(installation_id, repo_full_name, row[0])


def get_last_endpoint_health(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    method: str,
    path: str,
    target_id: int | None = None,
) -> dict | None:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT reachable, status_code, latency_ms, response_shape, checked_at
                FROM endpoint_health
                WHERE installation_id = %s
                  AND repo_full_name = %s
                  AND endpoint_method = %s
                  AND endpoint_path = %s
                  AND target_id IS NOT DISTINCT FROM %s
                ORDER BY checked_at DESC, id DESC
                LIMIT 1
                """,
                (installation_id, repo_full_name, method, path, target_id),
            )
            row = cur.fetchone()
            if row is None:
                return None
            columns = [description[0] for description in cur.description]
            result = dict(zip(columns, row))
            if result["latency_ms"] is not None:
                result["latency_ms"] = float(result["latency_ms"])
            return result


def list_recent_endpoint_incidents(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    since: datetime,
) -> list[dict]:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                """
                SELECT endpoint_method, endpoint_path, count(*) AS incident_count, max(checked_at) AS last_incident_at
                FROM endpoint_health
                WHERE installation_id = %s AND repo_full_name = %s AND reachable = false AND checked_at >= %s
                GROUP BY endpoint_method, endpoint_path
                """,
                (installation_id, repo_full_name, since),
            )
            return cur.fetchall()


def insert_endpoint_health(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    method: str,
    path: str,
    reachable: bool,
    status_code: int | None,
    latency_ms: float | None,
    response_shape: list[str] | None = None,
    target_id: int | None = None,
    keep: int = 20,
) -> None:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO endpoint_health
                    (installation_id, repo_full_name, endpoint_method, endpoint_path,
                     reachable, status_code, latency_ms, response_shape, target_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    installation_id,
                    repo_full_name,
                    method,
                    path,
                    reachable,
                    status_code,
                    latency_ms,
                    response_shape,
                    target_id,
                ),
            )
            cur.execute(
                """
                DELETE FROM endpoint_health
                WHERE id IN (
                    SELECT id
                    FROM endpoint_health
                    WHERE installation_id = %s
                      AND repo_full_name = %s
                      AND endpoint_method = %s
                      AND endpoint_path = %s
                      AND target_id IS NOT DISTINCT FROM %s
                    ORDER BY checked_at DESC, id DESC
                    OFFSET %s
                )
                """,
                (installation_id, repo_full_name, method, path, target_id, keep),
            )
        conn.commit()


def delete_expired_webhook_deliveries(dsn: str, retention_days: int) -> int:
    """Drop delivery GUIDs older than the retention window.

    The window has to outlive GitHub's own redelivery horizon, or an
    operator redelivering an old event - or an attacker replaying a captured
    payload - would find the ledger already swept and the delivery treated
    as new. GitHub keeps delivery logs for roughly 30 days, so the default
    matches that rather than the ~3-day automatic retry window.
    """
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM webhook_deliveries WHERE received_at < now() - make_interval(days => %s)",
                (retention_days,),
            )
            deleted = cur.rowcount
        conn.commit()
    return deleted


def delete_expired_sessions(dsn: str) -> int:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sessions WHERE expires_at < now()")
            deleted = cur.rowcount
        conn.commit()
    return deleted


def delete_expired_endpoint_health(dsn: str, retention_days: int = 30) -> int:
    """Bound endpoint-health history after the public and dashboard windows."""
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM endpoint_health "
                "WHERE checked_at < now() - make_interval(days => %s)",
                (retention_days,),
            )
            deleted = cur.rowcount
        conn.commit()
    return deleted


def upsert_wiki_overview(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    description: str,
    diagram_mermaid: str,
    source_commit: str | None = None,
) -> None:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO wiki_overview
                    (installation_id, repo_full_name, description, diagram_mermaid, source_commit, updated_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (installation_id, repo_full_name) DO UPDATE
                SET description = EXCLUDED.description,
                    diagram_mermaid = EXCLUDED.diagram_mermaid,
                    source_commit = EXCLUDED.source_commit,
                    updated_at = now()
                """,
                (installation_id, repo_full_name, description, diagram_mermaid, source_commit),
            )
        conn.commit()


def get_wiki_overview(dsn: str, installation_id: int, repo_full_name: str) -> dict | None:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                """
                SELECT description, diagram_mermaid, source_commit, updated_at
                FROM wiki_overview
                WHERE installation_id = %s AND repo_full_name = %s
                """,
                (installation_id, repo_full_name),
            )
            return cur.fetchone()


def upsert_wiki_subsystem(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    subsystem_id: str,
    name: str,
    description: str,
    files: list,
    diagram_mermaid: str,
    source_commit: str | None = None,
) -> None:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO wiki_subsystems
                    (installation_id, repo_full_name, subsystem_id, name, description,
                     files, diagram_mermaid, source_commit, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, now())
                ON CONFLICT (installation_id, repo_full_name, subsystem_id) DO UPDATE
                SET name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    files = EXCLUDED.files,
                    diagram_mermaid = EXCLUDED.diagram_mermaid,
                    source_commit = EXCLUDED.source_commit,
                    updated_at = now()
                """,
                (
                    installation_id,
                    repo_full_name,
                    subsystem_id,
                    name,
                    description,
                    json.dumps(files),
                    diagram_mermaid,
                    source_commit,
                ),
            )
        conn.commit()


def list_wiki_subsystems(dsn: str, installation_id: int, repo_full_name: str) -> list[dict]:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                """
                SELECT subsystem_id, name, description, files, diagram_mermaid, source_commit, updated_at
                FROM wiki_subsystems
                WHERE installation_id = %s AND repo_full_name = %s
                ORDER BY name ASC
                """,
                (installation_id, repo_full_name),
            )
            return cur.fetchall()


def delete_wiki_subsystems_not_in(
    dsn: str, installation_id: int, repo_full_name: str, keep_subsystem_ids: list[str]
) -> None:
    """Removes subsystem pages whose cluster no longer exists (e.g. it was
    merged into another cluster, or its files were deleted). Passing an
    empty keep list removes every subsystem page for the repo.
    """
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM wiki_subsystems
                WHERE installation_id = %s AND repo_full_name = %s
                  AND NOT (subsystem_id = ANY(%s::text[]))
                """,
                (installation_id, repo_full_name, keep_subsystem_ids),
            )
        conn.commit()


def set_wiki_build_status(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    status: str,
    error_message: str | None = None,
) -> None:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO wiki_build_status
                    (installation_id, repo_full_name, status, error_message, updated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (installation_id, repo_full_name) DO UPDATE
                SET status = EXCLUDED.status,
                    error_message = EXCLUDED.error_message,
                    updated_at = now()
                """,
                (installation_id, repo_full_name, status, error_message),
            )
        conn.commit()


def upsert_docs_symbol(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    module_path: str,
    symbol_name: str,
    description: str,
    mode: str,
    source_commit: str | None = None,
    content_hash: str | None = None,
) -> None:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO docs_symbols
                    (installation_id, repo_full_name, module_path, symbol_name, description,
                     mode, source_commit, content_hash, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (installation_id, repo_full_name, module_path, symbol_name) DO UPDATE
                SET description = EXCLUDED.description,
                    mode = EXCLUDED.mode,
                    source_commit = EXCLUDED.source_commit,
                    content_hash = EXCLUDED.content_hash,
                    updated_at = now()
                """,
                (
                    installation_id,
                    repo_full_name,
                    module_path,
                    symbol_name,
                    description,
                    mode,
                    source_commit,
                    content_hash,
                ),
            )
        conn.commit()


def get_docs_symbol_hashes(
    dsn: str, installation_id: int, repo_full_name: str, module_path: str
) -> dict[str, str]:
    """symbol_name -> content_hash for one module's already-stored
    descriptions - lets a caller skip re-asking the LLM about a symbol
    whose source snippet hasn't changed since it was last described (see
    live_docs.generate_file_descriptions_combined's already_hashed param).
    Rows written before the content_hash column existed have a NULL hash
    and are naturally excluded, so old data just means "generate everything
    that module still needs" rather than a crash.
    """
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT symbol_name, content_hash FROM docs_symbols
                WHERE installation_id = %s AND repo_full_name = %s AND module_path = %s
                  AND content_hash IS NOT NULL
                """,
                (installation_id, repo_full_name, module_path),
            )
            return dict(cur.fetchall())


def list_docs_symbols(dsn: str, installation_id: int, repo_full_name: str) -> list[dict]:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                """
                SELECT module_path, symbol_name, description, mode, source_commit, updated_at
                FROM docs_symbols
                WHERE installation_id = %s AND repo_full_name = %s
                ORDER BY module_path ASC, symbol_name ASC
                """,
                (installation_id, repo_full_name),
            )
            return cur.fetchall()


def delete_docs_symbols_not_in(
    dsn: str, installation_id: int, repo_full_name: str, module_path: str, keep_symbol_names: list[str]
) -> None:
    """Removes stale symbol descriptions for one module - a symbol that was
    renamed, deleted, or gained a real docstring (so it no longer needs an
    AI-generated one) shouldn't leave its old generated text behind.
    Passing an empty keep list removes every description for that module.
    """
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM docs_symbols
                WHERE installation_id = %s AND repo_full_name = %s AND module_path = %s
                  AND NOT (symbol_name = ANY(%s::text[]))
                """,
                (installation_id, repo_full_name, module_path, keep_symbol_names),
            )
        conn.commit()


def set_docs_build_status(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    status: str,
    error_message: str | None = None,
) -> None:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO docs_build_status
                    (installation_id, repo_full_name, status, error_message, updated_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (installation_id, repo_full_name) DO UPDATE
                SET status = EXCLUDED.status,
                    error_message = EXCLUDED.error_message,
                    updated_at = now()
                """,
                (installation_id, repo_full_name, status, error_message),
            )
        conn.commit()


def get_docs_repo_commit_settings(dsn: str, installation_id: int, repo_full_name: str) -> dict | None:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                """
                SELECT enabled, last_content_hash, pr_number
                FROM docs_repo_commit_settings
                WHERE installation_id = %s AND repo_full_name = %s
                """,
                (installation_id, repo_full_name),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def record_docs_repo_commit(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    content_hash: str,
    pr_number: int,
) -> None:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO docs_repo_commit_settings
                    (installation_id, repo_full_name, enabled, last_content_hash, pr_number, updated_at)
                VALUES (%s, %s, true, %s, %s, now())
                ON CONFLICT (installation_id, repo_full_name) DO UPDATE
                SET last_content_hash = EXCLUDED.last_content_hash,
                    pr_number = EXCLUDED.pr_number,
                    updated_at = now()
                """,
                (installation_id, repo_full_name, content_hash, pr_number),
            )
        conn.commit()


def list_paid_repos_due_for_docs_catchup(dsn: str, interval_seconds: int) -> list[tuple[int, str]]:
    """Paid-plan repos due for the recurring Docs catch-up sweep - never
    swept before, or swept more than interval_seconds ago AND scanned at
    least once since that last sweep. The activity requirement (a real
    scan since the last sweep, not just "installation is still paid") is
    what keeps a dormant repo with no new commits from repeatedly costing
    real LLM spend every 48h for zero new information - nothing changed,
    so there's nothing new to describe.
    """
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor(row_factory=psycopg.rows.tuple_row) as cur:
            cur.execute(
                """
                SELECT DISTINCT rh.installation_id, rh.repo_full_name
                FROM repo_history rh
                JOIN installations i ON i.installation_id = rh.installation_id
                LEFT JOIN docs_catchup_sweeps s
                    ON s.installation_id = rh.installation_id
                   AND s.repo_full_name = rh.repo_full_name
                LEFT JOIN hidden_repos hr
                    ON hr.installation_id = rh.installation_id
                   AND hr.repo_full_name = rh.repo_full_name
                WHERE i.plan != 'free'
                  AND hr.installation_id IS NULL
                  AND (
                        s.last_swept_at IS NULL
                        OR (
                            rh.scanned_at > s.last_swept_at
                            AND s.last_swept_at <= now() - make_interval(secs => %s)
                        )
                  )
                """,
                (interval_seconds,),
            )
            return [(row[0], row[1]) for row in cur.fetchall()]


def record_docs_catchup_swept(dsn: str, installation_id: int, repo_full_name: str) -> None:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO docs_catchup_sweeps (installation_id, repo_full_name, last_swept_at)
                VALUES (%s, %s, now())
                ON CONFLICT (installation_id, repo_full_name) DO UPDATE SET last_swept_at = now()
                """,
                (installation_id, repo_full_name),
            )
        conn.commit()


def list_paid_repos_due_for_wiki_catchup(dsn: str, interval_seconds: int) -> list[tuple[int, str]]:
    """Paid-plan repos due for the recurring AIRview catch-up sweep - mirrors
    list_paid_repos_due_for_docs_catchup exactly (never swept before, or
    swept more than interval_seconds ago AND scanned at least once since
    that last sweep), against wiki_catchup_sweeps instead of
    docs_catchup_sweeps.
    """
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor(row_factory=psycopg.rows.tuple_row) as cur:
            cur.execute(
                """
                SELECT DISTINCT rh.installation_id, rh.repo_full_name
                FROM repo_history rh
                JOIN installations i ON i.installation_id = rh.installation_id
                LEFT JOIN wiki_catchup_sweeps s
                    ON s.installation_id = rh.installation_id
                   AND s.repo_full_name = rh.repo_full_name
                LEFT JOIN hidden_repos hr
                    ON hr.installation_id = rh.installation_id
                   AND hr.repo_full_name = rh.repo_full_name
                WHERE i.plan != 'free'
                  AND hr.installation_id IS NULL
                  AND (
                        s.last_swept_at IS NULL
                        OR (
                            rh.scanned_at > s.last_swept_at
                            AND s.last_swept_at <= now() - make_interval(secs => %s)
                        )
                  )
                """,
                (interval_seconds,),
            )
            return [(row[0], row[1]) for row in cur.fetchall()]


def record_wiki_catchup_swept(dsn: str, installation_id: int, repo_full_name: str) -> None:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO wiki_catchup_sweeps (installation_id, repo_full_name, last_swept_at)
                VALUES (%s, %s, now())
                ON CONFLICT (installation_id, repo_full_name) DO UPDATE SET last_swept_at = now()
                """,
                (installation_id, repo_full_name),
            )
        conn.commit()


def insert_evidence_packet_cache_row(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    content_hash: str,
    embedding: list[float],
    packet: dict,
    model_output: dict,
    model_used: str,
    embedder: str,
) -> None:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO evidence_packet_cache
                    (installation_id, repo_full_name, content_hash, embedding,
                     packet_json, model_output, model_used, embedder)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
                """,
                (
                    installation_id,
                    repo_full_name,
                    content_hash,
                    embedding,
                    json.dumps(packet),
                    json.dumps(model_output),
                    model_used,
                    embedder,
                ),
            )
        conn.commit()


def list_recent_evidence_packet_cache_rows(
    dsn: str, installation_id: int, repo_full_name: str, embedder: str, limit: int = 200
) -> list[dict]:
    # Filtered to the currently-configured embedder at the SQL level, not
    # in Python after fetching: a row written under a different embedder
    # (an old row from before a switch, or one written mid-rollout) is a
    # different embedding space entirely, not just a lower-quality match -
    # see before_launch_fixes.md Batch 5 finding 8. NULL (every row from
    # before this column existed) never equals embedder in SQL, so those
    # age out the same way, without a separate migration to purge them.
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                """
                SELECT id, content_hash, embedding, packet_json, model_output, model_used, hit_count
                FROM evidence_packet_cache
                WHERE installation_id = %s AND repo_full_name = %s AND embedder = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (installation_id, repo_full_name, embedder, limit),
            )
            return cur.fetchall()


def record_evidence_packet_cache_hit(dsn: str, row_id: int) -> None:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE evidence_packet_cache
                SET hit_count = hit_count + 1, last_hit_at = now()
                WHERE id = %s
                """,
                (row_id,),
            )
        conn.commit()


def insert_flash_review_cache_row(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    content_hash: str,
    embedding: list[float],
    diff_text: str,
    findings: list[dict],
    model_used: str,
    embedder: str,
) -> None:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO flash_review_cache
                    (installation_id, repo_full_name, content_hash, embedding,
                     diff_text, findings, model_used, embedder)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                """,
                (
                    installation_id,
                    repo_full_name,
                    content_hash,
                    embedding,
                    diff_text,
                    json.dumps(findings),
                    model_used,
                    embedder,
                ),
            )
        conn.commit()


def list_recent_flash_review_cache_rows(
    dsn: str, installation_id: int, repo_full_name: str, embedder: str, limit: int = 200
) -> list[dict]:
    # See list_recent_evidence_packet_cache_rows's comment - same
    # embedder-identity filter, same reasoning (Batch 5 finding 8).
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                """
                SELECT id, content_hash, embedding, diff_text, findings, model_used, hit_count
                FROM flash_review_cache
                WHERE installation_id = %s AND repo_full_name = %s AND embedder = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (installation_id, repo_full_name, embedder, limit),
            )
            return cur.fetchall()


def record_flash_review_cache_hit(dsn: str, row_id: int) -> None:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE flash_review_cache
                SET hit_count = hit_count + 1, last_hit_at = now()
                WHERE id = %s
                """,
                (row_id,),
            )
        conn.commit()


def delete_expired_flash_review_cache(dsn: str, retention_days: int = 30) -> int:
    """Bounds how long a real PR diff (source code, not derived evidence)
    sits in this table - previously unbounded, since the only thing
    limiting a lookup's read was list_recent_flash_review_cache_rows'
    LIMIT 200, which caps what one query returns, not what the table
    retains. A near-duplicate-diff hit also gets less useful the older the
    stored diff is, so this doesn't trade away the cache's actual purpose."""
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM flash_review_cache "
                "WHERE created_at < now() - make_interval(days => %s)",
                (retention_days,),
            )
            deleted = cur.rowcount
        conn.commit()
    return deleted


def email_already_sent(dsn: str, dedupe_key: str) -> bool:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM sent_emails WHERE dedupe_key = %s", (dedupe_key,))
            return cur.fetchone() is not None


def record_sent_email(
    dsn: str,
    dedupe_key: str,
    template_name: str,
    recipient: str,
    installation_id: int | None,
    resend_message_id: str | None,
) -> None:
    # Only ever called after a successful Resend call (see
    # send_transactional_email_job) - inserting this as a "claim" before
    # sending would let a transient send failure permanently block a
    # legitimate future retry, since dedupe_key is UNIQUE.
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sent_emails
                    (dedupe_key, template_name, recipient, installation_id, resend_message_id)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (dedupe_key) DO NOTHING
                """,
                (dedupe_key, template_name, recipient, installation_id, resend_message_id),
            )
        conn.commit()


def list_paid_installations_due_for_digest(dsn: str, interval_seconds: int) -> list[int]:
    """Paid installations due for the weekly usage digest - never sent
    before, or sent more than interval_seconds ago. Unlike the docs
    catch-up sweep, deliberately NOT gated on activity (see
    digest_sends' migration comment) - a quiet installation still gets a
    digest, just one that gently prompts re-engagement instead of listing
    numbers.
    """
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor(row_factory=psycopg.rows.tuple_row) as cur:
            cur.execute(
                """
                SELECT i.installation_id
                FROM installations i
                LEFT JOIN digest_sends d ON d.installation_id = i.installation_id
                WHERE i.plan != 'free'
                  AND (d.last_sent_at IS NULL OR d.last_sent_at <= now() - make_interval(secs => %s))
                """,
                (interval_seconds,),
            )
            return [row[0] for row in cur.fetchall()]


def record_digest_sent(dsn: str, installation_id: int) -> None:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO digest_sends (installation_id, last_sent_at)
                VALUES (%s, now())
                ON CONFLICT (installation_id) DO UPDATE SET last_sent_at = now()
                """,
                (installation_id,),
            )
        conn.commit()


def count_repo_scans_since(dsn: str, installation_id: int, since: datetime) -> int:
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM repo_history WHERE installation_id = %s AND scanned_at >= %s",
                (installation_id, since),
            )
            return cur.fetchone()[0]


def get_endpoint_health_summary(dsn: str, installation_id: int, stale_after_seconds: int = 900) -> dict:
    """Current live status - most-recent row per (method, path) within the
    same 15-minute staleness window as the public status API
    (dashboard.py's PUBLIC_HEALTH_STALE_AFTER), so the digest and the
    status page never disagree about what's currently "up".
    """
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (endpoint_method, endpoint_path) reachable
                FROM endpoint_health
                WHERE installation_id = %s AND checked_at >= now() - make_interval(secs => %s)
                ORDER BY endpoint_method, endpoint_path, checked_at DESC, id DESC
                """,
                (installation_id, stale_after_seconds),
            )
            rows = cur.fetchall()
            return {"total": len(rows), "reachable": sum(1 for (reachable,) in rows if reachable)}


def get_seconds_since_last_health_check(dsn: str) -> float | None:
    """Seconds since the most recent row landed in endpoint_health, across
    every installation and target - a global liveness signal for the
    health-check sweep mechanism itself (scan_worker.jobs.
    run_health_check_sweep_job), not any one customer's specific endpoint.
    Returns None if the table has no rows at all (a fresh install, not a
    failure - the caller should not alert on that).
    """
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT max(checked_at) FROM endpoint_health")
            row = cur.fetchone()
            last_checked_at = row[0] if row else None
            if last_checked_at is None:
                return None
            return (datetime.now(timezone.utc) - last_checked_at).total_seconds()


def list_installation_member_emails(dsn: str, installation_id: int) -> list[str]:
    """Sync (psycopg) counterpart to app_server.db.list_installation_member_emails
    (asyncpg) - scan_worker jobs run outside the event loop, so they can't
    share that pool. Same semantics: only members who've logged in at
    least once (and so have a row in github_user_emails) get an email.
    """
    with get_db_pool(dsn).connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.email
                FROM installation_members m
                JOIN github_user_emails e ON e.github_login = m.github_login
                WHERE m.installation_id = %s
                """,
                (installation_id,),
            )
            return [row[0] for row in cur.fetchall()]
