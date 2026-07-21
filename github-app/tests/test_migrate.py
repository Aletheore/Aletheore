import os
from urllib.parse import urlparse

import psycopg
import pytest

from scripts.migrate import MIGRATIONS_DIR, run_migrations

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:test@localhost:55433/aletheore_test",
)
_ALL_MIGRATION_NAMES = [f.name for f in sorted(MIGRATIONS_DIR.glob("*.sql"))]


def _admin_dsn() -> str:
    parsed = urlparse(TEST_DATABASE_URL)
    return f"postgresql://{parsed.username}:{parsed.password}@{parsed.hostname}:{parsed.port}/postgres"


def _dsn_for(db_name: str) -> str:
    parsed = urlparse(TEST_DATABASE_URL)
    return f"postgresql://{parsed.username}:{parsed.password}@{parsed.hostname}:{parsed.port}/{db_name}"


@pytest.fixture
def fresh_database():
    db_name = "aletheore_migrate_test"
    try:
        with psycopg.connect(_admin_dsn(), autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP DATABASE IF EXISTS {db_name}")
                cur.execute(f"CREATE DATABASE {db_name}")
    except OSError as exc:
        pytest.skip(f"test Postgres unavailable: {exc}")

    yield _dsn_for(db_name)

    with psycopg.connect(_admin_dsn(), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {db_name}")


def test_run_migrations_applies_all_files_to_a_fresh_database(fresh_database):
    applied = run_migrations(fresh_database)
    assert applied == _ALL_MIGRATION_NAMES

    with psycopg.connect(fresh_database) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
            tables = {row[0] for row in cur.fetchall()}

    for expected in (
        "installations",
        "repo_history",
        "sessions",
        "api_tokens",
        "endpoint_health",
        "managed_audit_rate_limits",
        "llm_spend",
        "flash_review_state",
        "schema_migrations",
    ):
        assert expected in tables


def test_run_migrations_is_idempotent(fresh_database):
    first = run_migrations(fresh_database)
    assert len(first) == len(_ALL_MIGRATION_NAMES)

    second = run_migrations(fresh_database)
    assert second == []


def test_run_migrations_backfills_schema_migrations_for_already_bootstrapped_db(fresh_database):
    # Simulates a database that got its schema from
    # docker-entrypoint-initdb.d (every migration file applied once by
    # Postgres on first init, but schema_migrations never populated,
    # since that mechanism knows nothing about this script). The first
    # migrate.py run against it must not fail re-applying idempotent
    # SQL, and must correctly backfill schema_migrations.
    with psycopg.connect(fresh_database) as conn:
        for migration_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            with conn.cursor() as cur:
                cur.execute(migration_file.read_text())
        conn.commit()

    applied = run_migrations(fresh_database)
    assert applied == _ALL_MIGRATION_NAMES

    second = run_migrations(fresh_database)
    assert second == []
