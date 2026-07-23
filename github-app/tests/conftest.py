import os
import subprocess
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:test@localhost:55433/aletheore_test",
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/0")

os.environ.setdefault("DATABASE_URL", TEST_DATABASE_URL)
os.environ.setdefault("GITHUB_APP_ID", "12345")
os.environ.setdefault("GITHUB_APP_PRIVATE_KEY", "test-private-key")
os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-webhook-secret")
os.environ.setdefault("GITHUB_CLIENT_ID", "test-client-id")
os.environ.setdefault("GITHUB_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("AUDIT_SIGNING_PRIVATE_KEY", "11" * 32)
os.environ.setdefault("PUBLIC_BASE_URL", "http://test")


@pytest_asyncio.fixture
async def pool():
    try:
        p = await asyncpg.create_pool(TEST_DATABASE_URL)
    except OSError as exc:
        pytest.skip(f"test Postgres unavailable: {exc}")
    async with p.acquire() as conn:
        # Every migration file is idempotent (CREATE TABLE IF NOT EXISTS,
        # etc. - see scripts/migrate.py), so it's safe to apply all of
        # them here regardless of whether this database already has some
        # or all of them applied.
        migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
        for migration in sorted(migrations_dir.glob("*.sql")):
            await conn.execute(migration.read_text())
        await conn.execute("TRUNCATE installations, sessions CASCADE")
    yield p
    await p.close()


@pytest.fixture
def redis_conn():
    from redis import Redis

    conn = Redis.from_url(TEST_REDIS_URL)
    try:
        conn.ping()
    except Exception as exc:
        pytest.skip(f"test Redis unavailable: {exc}")
    yield conn
    conn.flushdb()
    conn.close()


def _make_git_repo(path: Path, files: dict[str, str]) -> str:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    for name, content in files.items():
        (path / name).write_text(content)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "commit"], cwd=path, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def bare_repo_with_two_commits(tmp_path):
    work = tmp_path / "work"
    base_sha = _make_git_repo(work, {"app.py": "print('hello')\n"})
    (work / "app.py").write_text("password = 'sk-abcdef1234567890abcdef1234567890'\n")
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add secret"], cwd=work, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    bare = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    return str(bare), base_sha, head_sha
