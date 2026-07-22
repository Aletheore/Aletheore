import json
from contextlib import contextmanager
from datetime import datetime

from app_server.evidence_limits import check_evidence_size


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


def record_llm_spend(dsn: str, installation_id: int, cost_usd: float) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO llm_spend (installation_id, month, total_cost_usd)
                VALUES (%s, date_trunc('month', now())::date, %s)
                ON CONFLICT (installation_id, month) DO UPDATE
                SET total_cost_usd = llm_spend.total_cost_usd + EXCLUDED.total_cost_usd
                """,
                (installation_id, cost_usd),
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
                       health_check_base_url, health_check_latency_threshold_ms
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


def list_monitored_installations(dsn: str) -> list[dict]:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT installation_id, health_check_base_url,
                       health_check_latency_threshold_ms, webhook_url
                FROM installations
                WHERE plan != 'free' AND health_check_base_url IS NOT NULL
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
) -> dict | None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT reachable, status_code, latency_ms, checked_at
                FROM endpoint_health
                WHERE installation_id = %s
                  AND repo_full_name = %s
                  AND endpoint_method = %s
                  AND endpoint_path = %s
                ORDER BY checked_at DESC, id DESC
                LIMIT 1
                """,
                (installation_id, repo_full_name, method, path),
            )
            row = cur.fetchone()
            if row is None:
                return None
            columns = [description[0] for description in cur.description]
            result = dict(zip(columns, row))
            if result["latency_ms"] is not None:
                result["latency_ms"] = float(result["latency_ms"])
            return result


def insert_endpoint_health(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    method: str,
    path: str,
    reachable: bool,
    status_code: int | None,
    latency_ms: float | None,
    keep: int = 20,
) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO endpoint_health
                    (installation_id, repo_full_name, endpoint_method, endpoint_path,
                     reachable, status_code, latency_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (installation_id, repo_full_name, method, path, reachable, status_code, latency_ms),
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
                    ORDER BY checked_at DESC, id DESC
                    OFFSET %s
                )
                """,
                (installation_id, repo_full_name, method, path, keep),
            )
        conn.commit()


def delete_expired_sessions(dsn: str) -> int:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sessions WHERE expires_at < now()")
            deleted = cur.rowcount
        conn.commit()
    return deleted
