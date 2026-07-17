import json
from datetime import datetime


def insert_repo_history(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    scanned_at: datetime,
    evidence: dict,
    keep: int = 20,
) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO repo_history (installation_id, repo_full_name, scanned_at, evidence)
                VALUES (%s, %s, %s, %s::jsonb)
                """,
                (installation_id, repo_full_name, scanned_at, json.dumps(evidence)),
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


def get_installation(dsn: str, installation_id: int) -> dict | None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT installation_id, account_login, plan, webhook_url
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
