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


# Arbitrary but fixed: any process running this script takes the same lock, and
# it must not collide with the per-installation advisory locks taken elsewhere
# (those use installation_id, which is a GitHub installation id and never this).
_MIGRATION_LOCK_ID = 8_675_309


def run_migrations(database_url: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    migration_files = sorted(migrations_dir.glob("*.sql"))

    with psycopg.connect(database_url) as conn:
        # Session-level advisory lock held for the whole run. This script is the
        # app-server container's entrypoint (`migrate.py && exec uvicorn`), so
        # starting a second replica - or any restart overlapping an in-flight
        # one - runs it concurrently against the same database. Without the
        # lock, two processes both read schema_migrations, both see the same
        # file as pending, and both execute it: fine for the idempotent
        # CREATE TABLE IF NOT EXISTS bodies, but a duplicate-key error on the
        # INSERT below, which fails the container's startup command and puts it
        # into a crash-loop rather than starting the server.
        #
        # Deliberately blocking rather than pg_try_advisory_lock: the loser
        # should wait for the winner to finish and then find nothing pending,
        # not skip migrating and boot against a schema it does not match.
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK_ID,))
        conn.commit()

        try:
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
        finally:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK_ID,))
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
