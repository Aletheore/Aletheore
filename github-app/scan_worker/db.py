import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone

from app_server.evidence_limits import check_evidence_size
from app_server.llm_cost import WARN_FRACTION_OF_CAP, crossed_spend_warning_threshold

logger = logging.getLogger(__name__)


def insert_repo_history(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    scanned_at: datetime,
    evidence: dict,
    keep: int = 20,
) -> None:
    import psycopg

    encoded = json.dumps(evidence)
    check_evidence_size(encoded)

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO repo_history (installation_id, repo_full_name, scanned_at, evidence)
                VALUES (%s, %s, %s, %s::jsonb)
                """,
                (installation_id, repo_full_name, scanned_at, encoded),
            )
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
    import psycopg

    with psycopg.connect(dsn) as conn:
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
    import psycopg

    # Mirrors app_server.db.check_and_reserve_managed_audit's atomic
    # INSERT .. ON CONFLICT .. WHERE - the RETURNING row only appears when the
    # cooldown has actually elapsed, so a single round trip both checks and
    # records the attempt with no race window for concurrent callers.
    with psycopg.connect(dsn) as conn:
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
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (installation_id,))
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
    import psycopg

    with psycopg.connect(dsn) as conn:
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
    dsn: str, installation_id: int, cost_usd: float, monthly_cap: float | None = None
) -> None:
    """monthly_cap: when given, logs a one-time warning if this call is the
    one that pushes the installation's spend this month past
    WARN_FRACTION_OF_CAP of it - see llm_cost.crossed_spend_warning_threshold.
    Omit it (as existing callers that predate this did) to skip the check
    entirely; it has no effect on what gets recorded."""
    import psycopg

    with psycopg.connect(dsn) as conn:
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
    import psycopg

    with psycopg.connect(dsn) as conn:
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


def increment_flash_review_count(dsn: str, installation_id: int) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO flash_review_monthly_count (installation_id, month, review_count)
                VALUES (%s, date_trunc('month', now())::date, 1)
                ON CONFLICT (installation_id, month) DO UPDATE
                SET review_count = flash_review_monthly_count.review_count + 1
                """,
                (installation_id,),
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
    import psycopg

    with psycopg.connect(dsn) as conn:
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
    import psycopg

    with psycopg.connect(dsn) as conn:
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
    import psycopg

    conn = psycopg.connect(dsn, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (installation_id,))
        yield
    finally:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (installation_id,))
        conn.close()


def check_and_reserve_flash_review_attempt(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    pr_number: int,
    debounce_seconds: int = 120,
) -> bool:
    import psycopg

    with psycopg.connect(dsn) as conn:
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
    import psycopg

    with psycopg.connect(dsn) as conn:
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
    import psycopg

    with psycopg.connect(dsn) as conn:
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
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT installation_id, account_login, plan, webhook_url,
                       health_check_base_url, health_check_latency_threshold_ms,
                       llm_suggestions_enabled
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
    import psycopg

    with psycopg.connect(dsn) as conn:
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
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT t.id AS target_id, t.installation_id, t.repo_full_name, t.label,
                       t.base_url, t.latency_threshold_ms, i.webhook_url
                FROM health_check_targets t
                JOIN installations i ON i.installation_id = t.installation_id
                WHERE i.plan != 'free'
                """
            )
            columns = [description[0] for description in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]


def list_repos_for_installation(dsn: str, installation_id: int) -> list[str]:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT repo_full_name FROM repo_history WHERE installation_id = %s",
                (installation_id,),
            )
            return [row[0] for row in cur.fetchall()]


def get_latest_evidence(dsn: str, installation_id: int, repo_full_name: str) -> dict | None:
    import psycopg

    with psycopg.connect(dsn) as conn:
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
            return json.loads(row[0]) if isinstance(row[0], str) else row[0]


def get_last_endpoint_health(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    method: str,
    path: str,
    target_id: int | None = None,
) -> dict | None:
    import psycopg

    with psycopg.connect(dsn) as conn:
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
    import psycopg
    import psycopg.rows

    with psycopg.connect(dsn) as conn:
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
    import psycopg

    with psycopg.connect(dsn) as conn:
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


def delete_expired_telemetry_events(dsn: str, retention_days: int) -> int:
    """Drop anonymous CLI telemetry older than the retention window.

    /v1/telemetry is unauthenticated, so without this the one table a stranger
    can write to grows without bound. The rows are aggregate usage counters
    with no per-user value once they age out - count_telemetry_events reports
    totals and unique machines, neither of which needs multi-year history.
    """
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM cli_telemetry_events "
                "WHERE occurred_at < now() - make_interval(days => %s)",
                (retention_days,),
            )
            deleted = cur.rowcount
        conn.commit()
    return deleted


def delete_expired_webhook_deliveries(dsn: str, retention_days: int) -> int:
    """Drop delivery GUIDs older than the retention window.

    The window has to outlive GitHub's own redelivery horizon, or an
    operator redelivering an old event - or an attacker replaying a captured
    payload - would find the ledger already swept and the delivery treated
    as new. GitHub keeps delivery logs for roughly 30 days, so the default
    matches that rather than the ~3-day automatic retry window.
    """
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM webhook_deliveries WHERE received_at < now() - make_interval(days => %s)",
                (retention_days,),
            )
            deleted = cur.rowcount
        conn.commit()
    return deleted


def delete_expired_sessions(dsn: str) -> int:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sessions WHERE expires_at < now()")
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
    import psycopg

    with psycopg.connect(dsn) as conn:
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
    import psycopg
    import psycopg.rows

    with psycopg.connect(dsn) as conn:
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
    import psycopg

    with psycopg.connect(dsn) as conn:
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
    import psycopg.rows

    with psycopg.connect(dsn) as conn:
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
    import psycopg

    with psycopg.connect(dsn) as conn:
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
    import psycopg

    with psycopg.connect(dsn) as conn:
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
) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO docs_symbols
                    (installation_id, repo_full_name, module_path, symbol_name, description,
                     mode, source_commit, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (installation_id, repo_full_name, module_path, symbol_name) DO UPDATE
                SET description = EXCLUDED.description,
                    mode = EXCLUDED.mode,
                    source_commit = EXCLUDED.source_commit,
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
                ),
            )
        conn.commit()


def list_docs_symbols(dsn: str, installation_id: int, repo_full_name: str) -> list[dict]:
    import psycopg.rows

    with psycopg.connect(dsn) as conn:
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
    import psycopg

    with psycopg.connect(dsn) as conn:
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
    import psycopg

    with psycopg.connect(dsn) as conn:
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
    import psycopg

    with psycopg.connect(dsn) as conn:
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
    import psycopg

    with psycopg.connect(dsn) as conn:
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
    import psycopg
    import psycopg.rows

    with psycopg.connect(dsn) as conn:
        with conn.cursor(row_factory=psycopg.rows.tuple_row) as cur:
            cur.execute(
                """
                SELECT DISTINCT rh.installation_id, rh.repo_full_name
                FROM repo_history rh
                JOIN installations i ON i.installation_id = rh.installation_id
                LEFT JOIN docs_catchup_sweeps s
                    ON s.installation_id = rh.installation_id
                   AND s.repo_full_name = rh.repo_full_name
                WHERE i.plan != 'free'
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
    import psycopg

    with psycopg.connect(dsn) as conn:
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
    import psycopg
    import psycopg.rows

    with psycopg.connect(dsn) as conn:
        with conn.cursor(row_factory=psycopg.rows.tuple_row) as cur:
            cur.execute(
                """
                SELECT DISTINCT rh.installation_id, rh.repo_full_name
                FROM repo_history rh
                JOIN installations i ON i.installation_id = rh.installation_id
                LEFT JOIN wiki_catchup_sweeps s
                    ON s.installation_id = rh.installation_id
                   AND s.repo_full_name = rh.repo_full_name
                WHERE i.plan != 'free'
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
    import psycopg

    with psycopg.connect(dsn) as conn:
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
) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO evidence_packet_cache
                    (installation_id, repo_full_name, content_hash, embedding,
                     packet_json, model_output, model_used)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                """,
                (
                    installation_id,
                    repo_full_name,
                    content_hash,
                    embedding,
                    json.dumps(packet),
                    json.dumps(model_output),
                    model_used,
                ),
            )
        conn.commit()


def list_recent_evidence_packet_cache_rows(
    dsn: str, installation_id: int, repo_full_name: str, limit: int = 200
) -> list[dict]:
    import psycopg.rows

    with psycopg.connect(dsn) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                """
                SELECT id, content_hash, embedding, packet_json, model_output, model_used, hit_count
                FROM evidence_packet_cache
                WHERE installation_id = %s AND repo_full_name = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (installation_id, repo_full_name, limit),
            )
            return cur.fetchall()


def record_evidence_packet_cache_hit(dsn: str, row_id: int) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn:
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
) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO flash_review_cache
                    (installation_id, repo_full_name, content_hash, embedding,
                     diff_text, findings, model_used)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                """,
                (
                    installation_id,
                    repo_full_name,
                    content_hash,
                    embedding,
                    diff_text,
                    json.dumps(findings),
                    model_used,
                ),
            )
        conn.commit()


def list_recent_flash_review_cache_rows(
    dsn: str, installation_id: int, repo_full_name: str, limit: int = 200
) -> list[dict]:
    import psycopg.rows

    with psycopg.connect(dsn) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                """
                SELECT id, content_hash, embedding, diff_text, findings, model_used, hit_count
                FROM flash_review_cache
                WHERE installation_id = %s AND repo_full_name = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (installation_id, repo_full_name, limit),
            )
            return cur.fetchall()


def record_flash_review_cache_hit(dsn: str, row_id: int) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn:
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


def email_already_sent(dsn: str, dedupe_key: str) -> bool:
    import psycopg

    with psycopg.connect(dsn) as conn:
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
    import psycopg

    with psycopg.connect(dsn) as conn:
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
    import psycopg
    import psycopg.rows

    with psycopg.connect(dsn) as conn:
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
    import psycopg

    with psycopg.connect(dsn) as conn:
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
    import psycopg

    with psycopg.connect(dsn) as conn:
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
    import psycopg

    with psycopg.connect(dsn) as conn:
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
    import psycopg

    with psycopg.connect(dsn) as conn:
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
    import psycopg

    with psycopg.connect(dsn) as conn:
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
