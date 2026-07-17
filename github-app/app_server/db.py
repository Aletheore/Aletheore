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
        SELECT installation_id, account_login, plan
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
