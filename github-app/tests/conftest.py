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
os.environ.setdefault("GITHUB_APP_SLUG", "aletheore")
os.environ.setdefault("GITHUB_CLIENT_ID", "test-client-id")
os.environ.setdefault("GITHUB_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("AUDIT_SIGNING_PRIVATE_KEY", "11" * 32)
os.environ.setdefault("PADDLE_WEBHOOK_SECRET", "pdl_ntfset_test_secret")
os.environ.setdefault("PADDLE_CLIENT_TOKEN", "test_conftest_client_token")
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
        # data_deletion_log and webhook_deliveries are listed explicitly
        # because neither has an FK to installations (see
        # 035_data_deletion_log.sql and 036_webhook_deliveries.sql) - the
        # CASCADE from installations doesn't reach them, so without this
        # their rows would leak from one test into the next.
        await conn.execute(
            "TRUNCATE installations, sessions, demo_scan_rate_limits, cli_telemetry_events, "
            "github_user_emails, sent_emails, data_deletion_log, webhook_deliveries CASCADE"
        )
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


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    # get_settings() is @lru_cache'd for production (56 call sites, was
    # re-reading the private-key file from disk on every single call) - but
    # the test suite monkeypatches env vars per-test expecting get_settings()
    # to reflect them fresh each time. Without this, whichever test happens
    # to call get_settings() first in the whole pytest session would
    # permanently pin every later test's settings to its own monkeypatched
    # values for the rest of the run.
    from app_server.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clear_redis_client_cache():
    # Same reasoning as _clear_settings_cache above, for get_redis_client()
    # (also @lru_cache'd, for the same "one pooled connection instead of a
    # fresh one per caller" reason). Without this, whichever test calls it
    # first in the whole session permanently pins every later test to that
    # first real connection - a test that monkeypatches get_redis_client to
    # simulate a Redis outage would silently have no effect, since the
    # cached real client from an earlier test is what every caller actually
    # gets.
    from app_server.redis_client import get_redis_client

    get_redis_client.cache_clear()
    yield
    get_redis_client.cache_clear()


@pytest.fixture(autouse=True)
def _no_real_paddle_ip_fetch(monkeypatch):
    # Without this, every full-route webhook test would make a real network
    # call to Paddle's /ips endpoint on the first request (module-level
    # cache miss) - slow and flaky in a sandboxed/offline CI runner. Default
    # to "can't verify" (None), the same fail-open outcome a real fetch
    # failure produces, so this doesn't change what any existing test
    # asserts. Tests that need to exercise the actual allow/reject paths
    # patch is_known_paddle_ip directly instead.
    from app_server import paddle_ip_allowlist

    monkeypatch.setattr(paddle_ip_allowlist, "_cache", None)

    async def _fake_fetch():
        return None

    monkeypatch.setattr(paddle_ip_allowlist, "_fetch_paddle_networks", _fake_fetch)


@pytest.fixture(autouse=True)
def _no_real_auth_rate_limiting(monkeypatch):
    # /auth/login and /auth/callback share one real-Redis-backed rate limit
    # keyed by client IP - every test hitting either route runs from the
    # same "testclient" source IP, so without this the whole suite (well
    # over AUTH_RATE_LIMIT calls across test_auth.py and
    # test_frontend_subscribe.py alone) would trip real 429s partway
    # through, unrelated to whatever each test actually verifies. Tests
    # that specifically exercise the 429 path patch is_rate_limited back
    # to something real (or fake it directly) within their own test body.
    monkeypatch.setattr("app_server.auth.is_rate_limited", lambda *a, **k: False)


@pytest.fixture(scope="session")
def _test_redis_connection():
    # One real connection reused for the whole run - reconnecting fresh
    # per test (as a function-scoped fixture would) adds a TCP handshake
    # to every single test in the suite for what's otherwise a single
    # flushdb() round trip.
    from redis import Redis

    try:
        conn = Redis.from_url(TEST_REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5)
        conn.ping()
    except Exception:
        yield None
        return
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _flush_test_redis(_test_redis_connection):
    # Real, persistent state in Redis (the administered-installations
    # cache, deletion OTP codes, etc.) otherwise survives between tests -
    # unlike `pool`, which truncates Postgres per test, nothing previously
    # cleared Redis, so a cache entry written by one test (often reusing
    # the same literal token/session fixtures as many others) could be
    # read back by a later, unrelated test. No-ops if no test Redis is
    # reachable, same as `redis_conn` above.
    if _test_redis_connection is None:
        yield
        return
    _test_redis_connection.flushdb()
    yield
    _test_redis_connection.flushdb()


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
