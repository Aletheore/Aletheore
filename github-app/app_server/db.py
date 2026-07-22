import json
from datetime import datetime

import asyncpg

from app_server.evidence_limits import check_evidence_size


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
        SELECT installation_id, account_login, plan, webhook_url, max_api_tokens,
               health_check_base_url, health_check_latency_threshold_ms
        FROM installations
        WHERE installation_id = $1
        """,
        installation_id,
    )
    return dict(row) if row else None


async def set_installation_plan(pool: asyncpg.Pool, installation_id: int, plan: str) -> None:
    await pool.execute(
        "UPDATE installations SET plan = $2, updated_at = now() WHERE installation_id = $1",
        installation_id,
        plan,
    )


async def delete_installation(pool: asyncpg.Pool, installation_id: int) -> None:
    await pool.execute("DELETE FROM installations WHERE installation_id = $1", installation_id)


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


async def get_llm_spend_this_month(pool: asyncpg.Pool, installation_id: int) -> float:
    row = await pool.fetchrow(
        """
        SELECT total_cost_usd FROM llm_spend
        WHERE installation_id = $1 AND month = date_trunc('month', now())::date
        """,
        installation_id,
    )
    return float(row["total_cost_usd"]) if row else 0.0


async def record_llm_spend(pool: asyncpg.Pool, installation_id: int, cost_usd: float) -> None:
    await pool.execute(
        """
        INSERT INTO llm_spend (installation_id, month, total_cost_usd)
        VALUES ($1, date_trunc('month', now())::date, $2)
        ON CONFLICT (installation_id, month) DO UPDATE
        SET total_cost_usd = llm_spend.total_cost_usd + EXCLUDED.total_cost_usd
        """,
        installation_id,
        cost_usd,
    )


async def get_extra_seats(pool: asyncpg.Pool, installation_id: int) -> int:
    row = await pool.fetchrow(
        "SELECT extra_seats FROM installations WHERE installation_id = $1",
        installation_id,
    )
    return row["extra_seats"] if row else 0


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
    rows = await pool.fetch(
        """
        SELECT DISTINCT ON (endpoint_method, endpoint_path)
            endpoint_method, endpoint_path, reachable, status_code, latency_ms, checked_at
        FROM endpoint_health
        WHERE installation_id = $1 AND repo_full_name = $2
        ORDER BY endpoint_method, endpoint_path, checked_at DESC, id DESC
        """,
        installation_id,
        repo_full_name,
    )
    return [dict(row) for row in rows]


async def create_session(
    pool: asyncpg.Pool,
    session_id: str,
    github_user_id: int,
    github_login: str,
    access_token: str,
    expires_at: datetime,
) -> None:
    await pool.execute(
        """
        INSERT INTO sessions (id, github_user_id, github_login, github_access_token, expires_at)
        VALUES ($1, $2, $3, $4, $5)
        """,
        session_id,
        github_user_id,
        github_login,
        access_token,
        expires_at,
    )


async def get_session(pool: asyncpg.Pool, session_id: str) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT id, github_user_id, github_login, github_access_token, expires_at
        FROM sessions
        WHERE id = $1
        """,
        session_id,
    )
    return dict(row) if row else None


async def delete_session(pool: asyncpg.Pool, session_id: str) -> None:
    await pool.execute("DELETE FROM sessions WHERE id = $1", session_id)


async def set_webhook_url(pool: asyncpg.Pool, installation_id: int, url: str | None) -> None:
    await pool.execute(
        "UPDATE installations SET webhook_url = $2, updated_at = now() WHERE installation_id = $1",
        installation_id,
        url,
    )


async def set_health_check_config(
    pool: asyncpg.Pool,
    installation_id: int,
    base_url: str | None,
    threshold_ms: int | None,
) -> None:
    await pool.execute(
        """
        UPDATE installations
        SET health_check_base_url = $2,
            health_check_latency_threshold_ms = $3,
            updated_at = now()
        WHERE installation_id = $1
        """,
        installation_id,
        base_url,
        threshold_ms,
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
) -> None:
    await pool.execute(
        """
        INSERT INTO api_tokens (installation_id, token_hash, label, created_by_github_login)
        VALUES ($1, $2, $3, $4)
        """,
        installation_id,
        token_hash,
        label,
        created_by_github_login,
    )


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
