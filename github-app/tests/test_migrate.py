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
    except (OSError, psycopg.OperationalError) as exc:
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
        "code_graph_sync_state",
        "code_graph_files",
        "code_graph_symbols",
        "code_graph_dependency_edges",
        "code_graph_endpoints",
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


def test_concurrent_migrate_runs_do_not_collide(tmp_path):
    """Two processes running migrate.py against the same database at once -
    the shape of starting a second app-server replica, or a restart
    overlapping an in-flight one. Without the advisory lock both read
    schema_migrations, both see the same file pending, and the second one's
    INSERT fails on the primary key, crash-looping the container.
    """
    import threading
    import uuid

    # Unique per run: schema_migrations persists across tests in the shared
    # test database, so fixed filenames would be "already applied" on the
    # second run and the assertion below would pass vacuously.
    run_id = uuid.uuid4().hex[:8]
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    names = [f"{i:03d}_{run_id}.sql" for i in range(6)]
    for i, name in enumerate(names):
        (migrations_dir / name).write_text(
            f"CREATE TABLE IF NOT EXISTS concurrent_t{run_id}_{i} (id INT);"
        )

    results: list = []
    errors: list = []

    def run():
        try:
            results.append(run_migrations(TEST_DATABASE_URL, migrations_dir))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert errors == [], f"concurrent migration raised: {errors}"
    # Exactly one runner applies each file; the others wait, then find nothing.
    assert sorted(sum(results, [])) == sorted(names)
