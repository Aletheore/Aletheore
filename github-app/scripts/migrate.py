#!/usr/bin/env python3
"""Applies pending SQL migrations from migrations/, tracking what has
already been applied in a schema_migrations table so re-running this
script is always safe.

Every file under migrations/ is written to be idempotent (CREATE TABLE
IF NOT EXISTS, ADD COLUMN IF NOT EXISTS, etc.), including
001_initial_schema.sql - so this script can run against a brand-new
database, one already bootstrapped by docker-entrypoint-initdb.d (which
applies every file in migrations/ once, on first Postgres init, but
never records anything in schema_migrations), or a database that has
had some migrations applied manually in the past. In every case the
first run backfills schema_migrations correctly; every run after that
only applies files that are actually new.

Usage:
    DATABASE_URL=postgresql://user:pass@host:port/db python3 scripts/migrate.py
"""
import os
import sys
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def run_migrations(database_url: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    migration_files = sorted(migrations_dir.glob("*.sql"))

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    filename    TEXT PRIMARY KEY,
                    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute("SELECT filename FROM schema_migrations")
            applied = {row[0] for row in cur.fetchall()}
        conn.commit()

        pending = [f for f in migration_files if f.name not in applied]
        for migration_file in pending:
            with conn.cursor() as cur:
                cur.execute(migration_file.read_text())
                cur.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (%s)",
                    (migration_file.name,),
                )
            conn.commit()

    return [f.name for f in pending]


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("error: DATABASE_URL is required", file=sys.stderr)
        return 1

    applied = run_migrations(database_url)
    if not applied:
        print("no pending migrations")
        return 0

    for filename in applied:
        print(f"applied {filename}")
    print(f"applied {len(applied)} migration(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
