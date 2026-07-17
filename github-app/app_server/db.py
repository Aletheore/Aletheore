import json
from datetime import datetime

import asyncpg


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
        SELECT installation_id, account_login, plan, webhook_url, max_api_tokens
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
                json.dumps(evidence),
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
