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

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"

# data_deletion_log, webhook_deliveries, and affiliates are listed explicitly
# because none has an FK to installations (see 035_data_deletion_log.sql,
# 036_webhook_deliveries.sql, and 046_affiliate_program.sql) - the CASCADE
# from installations doesn't reach them, so without this their rows would
# leak from one test into the next. affiliate_referrals/affiliate_commissions
# need no separate entry: both DO have an installations FK with ON DELETE
# CASCADE, so truncating installations already clears them.
# processed_paddle_transactions is listed explicitly for the same reason as
# webhook_deliveries above: it has no FK to installations (a transaction_id
# is a Paddle identifier, not an installation one), so the CASCADE from
# truncating installations never reaches it - without this it would leak
# rows across tests/runs, and
# test_credit_topup_purchase_is_idempotent_on_replayed_transaction relies on
# a clean slate to tell a genuine first credit from a stale row left by a
# previous run.
_TRUNCATE_SQL = (
    "TRUNCATE installations, sessions, cli_telemetry_events, "
    "github_user_emails, sent_emails, data_deletion_log, webhook_deliveries, affiliates, "
    "processed_paddle_transactions CASCADE"
)


async def _apply_migrations(conn) -> None:
    # Every migration file is idempotent (CREATE TABLE IF NOT EXISTS, etc. -
    # see scripts/migrate.py), so it's safe to apply all of them here
    # regardless of whether this database already has some or all of them
    # applied.
    for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
        await conn.execute(migration.read_text())


async def _truncate_test_tables(conn) -> None:
    await conn.execute(_TRUNCATE_SQL)


@pytest_asyncio.fixture
async def pool():
    try:
        p = await asyncpg.create_pool(TEST_DATABASE_URL)
    except OSError as exc:
        pytest.skip(f"test Postgres unavailable: {exc}")
    async with p.acquire() as conn:
        await _apply_migrations(conn)
        await _truncate_test_tables(conn)
    yield p
    async with p.acquire() as conn:
        # Also truncate on teardown, not just setup - a row this test itself
        # inserted (e.g. a dismissed_findings row) otherwise survives in the
        # real, persistent test Postgres until the *next* test's setup
        # truncate runs - but that next test's setup replays every migration
        # file FIRST (see _apply_migrations) and reaches this leftover row
        # while a migration (058) has its CHECK constraint temporarily
        # narrowed, before a later migration (068) widens it again - e.g. a
        # leftover 'static_analysis' finding_type row fails migration 058's
        # replayed ALTER TABLE ADD CONSTRAINT with a CheckViolationError
        # that has nothing to do with whatever the next test actually
        # asserts. Same before-and-after shape as _flush_test_redis below
        # for the same cross-test-leakage reason.
        await _truncate_test_tables(conn)
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
def _reset_git_path_cache(monkeypatch):
    # scan_worker.jobs._git_path() caches shutil.which("git") at module
    # level - without resetting it per test, whichever test runs first in
    # the whole session pins every later test to that one resolved value.
    # Most existing git-subprocess tests here mock subprocess.run and
    # assert on the literal "git" argv they themselves pass in, not
    # whatever absolute path this machine happens to resolve it to, so
    # this also defaults shutil.which("git") to return "git" itself -
    # unchanged behavior for those tests. A test that wants to verify
    # real path resolution (see test_run_git_resolves_the_bare_git_name_
    # to_its_shutil_which_path) mocks shutil.which itself within the test
    # body, which overrides this default the normal monkeypatch way.
    from scan_worker import jobs

    monkeypatch.setattr(jobs.shutil, "which", lambda name: "git" if name == "git" else None)
    jobs._GIT_PATH = None
    yield
    jobs._GIT_PATH = None


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
    # A genuinely non-repeating value, deliberately - "sk-abcdef1234567890
    # abcdef1234567890" (the previous fixture) repeats a 16-char unit, which
    # aletheore.secrets' synthetic-repetition check now correctly recognizes
    # as a fabricated placeholder rather than a real-looking secret, and
    # this fixture exists specifically to exercise the non-placeholder path.
    (work / "app.py").write_text("password = 'sk-9fK2mQ7vXzL4pR8wT1cH6dY3jN0bE5aG'\n")
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


@pytest.fixture
def bare_repo_with_dependency_bump(tmp_path):
    # pyyaml 5.3.1 carries a real, currently-published OSV.dev advisory
    # (GHSA-8q59-q68h-6hv4 / CVE-2020-14343); 6.0.1 does not - confirmed
    # live against api.osv.dev before writing this fixture, not assumed
    # from memory. This exercises check_vulnerabilities' real HTTP path,
    # same as production, rather than a mocked/fake advisory a real OSV
    # query would never actually return.
    work = tmp_path / "work"
    base_sha = _make_git_repo(work, {"requirements.txt": "pyyaml==6.0.1\n"})
    (work / "requirements.txt").write_text("pyyaml==5.3.1\n")
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "bump pyyaml"], cwd=work, check=True)
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
