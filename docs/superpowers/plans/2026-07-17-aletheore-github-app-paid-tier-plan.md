# GitHub App Paid Tier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship GitHub OAuth login, seat-capped personal API tokens, managed audit runs (PR-comment and CLI/MCP triggered, one shared engine), and Slack/Teams + branch-protection team-risk alerts for the Aletheore GitHub App's paid tier.

**Architecture:** Extends the already-live `github-app/` service (`app_server` = FastAPI webhook/API/dashboard, `scan_worker` = RQ job worker) with a login layer (`app_server/auth.py`), a token-management layer (`app_server/admin.py`), a new webhook handler (`issue_comment`), a new authenticated API (`/v1/managed-audit`), and two new job functions in `scan_worker/jobs.py`. The managed-audit "engine" is not new code — it's `aletheore.report.run_reasoning_phase` plus `AnthropicAdapter`, both already shipped, called against either a real PR checkout or a scratch directory holding only evidence, depending on which trigger fired.

## Global Constraints

- Reuse `run_reasoning_phase(adapter, repo_path, manual_dir)` and `AnthropicAdapter` unchanged — confirmed by reading both fresh that `AnthropicAdapter.invoke()` only ever reads `.aletheore/evidence.toon` from `cwd`, never raw source files.
- The shared LLM key is supplied by setting `ANTHROPIC_API_KEY` in `scan-worker`'s environment — `AnthropicAdapter`/`credentials.py` already check that env var first, before any local credentials file. No adapter or credentials code changes.
- Branch-protection blocking is scoped to secrets only, using the same real/non-placeholder/non-accepted filter `--fail-on-new-secrets` already applies to `compute_diff`'s `secrets.new`.
- The App cannot force a merge block — only report a Check Run result. Any user-facing text about this feature must say so plainly.
- No automatic-per-push managed audits — on-demand only, via `/aletheore audit` PR comment or `aletheore audit --managed` / the equivalent MCP tool.
- `max_api_tokens` is a per-installation adjustable integer (default 3), not a hardcoded plan-tier table — pricing tiers are a future decision.
- All new Postgres schema lives in a new migration file (`002_paid_tier.sql`) — `docker-entrypoint-initdb.d` only runs against a fresh, empty Postgres data directory, so this migration must also be applied manually to the already-initialized live database (final task covers this).

---

## File Structure

```
github-app/
  migrations/
    002_paid_tier.sql                    # NEW
  app_server/
    config.py                            # MODIFY - add OAuth client id/secret, session secret
    db.py                                 # MODIFY - sessions, api_tokens, webhook_url/max_api_tokens
    auth.py                               # NEW - OAuth login/callback, session cookie helpers
    admin.py                              # NEW - token management + webhook-url routes (login-gated)
    managed_audit_api.py                  # NEW - POST/GET /v1/managed-audit (token-gated)
    main.py                               # MODIFY - wire new routers, issue_comment dispatch
    webhooks/
      issue_comment.py                    # NEW - /aletheore audit PR-comment trigger
  scan_worker/
    db.py                                 # MODIFY - sync get_installation
    slack.py                              # NEW - Slack/Teams webhook alert sender
    github_api.py                         # MODIFY - add create_check_run
    jobs.py                               # MODIFY - Slack/Check Run wiring, two new job functions
  requirements.txt                        # MODIFY - add itsdangerous
  tests/
    test_config.py                        # MODIFY - new Settings fields
    test_db.py                            # MODIFY - sessions/api_tokens coverage
    test_auth.py                          # NEW
    test_admin.py                         # NEW
    test_managed_audit_api.py             # NEW
    test_issue_comment_webhook.py         # NEW
    test_slack.py                         # NEW
    test_jobs.py                          # MODIFY - new wiring covered, existing tests still pass

prototype/
  pyproject.toml                          # MODIFY - add httpx as a direct dependency
  aletheore/
    managed_audit_client.py               # NEW - shared CLI/MCP client for the managed-audit API
    cli.py                                # MODIFY - `audit --managed` flag
    mcp_server.py                         # MODIFY - aletheore_managed_audit tool
  tests/
    test_managed_audit_client.py          # NEW
```

---

## Task 1: Migration 002 + `app_server/db.py` sessions & tokens

**Files:**
- Create: `github-app/migrations/002_paid_tier.sql`
- Modify: `github-app/app_server/db.py`
- Modify: `github-app/tests/conftest.py`
- Test: `github-app/tests/test_db.py`

**Interfaces:**
- Produces: `create_session(pool, session_id: str, github_user_id: int, github_login: str, access_token: str, expires_at: datetime) -> None`, `get_session(pool, session_id: str) -> dict | None`, `delete_session(pool, session_id: str) -> None`, `set_webhook_url(pool, installation_id: int, url: str | None) -> None`, `count_active_tokens(pool, installation_id: int) -> int`, `get_max_tokens(pool, installation_id: int) -> int`, `create_api_token(pool, installation_id: int, token_hash: str, label: str, created_by_github_login: str) -> None`, `revoke_api_token(pool, installation_id: int, token_id: int) -> None`, `list_api_tokens(pool, installation_id: int) -> list[dict]`, `get_installation_by_token_hash(pool, token_hash: str) -> dict | None`, `touch_api_token(pool, token_hash: str) -> None`. Used by Tasks 3, 4, 5, 8, 9.

- [ ] **Step 1: Write the migration**

Create `github-app/migrations/002_paid_tier.sql`:

```sql
ALTER TABLE installations ADD COLUMN max_api_tokens INT NOT NULL DEFAULT 3;
ALTER TABLE installations ADD COLUMN webhook_url TEXT;

CREATE TABLE sessions (
    id                  TEXT PRIMARY KEY,
    github_user_id      BIGINT NOT NULL,
    github_login        TEXT NOT NULL,
    github_access_token TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at          TIMESTAMPTZ NOT NULL
);

CREATE TABLE api_tokens (
    id                      BIGSERIAL PRIMARY KEY,
    installation_id         BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    token_hash              TEXT NOT NULL UNIQUE,
    label                   TEXT NOT NULL,
    created_by_github_login TEXT NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at            TIMESTAMPTZ,
    revoked_at              TIMESTAMPTZ
);
CREATE INDEX api_tokens_installation ON api_tokens (installation_id) WHERE revoked_at IS NULL;
```

- [ ] **Step 2: Apply it to the test database and update conftest's truncation**

Run: `PGPASSWORD=test psql -h localhost -p 55433 -U postgres -d aletheore_test -f github-app/migrations/002_paid_tier.sql`
Expected: `ALTER TABLE` x2, `CREATE TABLE` x2, `CREATE INDEX`, no errors

Modify `github-app/tests/conftest.py` — the `pool` fixture's `TRUNCATE installations CASCADE` already cascades to `api_tokens` (FK) but not `sessions` (no FK relationship). Update the truncate line:

```python
    async with p.acquire() as conn:
        await conn.execute("TRUNCATE installations, sessions CASCADE")
```

- [ ] **Step 3: Write the failing tests**

Append to `github-app/tests/test_db.py`:

```python
from datetime import datetime, timedelta, timezone

from app_server.db import (
    count_active_tokens,
    create_api_token,
    create_session,
    delete_session,
    get_installation_by_token_hash,
    get_max_tokens,
    get_session,
    list_api_tokens,
    revoke_api_token,
    set_webhook_url,
    touch_api_token,
    upsert_installation,
)


@pytest.mark.asyncio
async def test_create_and_get_session(pool):
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    await create_session(pool, "sess-1", 42, "octocat", "gho_faketoken", expires)
    row = await get_session(pool, "sess-1")
    assert row["github_user_id"] == 42
    assert row["github_login"] == "octocat"
    assert row["github_access_token"] == "gho_faketoken"


@pytest.mark.asyncio
async def test_get_session_missing_returns_none(pool):
    assert await get_session(pool, "nonexistent") is None


@pytest.mark.asyncio
async def test_delete_session_removes_it(pool):
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    await create_session(pool, "sess-2", 42, "octocat", "gho_faketoken", expires)
    await delete_session(pool, "sess-2")
    assert await get_session(pool, "sess-2") is None


@pytest.mark.asyncio
async def test_set_and_get_webhook_url(pool):
    await upsert_installation(pool, 100, "octocat")
    await set_webhook_url(pool, 100, "https://hooks.slack.com/services/x")
    row = await pool.fetchrow("SELECT webhook_url FROM installations WHERE installation_id = 100")
    assert row["webhook_url"] == "https://hooks.slack.com/services/x"


@pytest.mark.asyncio
async def test_default_max_tokens_is_three(pool):
    await upsert_installation(pool, 100, "octocat")
    assert await get_max_tokens(pool, 100) == 3


@pytest.mark.asyncio
async def test_create_api_token_and_count(pool):
    await upsert_installation(pool, 100, "octocat")
    assert await count_active_tokens(pool, 100) == 0
    await create_api_token(pool, 100, "hash1", "laptop", "octocat")
    assert await count_active_tokens(pool, 100) == 1


@pytest.mark.asyncio
async def test_revoked_token_not_counted_active(pool):
    await upsert_installation(pool, 100, "octocat")
    await create_api_token(pool, 100, "hash1", "laptop", "octocat")
    tokens = await list_api_tokens(pool, 100)
    await revoke_api_token(pool, 100, tokens[0]["id"])
    assert await count_active_tokens(pool, 100) == 0


@pytest.mark.asyncio
async def test_list_api_tokens_returns_label_and_no_hash(pool):
    await upsert_installation(pool, 100, "octocat")
    await create_api_token(pool, 100, "hash1", "laptop", "octocat")
    tokens = await list_api_tokens(pool, 100)
    assert len(tokens) == 1
    assert tokens[0]["label"] == "laptop"
    assert "token_hash" not in tokens[0]


@pytest.mark.asyncio
async def test_get_installation_by_token_hash_resolves_installation(pool):
    await upsert_installation(pool, 100, "octocat")
    await create_api_token(pool, 100, "hash1", "laptop", "octocat")
    row = await get_installation_by_token_hash(pool, "hash1")
    assert row["installation_id"] == 100
    assert row["account_login"] == "octocat"


@pytest.mark.asyncio
async def test_get_installation_by_token_hash_excludes_revoked(pool):
    await upsert_installation(pool, 100, "octocat")
    await create_api_token(pool, 100, "hash1", "laptop", "octocat")
    tokens = await list_api_tokens(pool, 100)
    await revoke_api_token(pool, 100, tokens[0]["id"])
    assert await get_installation_by_token_hash(pool, "hash1") is None


@pytest.mark.asyncio
async def test_touch_api_token_updates_last_used(pool):
    await upsert_installation(pool, 100, "octocat")
    await create_api_token(pool, 100, "hash1", "laptop", "octocat")
    await touch_api_token(pool, "hash1")
    row = await pool.fetchrow("SELECT last_used_at FROM api_tokens WHERE token_hash = 'hash1'")
    assert row["last_used_at"] is not None
```

- [ ] **Step 4: Run to verify it fails**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_db.py -v`
Expected: FAIL with `ImportError` (functions don't exist yet)

- [ ] **Step 5: Implement**

Append to `github-app/app_server/db.py`:

```python
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
        "SELECT count(*) AS n FROM api_tokens WHERE installation_id = $1 AND revoked_at IS NULL",
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
        ORDER BY created_at DESC
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
```

- [ ] **Step 6: Run to verify it passes**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_db.py -v`
Expected: PASS (all tests, including the 6 pre-existing ones)

- [ ] **Step 7: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/migrations/002_paid_tier.sql github-app/app_server/db.py \
        github-app/tests/conftest.py github-app/tests/test_db.py
git commit -m "feat(github-app): paid-tier schema - sessions, api_tokens, webhook_url"
```

---

## Task 2: `scan_worker/db.py` — sync `get_installation`

**Files:**
- Modify: `github-app/scan_worker/db.py`
- Test: `github-app/tests/test_scan_worker_db.py`

**Interfaces:**
- Produces: `get_installation(dsn: str, installation_id: int) -> dict | None` returning `{"installation_id", "account_login", "plan", "webhook_url"}`. Consumed by Task 5 (Slack) and Task 6 (Check Run) inside `scan_worker/jobs.py`.

- [ ] **Step 1: Write the failing test**

Create `github-app/tests/test_scan_worker_db.py`:

```python
import os

import psycopg
import pytest

from scan_worker.db import get_installation

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:test@localhost:55433/aletheore_test"
)


@pytest.fixture
def dsn():
    try:
        conn = psycopg.connect(TEST_DATABASE_URL)
    except psycopg.OperationalError as exc:
        pytest.skip(f"test Postgres unavailable: {exc}")
    with conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE installations CASCADE")
    conn.close()
    return TEST_DATABASE_URL


def test_get_installation_returns_row(dsn):
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO installations (installation_id, account_login, plan, webhook_url) "
                "VALUES (200, 'octocat', 'pro', 'https://hooks.slack.com/x')"
            )
        conn.commit()

    row = get_installation(dsn, 200)
    assert row["account_login"] == "octocat"
    assert row["plan"] == "pro"
    assert row["webhook_url"] == "https://hooks.slack.com/x"


def test_get_installation_missing_returns_none(dsn):
    assert get_installation(dsn, 999999) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_scan_worker_db.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_installation'`

- [ ] **Step 3: Implement**

Append to `github-app/scan_worker/db.py`:

```python
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
            columns = [desc[0] for desc in cur.description]
            return dict(zip(columns, row))
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_scan_worker_db.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/scan_worker/db.py github-app/tests/test_scan_worker_db.py
git commit -m "feat(github-app): sync get_installation for scan-worker's plan/webhook checks"
```

---

## Task 3: GitHub OAuth login

**Files:**
- Create: `github-app/app_server/auth.py`
- Modify: `github-app/app_server/config.py`
- Modify: `github-app/app_server/main.py`
- Modify: `github-app/requirements.txt`
- Modify: `github-app/tests/conftest.py`
- Test: `github-app/tests/test_auth.py`

**Interfaces:**
- Consumes: `create_session`, `get_session`, `delete_session` (Task 1).
- Produces: `auth_router` (FastAPI `APIRouter`, mounted in `main.py`), `get_current_session(request: Request) -> dict | None` (reads the signed cookie, looks up the session — used by Task 4's admin routes), `sign_session_id(session_id: str, secret: str) -> str`, `unsign_session_id(signed: str, secret: str) -> str | None`.

- [ ] **Step 1: Add `itsdangerous`/`cryptography` and new `Settings` fields**

Modify `github-app/requirements.txt` — add two lines (`cryptography` is already a transitive dependency via `pyjwt[crypto]`, but the spec's Non-Goals section explicitly flagged that a stored GitHub access token is a real credential needing encryption-at-rest, resolved here — declaring it directly rather than relying on an incidental transitive dependency matches this project's standing dependency-declaration discipline):

```
itsdangerous>=2.2.0
cryptography>=44.0.0
```

Read `github-app/app_server/config.py` fully before editing (already read fresh above). Modify it:

```python
@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    github_app_id: str
    github_app_private_key: str
    github_webhook_secret: str
    github_client_id: str
    github_client_secret: str
    session_secret: str
    public_base_url: str
```

Modify `get_settings()`'s return to add the four new fields:

```python
def get_settings() -> Settings:
    return Settings(
        database_url=os.environ["DATABASE_URL"],
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        github_app_id=os.environ.get("GITHUB_APP_ID", ""),
        github_app_private_key=_load_private_key(),
        github_webhook_secret=os.environ.get("GITHUB_WEBHOOK_SECRET", ""),
        github_client_id=os.environ.get("GITHUB_CLIENT_ID", ""),
        github_client_secret=os.environ.get("GITHUB_CLIENT_SECRET", ""),
        session_secret=os.environ.get("SESSION_SECRET", ""),
        public_base_url=os.environ.get("PUBLIC_BASE_URL", "https://aletheore.com"),
    )
```

- [ ] **Step 2: Write the failing test**

Create `github-app/tests/test_auth.py`:

```python
import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app_server.auth import sign_session_id, unsign_session_id
from app_server.db import get_session, upsert_installation
from app_server.main import app


def test_sign_and_unsign_round_trip():
    signed = sign_session_id("sess-123", "test-secret")
    assert unsign_session_id(signed, "test-secret") == "sess-123"


def test_unsign_rejects_tampered_value():
    signed = sign_session_id("sess-123", "test-secret")
    tampered = signed[:-1] + ("a" if signed[-1] != "a" else "b")
    assert unsign_session_id(tampered, "test-secret") is None


def test_unsign_rejects_wrong_secret():
    signed = sign_session_id("sess-123", "test-secret")
    assert unsign_session_id(signed, "different-secret") is None


@pytest.mark.asyncio
async def test_login_redirects_to_github_authorize(pool, monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "test-client-id")
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 307
    assert "github.com/login/oauth/authorize" in response.headers["location"]
    assert "client_id=test-client-id" in response.headers["location"]


@pytest.mark.asyncio
async def test_callback_creates_session_and_sets_cookie(pool, monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_faketoken"})
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 42, "login": "octocat"})
        return httpx.Response(404)

    monkeypatch.setattr(
        "app_server.auth._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )
    monkeypatch.setattr(
        "app_server.auth._github_oauth_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://github.com"),
    )

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/auth/callback?code=fake-code", follow_redirects=False)

    assert response.status_code == 307
    assert "session" in response.cookies

    from app_server.auth import unsign_session_id

    session_id = unsign_session_id(response.cookies["session"], "test-session-secret")
    assert session_id is not None
    row = await get_session(pool, session_id)
    assert row["github_login"] == "octocat"
    # Stored value must not be the plaintext token - proves encryption
    # actually happened, not just that a value was stored.
    assert row["github_access_token"] != "gho_faketoken"


@pytest.mark.asyncio
async def test_get_current_session_decrypts_access_token(pool, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from app_server.auth import encrypt_access_token, get_current_session
    from app_server.db import create_session

    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    encrypted = encrypt_access_token("gho_realtoken", "test-session-secret")
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    await create_session(pool, "sess-3", 42, "octocat", encrypted, expires)

    app.state.db_pool = pool
    signed = sign_session_id("sess-3", "test-session-secret")

    class FakeRequest:
        cookies = {"session": signed}
        app = type("App", (), {"state": type("State", (), {"db_pool": pool})()})()

    session = await get_current_session(FakeRequest())
    assert session["github_access_token"] == "gho_realtoken"
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd github-app && pip install -r requirements.txt && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app_server.auth'`

- [ ] **Step 4: Implement**

Create `github-app/app_server/auth.py`:

```python
import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import httpx
from cryptography.fernet import Fernet
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app_server.config import get_settings
from app_server.db import create_session, get_session

SESSION_COOKIE_NAME = "session"
SESSION_TTL = timedelta(days=30)

auth_router = APIRouter()


def _fernet_key(session_secret: str) -> bytes:
    # Fernet requires a 32-byte url-safe base64-encoded key - derived from
    # SESSION_SECRET (already a real secret managed the same way as every
    # other credential in .env) rather than adding a second secret to manage.
    return base64.urlsafe_b64encode(hashlib.sha256(session_secret.encode()).digest())


def encrypt_access_token(access_token: str, session_secret: str) -> str:
    return Fernet(_fernet_key(session_secret)).encrypt(access_token.encode()).decode()


def decrypt_access_token(encrypted: str, session_secret: str) -> str:
    return Fernet(_fernet_key(session_secret)).decrypt(encrypted.encode()).decode()


def _github_oauth_http_client() -> httpx.Client:
    return httpx.Client(base_url="https://github.com")


def _github_http_client() -> httpx.Client:
    return httpx.Client(base_url="https://api.github.com")


def sign_session_id(session_id: str, secret: str) -> str:
    return URLSafeTimedSerializer(secret).dumps(session_id)


def unsign_session_id(signed: str, secret: str) -> str | None:
    try:
        return URLSafeTimedSerializer(secret).loads(signed, max_age=int(SESSION_TTL.total_seconds()))
    except BadSignature:
        return None


async def get_current_session(request: Request) -> dict | None:
    signed = request.cookies.get(SESSION_COOKIE_NAME)
    if not signed:
        return None
    settings = get_settings()
    session_id = unsign_session_id(signed, settings.session_secret)
    if session_id is None:
        return None
    pool = request.app.state.db_pool
    row = await get_session(pool, session_id)
    if row is None:
        return None
    # Stored encrypted (Non-Goal in the spec explicitly flagged this as
    # needing resolution here) - decrypted only in memory, for the caller's
    # immediate use (e.g. calling GET /user/installations in Task 4).
    row["github_access_token"] = decrypt_access_token(row["github_access_token"], settings.session_secret)
    return row


@auth_router.get("/auth/login")
async def login():
    settings = get_settings()
    url = (
        "https://github.com/login/oauth/authorize"
        f"?client_id={settings.github_client_id}"
        f"&redirect_uri={settings.public_base_url}/auth/callback"
    )
    return RedirectResponse(url=url, status_code=307)


@auth_router.get("/auth/callback")
async def callback(code: str, request: Request):
    settings = get_settings()

    oauth_client = _github_oauth_http_client()
    token_response = oauth_client.post(
        "/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": settings.github_client_id,
            "client_secret": settings.github_client_secret,
            "code": code,
        },
    )
    token_response.raise_for_status()
    access_token = token_response.json()["access_token"]

    api_client = _github_http_client()
    user_response = api_client.get(
        "/user",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json"},
    )
    user_response.raise_for_status()
    user = user_response.json()

    session_id = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + SESSION_TTL
    pool = request.app.state.db_pool
    encrypted_token = encrypt_access_token(access_token, settings.session_secret)
    await create_session(pool, session_id, user["id"], user["login"], encrypted_token, expires_at)

    signed = sign_session_id(session_id, settings.session_secret)
    response = RedirectResponse(url="/dashboard", status_code=307)
    response.set_cookie(
        SESSION_COOKIE_NAME, signed, httponly=True, secure=True, samesite="lax", max_age=int(SESSION_TTL.total_seconds())
    )
    return response


@auth_router.get("/auth/logout")
async def logout(request: Request):
    from app_server.db import delete_session

    signed = request.cookies.get(SESSION_COOKIE_NAME)
    if signed:
        settings = get_settings()
        session_id = unsign_session_id(signed, settings.session_secret)
        if session_id:
            pool = request.app.state.db_pool
            await delete_session(pool, session_id)
    response = RedirectResponse(url="/", status_code=307)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response
```

Modify `github-app/app_server/main.py` — add the import and router registration:

```python
from app_server.auth import auth_router
```

(with the other `app_server` imports), and after `app.include_router(dashboard_router)`:

```python
app.include_router(auth_router)
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_auth.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Run the full existing suite to confirm no regressions**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest -q`
Expected: PASS (all tests, no failures)

- [ ] **Step 7: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/app_server/auth.py github-app/app_server/config.py github-app/app_server/main.py \
        github-app/requirements.txt github-app/tests/test_auth.py
git commit -m "feat(github-app): GitHub OAuth login (signed session cookie, no new account system)"
```

---

## Task 4: Token management + Slack webhook-url admin routes

**Files:**
- Create: `github-app/app_server/admin.py`
- Modify: `github-app/app_server/main.py`
- Test: `github-app/tests/test_admin.py`

**Interfaces:**
- Consumes: `get_current_session` (Task 3), `count_active_tokens`/`get_max_tokens`/`create_api_token`/`list_api_tokens`/`revoke_api_token`/`set_webhook_url`/`get_installation` (Task 1's `app_server/db.py`, `get_installation` already existed pre-Task-1).
- Produces: `admin_router` (mounted in `main.py`). Routes: `GET /admin/{org}/{repo}` (installation summary + token list, requires login + admin access to that installation + paid plan), `POST /admin/{org}/{repo}/tokens` (generate, body `{"label": str}`, returns the raw token once), `DELETE /admin/{org}/{repo}/tokens/{token_id}`, `PUT /admin/{org}/{repo}/webhook-url` (body `{"webhook_url": str | None}`).

**Authorization check**: "does this session's GitHub user administer this installation" is verified by calling `GET /user/installations` with the session's stored `github_access_token` and checking the target `installation_id` appears in the response — GitHub's own permission model, no local roles table, matching the spec's stated reasoning.

- [ ] **Step 1: Write the failing test**

Create `github-app/tests/test_admin.py`:

```python
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app_server.auth import sign_session_id
from app_server.db import create_session, set_installation_plan, upsert_installation
from app_server.main import app


async def _logged_in_client(pool, monkeypatch, installation_id=100, plan="pro"):
    from app_server.auth import encrypt_access_token
    from app_server.db import insert_repo_history

    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    await upsert_installation(pool, installation_id, "octocat")
    await set_installation_plan(pool, installation_id, plan)
    # _require_admin_installation resolves org/repo -> installation_id via
    # repo_history, the same lookup dashboard.py's GET /app/{org}/{repo}
    # already uses - a real installation with no scans yet has no
    # repo_history row, but by the time a paid customer configures tokens
    # the free-tier scan has already run at least once on real usage. Seed
    # one here so this test path matches that reality instead of 404ing.
    await insert_repo_history(
        pool, installation_id, "octocat/hello-world", datetime.now(timezone.utc), {"scanned_at": "x"}
    )
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    encrypted = encrypt_access_token("gho_faketoken", "test-session-secret")
    await create_session(pool, "sess-1", 42, "octocat", encrypted, expires)

    def handler(request: httpx.Request) -> httpx.Response:
        # Real GitHub API shape: {"total_count": N, "installations": [...]}
        return httpx.Response(
            200,
            json={"total_count": 1, "installations": [{"id": installation_id, "account": {"login": "octocat"}}]},
        )

    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    signed = sign_session_id("sess-1", "test-session-secret")
    client = AsyncClient(transport=transport, base_url="http://test", cookies={"session": signed})
    return client


@pytest.mark.asyncio
async def test_admin_page_requires_login(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/admin/octocat/hello-world")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_admin_page_rejects_non_admin_installation(pool, monkeypatch):
    from app_server.db import insert_repo_history

    client = await _logged_in_client(pool, monkeypatch, installation_id=100)
    # A real, resolvable installation the session's mocked /user/installations
    # (which only lists id 100) does NOT include - proves the 403 comes from
    # the admin check itself, not from the repo simply not being found (that
    # would be 404, a different failure mode entirely).
    await upsert_installation(pool, 200, "someorg")
    await insert_repo_history(pool, 200, "someorg/other-repo", datetime.now(timezone.utc), {"scanned_at": "x"})
    async with client:
        response = await client.get("/admin/someorg/other-repo")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_page_rejects_free_plan(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, installation_id=100, plan="free")
    async with client:
        response = await client.get("/admin/octocat/hello-world")
    assert response.status_code == 402


@pytest.mark.asyncio
async def test_admin_page_includes_branch_protection_disclosure(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, installation_id=100)
    async with client:
        response = await client.get("/admin/octocat/hello-world")
    assert response.status_code == 200
    assert "cannot" in response.json()["branch_protection_disclosure"]


@pytest.mark.asyncio
async def test_generate_token_returns_raw_value_once(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, installation_id=100)
    async with client:
        response = await client.post("/admin/octocat/hello-world/tokens", json={"label": "laptop"})
    assert response.status_code == 200
    body = response.json()
    assert "token" in body
    assert len(body["token"]) > 20


@pytest.mark.asyncio
async def test_generate_token_rejected_over_cap(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, installation_id=100)
    async with client:
        for i in range(3):
            r = await client.post("/admin/octocat/hello-world/tokens", json={"label": f"device-{i}"})
            assert r.status_code == 200
        over_cap = await client.post("/admin/octocat/hello-world/tokens", json={"label": "one-too-many"})
    assert over_cap.status_code == 409


@pytest.mark.asyncio
async def test_revoke_token(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, installation_id=100)
    async with client:
        created = await client.post("/admin/octocat/hello-world/tokens", json={"label": "laptop"})
        token_id = created.json()["id"]
        revoke = await client.delete(f"/admin/octocat/hello-world/tokens/{token_id}")
    assert revoke.status_code == 200


@pytest.mark.asyncio
async def test_set_webhook_url(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, installation_id=100)
    async with client:
        response = await client.put(
            "/admin/octocat/hello-world/webhook-url",
            json={"webhook_url": "https://hooks.slack.com/services/x"},
        )
    assert response.status_code == 200
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_admin.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app_server.admin'`

- [ ] **Step 3: Implement**

Create `github-app/app_server/admin.py`:

```python
import hashlib
import secrets

import httpx
from fastapi import APIRouter, HTTPException, Request

from app_server.auth import get_current_session
from app_server.db import (
    count_active_tokens,
    create_api_token,
    get_installation,
    get_max_tokens,
    list_api_tokens,
    revoke_api_token,
    set_webhook_url,
)

admin_router = APIRouter()


def _github_http_client() -> httpx.Client:
    return httpx.Client(base_url="https://api.github.com")


async def _require_admin_installation(request: Request, org: str, repo: str) -> dict:
    session = await get_current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="login required")

    pool = request.app.state.db_pool
    row = await pool.fetchrow(
        "SELECT DISTINCT installation_id FROM repo_history WHERE repo_full_name = $1 LIMIT 1",
        f"{org}/{repo}",
    )
    if row is None:
        raise HTTPException(status_code=404, detail="no such repo")
    installation_id = row["installation_id"]

    client = _github_http_client()
    response = client.get(
        "/user/installations",
        headers={
            "Authorization": f"Bearer {session['github_access_token']}",
            "Accept": "application/vnd.github+json",
        },
    )
    response.raise_for_status()
    # Real shape confirmed against GitHub's documented API:
    # {"total_count": N, "installations": [...]} - not a bare array.
    administered_ids = {item["id"] for item in response.json()["installations"]}
    if installation_id not in administered_ids:
        raise HTTPException(status_code=403, detail="you do not administer this installation")

    installation = await get_installation(pool, installation_id)
    if installation is None or installation["plan"] == "free":
        raise HTTPException(status_code=402, detail="this feature requires a paid plan")

    return installation


BRANCH_PROTECTION_DISCLOSURE = (
    "Aletheore reports a Check Run result on new secrets found - it does not and cannot "
    "unilaterally block a merge. To actually require it, mark \"Aletheore secrets check\" as "
    "a required status check in this repository's own branch protection settings."
)


@admin_router.get("/admin/{org}/{repo}")
async def admin_page(org: str, repo: str, request: Request):
    installation = await _require_admin_installation(request, org, repo)
    pool = request.app.state.db_pool
    tokens = await list_api_tokens(pool, installation["installation_id"])
    return {
        "installation": installation,
        "tokens": tokens,
        "branch_protection_disclosure": BRANCH_PROTECTION_DISCLOSURE,
    }


@admin_router.post("/admin/{org}/{repo}/tokens")
async def generate_token(org: str, repo: str, request: Request):
    installation = await _require_admin_installation(request, org, repo)
    body = await request.json()
    label = body["label"]

    pool = request.app.state.db_pool
    installation_id = installation["installation_id"]
    max_tokens = await get_max_tokens(pool, installation_id)
    active = await count_active_tokens(pool, installation_id)
    if active >= max_tokens:
        raise HTTPException(status_code=409, detail=f"token limit reached ({max_tokens})")

    session = await get_current_session(request)
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    await create_api_token(pool, installation_id, token_hash, label, session["github_login"])

    tokens = await list_api_tokens(pool, installation_id)
    return {"token": raw_token, "id": tokens[0]["id"], "label": label}


@admin_router.delete("/admin/{org}/{repo}/tokens/{token_id}")
async def revoke_token(org: str, repo: str, token_id: int, request: Request):
    installation = await _require_admin_installation(request, org, repo)
    pool = request.app.state.db_pool
    await revoke_api_token(pool, installation["installation_id"], token_id)
    return {"ok": True}


@admin_router.put("/admin/{org}/{repo}/webhook-url")
async def set_webhook_url_route(org: str, repo: str, request: Request):
    installation = await _require_admin_installation(request, org, repo)
    body = await request.json()
    pool = request.app.state.db_pool
    await set_webhook_url(pool, installation["installation_id"], body.get("webhook_url"))
    return {"ok": True}
```

Modify `github-app/app_server/main.py` — add the import and router registration:

```python
from app_server.admin import admin_router
```

and:

```python
app.include_router(admin_router)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_admin.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Run the full suite**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest -q`
Expected: PASS (all tests)

- [ ] **Step 6: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/app_server/admin.py github-app/app_server/main.py github-app/tests/test_admin.py
git commit -m "feat(github-app): token management + Slack webhook-url admin routes, login-gated"
```

---

## Task 5: Slack/Teams alert on new findings

**Files:**
- Create: `github-app/scan_worker/slack.py`
- Modify: `github-app/scan_worker/jobs.py`
- Modify: `github-app/tests/test_jobs.py`
- Test: `github-app/tests/test_slack.py`

**Interfaces:**
- Consumes: `get_installation` (Task 2).
- Produces: `format_slack_message(diff: dict, repo_full_name: str, pr_number: int) -> dict` (a Slack/Teams-compatible `{"text": ...}` payload), `send_slack_alert(webhook_url: str, diff: dict, repo_full_name: str, pr_number: int, http_client: httpx.Client | None = None) -> None`.

- [ ] **Step 1: Write the failing test**

Create `github-app/tests/test_slack.py`:

```python
import httpx

from scan_worker.slack import format_slack_message, send_slack_alert


def _diff_with_new_secret():
    return {
        "secrets": {"new": [{"path": "a.py", "line": 1, "pattern": "aws_key"}], "resolved": []},
        "history_secrets": {"new": [], "resolved": []},
        "vulnerabilities": {"new": [], "resolved": []},
        "layer_violations": {"new": [], "resolved": []},
    }


def test_format_slack_message_mentions_repo_and_pr():
    body = format_slack_message(_diff_with_new_secret(), "octocat/hello-world", 42)
    assert "octocat/hello-world" in body["text"]
    assert "42" in body["text"]
    assert "a.py:1" in body["text"]


def test_send_slack_alert_posts_to_webhook_url():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    send_slack_alert(
        "https://hooks.slack.com/services/x", _diff_with_new_secret(), "octocat/hello-world", 42, http_client=client
    )
    assert len(calls) == 1
    assert calls[0].url == "https://hooks.slack.com/services/x"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_slack.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scan_worker.slack'`

- [ ] **Step 3: Implement `slack.py`**

Create `github-app/scan_worker/slack.py`:

```python
import httpx


def _has_new_findings(diff: dict) -> bool:
    return bool(
        diff.get("secrets", {}).get("new")
        or diff.get("history_secrets", {}).get("new")
        or diff.get("vulnerabilities", {}).get("new")
        or diff.get("layer_violations", {}).get("new")
    )


def format_slack_message(diff: dict, repo_full_name: str, pr_number: int) -> dict:
    lines = [f"*Aletheore*: new findings on `{repo_full_name}` PR #{pr_number}"]
    for finding in diff.get("secrets", {}).get("new", []):
        lines.append(f"- Secret: `{finding.get('path')}:{finding.get('line')}` ({finding.get('pattern')})")
    for finding in diff.get("vulnerabilities", {}).get("new", []):
        lines.append(
            f"- Vulnerability: {finding.get('package')} {finding.get('installed_version')} "
            f"({finding.get('advisory_id')})"
        )
    for finding in diff.get("layer_violations", {}).get("new", []):
        lines.append(f"- Layer violation: `{finding.get('from')}` -> `{finding.get('to')}`")
    return {"text": "\n".join(lines)}


def send_slack_alert(
    webhook_url: str,
    diff: dict,
    repo_full_name: str,
    pr_number: int,
    http_client: httpx.Client | None = None,
) -> None:
    if not _has_new_findings(diff):
        return
    client = http_client or httpx.Client()
    response = client.post(webhook_url, json=format_slack_message(diff, repo_full_name, pr_number))
    response.raise_for_status()
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_slack.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Wire it into `run_pr_scan_job`**

Read `github-app/scan_worker/jobs.py` fully before editing (already read fresh above). Modify it — add the import:

```python
from scan_worker.db import get_installation as get_installation_row, insert_repo_history
from scan_worker.slack import send_slack_alert
```

(replacing the existing `from scan_worker.db import insert_repo_history` line with the two-name version above).

In `run_pr_scan_job`, after the line `_insert_history(installation_id, repo_full_name, new)`, add:

```python
        _maybe_send_slack_alert(installation_id, repo_full_name, pr_number, diff)
```

Add the new helper function (near `_insert_history`):

```python
def _maybe_send_slack_alert(installation_id: int, repo_full_name: str, pr_number: int, diff: dict) -> None:
    settings = get_settings()
    installation = get_installation_row(settings.database_url, installation_id)
    if installation is None or installation["plan"] == "free":
        return
    webhook_url = installation.get("webhook_url")
    if not webhook_url:
        return
    send_slack_alert(webhook_url, diff, repo_full_name, pr_number)
```

- [ ] **Step 6: Update the three existing `test_jobs.py` tests to monkeypatch the new call**

Modify `github-app/tests/test_jobs.py` — each of the three existing test functions needs one added monkeypatch line so they don't try to hit a real database via `_maybe_send_slack_alert`. Add this line to `test_happy_path_posts_comment_and_writes_history`, `test_temp_dir_cleaned_up_on_success`, and `test_clone_failure_posts_failure_comment_and_cleans_up`, alongside their existing `monkeypatch.setattr("scan_worker.jobs._insert_history", ...)` line:

```python
    monkeypatch.setattr("scan_worker.jobs._maybe_send_slack_alert", lambda *a, **k: None)
```

- [ ] **Step 7: Write the new failing test for the wiring itself**

Append to `github-app/tests/test_jobs.py`:

```python
def test_slack_alert_fires_on_paid_install_with_webhook_url_and_new_secret(
    bare_repo_with_two_commits, monkeypatch
):
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row",
        lambda *a, **k: {"plan": "pro", "webhook_url": "https://hooks.slack.com/x"},
    )

    sent = {}

    def fake_send(webhook_url, diff, repo_full_name, pr_number):
        sent["webhook_url"] = webhook_url
        sent["repo_full_name"] = repo_full_name

    monkeypatch.setattr("scan_worker.jobs.send_slack_alert", fake_send)

    from scan_worker.jobs import run_pr_scan_job

    run_pr_scan_job(
        installation_id=1,
        repo_full_name="octocat/hello-world",
        pr_number=7,
        base_sha=base_sha,
        head_sha=head_sha,
    )

    assert sent["webhook_url"] == "https://hooks.slack.com/x"
    assert sent["repo_full_name"] == "octocat/hello-world"


def test_slack_alert_does_not_fire_on_free_plan(bare_repo_with_two_commits, monkeypatch):
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row",
        lambda *a, **k: {"plan": "free", "webhook_url": "https://hooks.slack.com/x"},
    )

    called = []
    monkeypatch.setattr("scan_worker.jobs.send_slack_alert", lambda *a, **k: called.append(1))

    from scan_worker.jobs import run_pr_scan_job

    run_pr_scan_job(
        installation_id=1,
        repo_full_name="octocat/hello-world",
        pr_number=7,
        base_sha=base_sha,
        head_sha=head_sha,
    )

    assert called == []


def test_slack_alert_does_not_fire_without_webhook_url(bare_repo_with_two_commits, monkeypatch):
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "pro", "webhook_url": None}
    )

    called = []
    monkeypatch.setattr("scan_worker.jobs.send_slack_alert", lambda *a, **k: called.append(1))

    from scan_worker.jobs import run_pr_scan_job

    run_pr_scan_job(
        installation_id=1,
        repo_full_name="octocat/hello-world",
        pr_number=7,
        base_sha=base_sha,
        head_sha=head_sha,
    )

    assert called == []
```

Note: `monkeypatch.setattr("scan_worker.jobs.get_installation_row", ...)` works because Step 5's import renamed `get_installation` to `get_installation_row` in `jobs.py`'s namespace via `from scan_worker.db import get_installation as get_installation_row` — the name that must be patched is the one bound inside `jobs.py`, not `scan_worker.db`'s original name.

- [ ] **Step 8: Run the full `test_jobs.py` suite**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_jobs.py -v`
Expected: PASS (6 tests: 3 pre-existing + 3 new)

- [ ] **Step 9: Run the full suite**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest -q`
Expected: PASS (all tests)

- [ ] **Step 10: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/scan_worker/slack.py github-app/scan_worker/jobs.py github-app/tests/test_slack.py \
        github-app/tests/test_jobs.py
git commit -m "feat(github-app): Slack/Teams alert on new findings for paid installs"
```

---

## Task 6: Branch-protection Check Run (secrets only)

**Files:**
- Modify: `github-app/scan_worker/github_api.py`
- Modify: `github-app/scan_worker/jobs.py`
- Modify: `github-app/tests/test_jobs.py`
- Test: `github-app/tests/test_github_api.py`

**Interfaces:**
- Consumes: `get_installation_row` (Task 5's rename, already in `jobs.py`).
- Produces: `create_check_run(client: httpx.Client, token: str, repo_full_name: str, head_sha: str, conclusion: str, summary: str) -> None` in `scan_worker/github_api.py`.

- [ ] **Step 1: Write the failing test**

Append to `github-app/tests/test_github_api.py`:

```python
def test_create_check_run_posts_expected_payload():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(201, json={"id": 1})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    from scan_worker.github_api import create_check_run

    create_check_run(client, "token", "octocat/hello-world", "abc123", "failure", "New secret found")

    assert len(calls) == 1
    request = calls[0]
    assert request.method == "POST"
    assert request.url.path == "/repos/octocat/hello-world/check-runs"
    import json as _json

    body = _json.loads(request.content)
    assert body["head_sha"] == "abc123"
    assert body["status"] == "completed"
    assert body["conclusion"] == "failure"
    assert body["name"] == "Aletheore secrets check"
```

Note: `httpx` is already imported at the top of `github-app/tests/test_github_api.py` (used by the existing tests) — no new import line needed at file scope.

- [ ] **Step 2: Run to verify it fails**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_github_api.py -v`
Expected: FAIL with `ImportError: cannot import name 'create_check_run'`

- [ ] **Step 3: Implement**

Append to `github-app/scan_worker/github_api.py`:

```python
def create_check_run(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    head_sha: str,
    conclusion: str,
    summary: str,
) -> None:
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    response = client.post(
        f"/repos/{repo_full_name}/check-runs",
        headers=headers,
        json={
            "name": "Aletheore secrets check",
            "head_sha": head_sha,
            "status": "completed",
            "conclusion": conclusion,
            "output": {"title": "Aletheore secrets check", "summary": summary},
        },
    )
    response.raise_for_status()
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_github_api.py -v`
Expected: PASS (3 tests: 2 pre-existing + 1 new)

- [ ] **Step 5: Wire it into `run_pr_scan_job`**

Modify `github-app/scan_worker/jobs.py` — add the import (extend the existing `from scan_worker.github_api import upsert_pr_comment` line):

```python
from scan_worker.github_api import create_check_run, upsert_pr_comment
```

In `run_pr_scan_job`, after the `_maybe_send_slack_alert(...)` line added in Task 5, add:

```python
        _maybe_create_check_run(client, token, repo_full_name, head_sha, installation_id, diff)
```

Add the new helper function:

```python
def _real_new_secrets(diff: dict) -> list[dict]:
    return [
        finding
        for finding in diff.get("secrets", {}).get("new", [])
        if not finding.get("likely_placeholder", False) and not finding.get("accepted", False)
    ]


def _maybe_create_check_run(
    client: httpx.Client, token: str, repo_full_name: str, head_sha: str, installation_id: int, diff: dict
) -> None:
    settings = get_settings()
    installation = get_installation_row(settings.database_url, installation_id)
    if installation is None or installation["plan"] == "free":
        return

    new_secrets = _real_new_secrets(diff)
    if new_secrets:
        summary = "\n".join(f"- `{f.get('path')}:{f.get('line')}` ({f.get('pattern')})" for f in new_secrets)
        create_check_run(client, token, repo_full_name, head_sha, "failure", summary)
    else:
        create_check_run(client, token, repo_full_name, head_sha, "success", "No new secrets found.")
```

- [ ] **Step 6: Update the three pre-existing `test_jobs.py` tests once more**

Add this line alongside the `_maybe_send_slack_alert` monkeypatch added in Task 5, in the same three test functions:

```python
    monkeypatch.setattr("scan_worker.jobs._maybe_create_check_run", lambda *a, **k: None)
```

Also add it to the three Task-5 Slack tests (`test_slack_alert_fires_on_paid_install_with_webhook_url_and_new_secret`, `test_slack_alert_does_not_fire_on_free_plan`, `test_slack_alert_does_not_fire_without_webhook_url`) so they don't also trigger a real Check Run call.

- [ ] **Step 7: Write the new failing tests for the wiring**

Append to `github-app/tests/test_jobs.py`:

```python
def test_check_run_failure_on_new_secret(bare_repo_with_two_commits, monkeypatch):
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_send_slack_alert", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "pro"})

    created = {}

    def fake_create_check_run(client, token, repo_full_name, head_sha, conclusion, summary):
        created["conclusion"] = conclusion
        created["head_sha"] = head_sha

    monkeypatch.setattr("scan_worker.jobs.create_check_run", fake_create_check_run)

    from scan_worker.jobs import run_pr_scan_job

    run_pr_scan_job(
        installation_id=1,
        repo_full_name="octocat/hello-world",
        pr_number=7,
        base_sha=base_sha,
        head_sha=head_sha,
    )

    assert created["conclusion"] == "failure"
    assert created["head_sha"] == head_sha


def test_check_run_success_on_clean_scan(monkeypatch, tmp_path):
    import subprocess

    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=work, check=True)
    (work / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "commit"], cwd=work, check=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work, check=True, capture_output=True, text=True
    ).stdout.strip()
    (work / "readme.md").write_text("docs\n")
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add docs"], cwd=work, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work, check=True, capture_output=True, text=True
    ).stdout.strip()
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: str(bare))
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_send_slack_alert", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "pro"})

    created = {}
    monkeypatch.setattr(
        "scan_worker.jobs.create_check_run",
        lambda client, token, repo_full_name, head_sha, conclusion, summary: created.update(
            conclusion=conclusion
        ),
    )

    from scan_worker.jobs import run_pr_scan_job

    run_pr_scan_job(
        installation_id=1,
        repo_full_name="octocat/hello-world",
        pr_number=7,
        base_sha=base_sha,
        head_sha=head_sha,
    )

    assert created["conclusion"] == "success"


def test_check_run_not_created_for_free_plan(bare_repo_with_two_commits, monkeypatch):
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_send_slack_alert", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "free"})

    called = []
    monkeypatch.setattr("scan_worker.jobs.create_check_run", lambda *a, **k: called.append(1))

    from scan_worker.jobs import run_pr_scan_job

    run_pr_scan_job(
        installation_id=1,
        repo_full_name="octocat/hello-world",
        pr_number=7,
        base_sha=base_sha,
        head_sha=head_sha,
    )

    assert called == []
```

- [ ] **Step 8: Run the full `test_jobs.py` suite**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_jobs.py -v`
Expected: PASS (9 tests: 6 from Task 5 + 3 new)

- [ ] **Step 9: Run the full suite**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest -q`
Expected: PASS (all tests)

- [ ] **Step 10: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/scan_worker/github_api.py github-app/scan_worker/jobs.py github-app/tests/test_github_api.py \
        github-app/tests/test_jobs.py
git commit -m "feat(github-app): branch-protection Check Run, secrets-only, paid plans"
```

---

## Task 7: Managed-audit shared engine core

**Files:**
- Create: `github-app/scan_worker/managed_audit.py`
- Test: `github-app/tests/test_managed_audit.py`

**Interfaces:**
- Consumes: `run_reasoning_phase` (`aletheore.report`), `AnthropicAdapter` (`aletheore.adapters.anthropic_native`), `write_evidence` (`aletheore.evidence`).
- Produces: `run_managed_audit(repo_path: Path, manual_dir: str | None = None) -> str` (calls `run_reasoning_phase` with a fresh `AnthropicAdapter()`, returns the report text read back from the written file — not just the path, since both trigger paths in Tasks 8 and 9 need the actual text). Used by Tasks 8 and 9.

- [ ] **Step 1: Write the failing test**

Create `github-app/tests/test_managed_audit.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock

from scan_worker.managed_audit import run_managed_audit


def test_run_managed_audit_returns_report_text(tmp_path, monkeypatch):
    repo_path = tmp_path / "repo"
    (repo_path / ".aletheore").mkdir(parents=True)
    (repo_path / ".aletheore" / "evidence.toon").write_text("fake toon evidence")

    fake_adapter = MagicMock()
    fake_adapter.invoke.return_value = "irrelevant - report.py writes the file itself"

    def fake_run_reasoning_phase(adapter, repo_path_arg, manual_dir):
        report_path = Path(repo_path_arg) / ".aletheore" / "audit-report.md"
        report_path.write_text("# Real Report\n\nfindings here")
        return str(report_path)

    monkeypatch.setattr("scan_worker.managed_audit.AnthropicAdapter", lambda: fake_adapter)
    monkeypatch.setattr("scan_worker.managed_audit.run_reasoning_phase", fake_run_reasoning_phase)

    result = run_managed_audit(repo_path)

    assert "Real Report" in result
    assert "findings here" in result
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd github-app && python -m pytest tests/test_managed_audit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scan_worker.managed_audit'`

- [ ] **Step 3: Implement**

Create `github-app/scan_worker/managed_audit.py`:

```python
from pathlib import Path

import aletheore.cli as _aletheore_cli
from aletheore.adapters.anthropic_native import AnthropicAdapter
from aletheore.report import run_reasoning_phase


def run_managed_audit(repo_path: Path, manual_dir: str | None = None) -> str:
    adapter = AnthropicAdapter()
    report_path = run_reasoning_phase(adapter, str(repo_path), manual_dir or _aletheore_cli.MANUAL_DIR)
    return Path(report_path).read_text()
```

`MANUAL_DIR` is resolved via `aletheore.cli`'s own module-level constant (`Path(__file__).resolve().parent / "manual"`, relative to wherever `cli.py` itself lives) rather than a path relative to this repo's layout - this works identically whether `aletheore` is installed as a real package (production, `pip install ./prototype` per `Dockerfile.scan-worker`) or in editable mode (local dev), since it always resolves relative to the installed `cli.py`'s actual location, not to this file's position in the source tree.

- [ ] **Step 4: Run to verify it passes**

Run: `cd github-app && python -m pytest tests/test_managed_audit.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/scan_worker/managed_audit.py github-app/tests/test_managed_audit.py
git commit -m "feat(github-app): shared managed-audit engine (reuses run_reasoning_phase + AnthropicAdapter unchanged)"
```

---

## Task 8: PR-comment-triggered managed audit (`/aletheore audit`)

**Files:**
- Create: `github-app/app_server/webhooks/issue_comment.py`
- Modify: `github-app/app_server/main.py`
- Modify: `github-app/scan_worker/jobs.py`
- Test: `github-app/tests/test_issue_comment_webhook.py`
- Test: `github-app/tests/test_jobs.py` (managed-audit PR job coverage)

**Interfaces:**
- Consumes: `run_managed_audit` (Task 7), `get_installation` (Task 2), `_clone_url`/`_job_temp_dir`/`_token_sync` (existing helpers already in `jobs.py`).
- Produces: `handle_issue_comment_event(payload: dict, redis_url: str, queue=None) -> None` in `app_server/webhooks/issue_comment.py`; `run_managed_audit_pr_job(installation_id: int, repo_full_name: str, pr_number: int) -> None` in `scan_worker/jobs.py`.

- [ ] **Step 1: Write the failing webhook-handler test**

Create `github-app/tests/test_issue_comment_webhook.py`:

```python
from unittest.mock import MagicMock

import pytest

from app_server.webhooks.issue_comment import handle_issue_comment_event


def _payload(comment_body: str, has_pr: bool = True):
    payload = {
        "action": "created",
        "installation": {"id": 111},
        "repository": {"full_name": "octocat/hello-world"},
        "issue": {"number": 42},
        "comment": {"body": comment_body},
    }
    if has_pr:
        payload["issue"]["pull_request"] = {"url": "https://api.github.com/..."}
    return payload


@pytest.mark.asyncio
async def test_audit_command_enqueues_managed_audit_job():
    fake_queue = MagicMock()
    await handle_issue_comment_event(_payload("/aletheore audit"), "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_called_once()
    args, kwargs = fake_queue.enqueue.call_args
    assert args[0] == "scan_worker.jobs.run_managed_audit_pr_job"
    assert kwargs["installation_id"] == 111
    assert kwargs["repo_full_name"] == "octocat/hello-world"
    assert kwargs["pr_number"] == 42


@pytest.mark.asyncio
async def test_non_audit_comment_does_not_enqueue():
    fake_queue = MagicMock()
    await handle_issue_comment_event(_payload("just a regular comment"), "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_comment_on_plain_issue_not_pr_does_not_enqueue():
    fake_queue = MagicMock()
    await handle_issue_comment_event(
        _payload("/aletheore audit", has_pr=False), "redis://unused", queue=fake_queue
    )
    fake_queue.enqueue.assert_not_called()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd github-app && python -m pytest tests/test_issue_comment_webhook.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app_server.webhooks.issue_comment'`

- [ ] **Step 3: Implement the webhook handler**

Create `github-app/app_server/webhooks/issue_comment.py`:

```python
AUDIT_COMMAND = "/aletheore audit"


async def handle_issue_comment_event(payload: dict, redis_url: str, queue=None) -> None:
    if payload.get("action") != "created":
        return
    if "pull_request" not in payload.get("issue", {}):
        return
    body = payload.get("comment", {}).get("body", "")
    if AUDIT_COMMAND not in body:
        return

    if queue is None:
        from redis import Redis
        from rq import Queue

        queue = Queue("scans", connection=Redis.from_url(redis_url))

    queue.enqueue(
        "scan_worker.jobs.run_managed_audit_pr_job",
        installation_id=payload["installation"]["id"],
        repo_full_name=payload["repository"]["full_name"],
        pr_number=payload["issue"]["number"],
    )
```

Modify `github-app/app_server/main.py` — add a new branch in the `webhook` route, alongside the existing `elif event == "pull_request":` branch:

```python
    elif event == "issue_comment":
        from app_server.webhooks.issue_comment import handle_issue_comment_event

        await handle_issue_comment_event(payload, settings.redis_url)
```

- [ ] **Step 4: Run to verify the webhook-handler tests pass**

Run: `cd github-app && python -m pytest tests/test_issue_comment_webhook.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Write the failing job test**

Append to `github-app/tests/test_jobs.py`:

```python
def test_managed_audit_pr_job_clones_pr_head_runs_audit_and_replies(monkeypatch, tmp_path):
    import subprocess

    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=work, check=True)
    (work / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "commit"], cwd=work, check=True)
    subprocess.run(["git", "update-ref", "refs/pull/42/head", "HEAD"], cwd=work, check=True)
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: str(bare))
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.run_managed_audit", lambda repo_path: "# Managed Audit\n\nreal report")

    posted = {}

    def fake_upsert(client, token, repo_full_name, pr_number, body):
        posted["body"] = body
        posted["repo_full_name"] = repo_full_name
        posted["pr_number"] = pr_number

    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", fake_upsert)

    from scan_worker.jobs import run_managed_audit_pr_job

    run_managed_audit_pr_job(installation_id=1, repo_full_name="octocat/hello-world", pr_number=42)

    assert "Managed Audit" in posted["body"]
    assert posted["repo_full_name"] == "octocat/hello-world"
    assert posted["pr_number"] == 42


def test_managed_audit_pr_job_cleans_up_temp_dir(monkeypatch, tmp_path):
    import subprocess

    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=work, check=True)
    (work / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "commit"], cwd=work, check=True)
    subprocess.run(["git", "update-ref", "refs/pull/42/head", "HEAD"], cwd=work, check=True)
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)

    import scan_worker.jobs as jobs_module

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: str(bare))
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.run_managed_audit", lambda repo_path: "report")
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)

    seen_dirs = []
    original = jobs_module._job_temp_dir

    def spy():
        d = original()
        seen_dirs.append(d)
        return d

    monkeypatch.setattr("scan_worker.jobs._job_temp_dir", spy)

    from scan_worker.jobs import run_managed_audit_pr_job

    run_managed_audit_pr_job(installation_id=1, repo_full_name="octocat/hello-world", pr_number=42)

    assert len(seen_dirs) == 1
    assert not seen_dirs[0].exists()
```

- [ ] **Step 6: Run to verify it fails**

Run: `cd github-app && python -m pytest tests/test_jobs.py -k managed_audit_pr -v`
Expected: FAIL with `ImportError: cannot import name 'run_managed_audit_pr_job'`

- [ ] **Step 7: Implement `run_managed_audit_pr_job`**

Modify `github-app/scan_worker/jobs.py` — add the import:

```python
from scan_worker.managed_audit import run_managed_audit
```

Append the new function (a comment marker distinct from the free scan's, so the two never collide when both exist on the same PR):

```python
AUDIT_COMMENT_MARKER = "<!-- aletheore-audit -->"


def _clone_pr_head(url: str, pr_number: int, dest) -> None:
    subprocess.run(["git", "clone", "-q", "--no-checkout", url, str(dest)], check=True)
    subprocess.run(["git", "fetch", "-q", "origin", f"pull/{pr_number}/head"], cwd=dest, check=True)
    subprocess.run(["git", "checkout", "-q", "FETCH_HEAD"], cwd=dest, check=True)


def run_managed_audit_pr_job(installation_id: int, repo_full_name: str, pr_number: int) -> None:
    settings = get_settings()
    job_dir = _job_temp_dir()
    try:
        app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
        token = _token_sync(installation_id, app_jwt)

        clone_url = _clone_url(repo_full_name, token)
        repo_dir = job_dir / "head"
        _clone_pr_head(clone_url, pr_number, repo_dir)
        _run_scan(repo_dir)

        report_text = run_managed_audit(repo_dir)
        body = f"{AUDIT_COMMENT_MARKER}\n### 🔍 Aletheore managed audit\n\n{report_text}"

        client = httpx.Client(base_url="https://api.github.com")
        upsert_pr_comment(client, token, repo_full_name, pr_number, body)
    except Exception as exc:  # noqa: BLE001
        try:
            _post_failure_comment(settings, installation_id, repo_full_name, pr_number, exc)
        except Exception:  # noqa: BLE001
            pass
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)
```

Note: `upsert_pr_comment` finds/edits a comment by searching for `COMMENT_MARKER` (`<!-- aletheore-diff -->`, imported from `aletheore.pr_comment`) in existing comments - it will not find `AUDIT_COMMENT_MARKER`-marked comments, so each `/aletheore audit` reply upserts against *other* `AUDIT_COMMENT_MARKER`-tagged comments only, never colliding with the free scan's diff comment. This is correct as-is because `upsert_pr_comment`'s marker check (`COMMENT_MARKER in comment.get("body", "")`) is a substring check against whatever `body` was passed to it - since `run_managed_audit_pr_job` passes a body starting with `AUDIT_COMMENT_MARKER`, not `COMMENT_MARKER`, the search inside `upsert_pr_comment` needs the *correct* marker constant for the *audit* comment thread specifically. Read `github-app/scan_worker/github_api.py`'s `upsert_pr_comment` again: it imports `COMMENT_MARKER` directly from `aletheore.pr_comment` and always searches for *that* marker - meaning as written, calling `upsert_pr_comment` from `run_managed_audit_pr_job` would incorrectly search for the diff comment's marker, not the audit comment's marker, and would either wrongly overwrite the diff comment or never find/upsert its own audit comment correctly.

Fix this before it ships: `upsert_pr_comment` needs a `marker` parameter instead of a hardcoded import. Modify `github-app/scan_worker/github_api.py`:

```python
def upsert_pr_comment(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    pr_number: int,
    body: str,
    marker: str = COMMENT_MARKER,
) -> None:
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    comments_url = f"/repos/{repo_full_name}/issues/{pr_number}/comments"
    response = client.get(comments_url, headers=headers)
    response.raise_for_status()
    existing = next(
        (comment for comment in response.json() if marker in comment.get("body", "")),
        None,
    )

    if existing:
        response = client.patch(
            f"/repos/{repo_full_name}/issues/comments/{existing['id']}",
            headers=headers,
            json={"body": body},
        )
    else:
        response = client.post(comments_url, headers=headers, json={"body": body})
    response.raise_for_status()
```

And update the call in `run_managed_audit_pr_job` to pass it explicitly:

```python
        upsert_pr_comment(client, token, repo_full_name, pr_number, body, marker=AUDIT_COMMENT_MARKER)
```

The existing `run_pr_scan_job`'s call to `upsert_pr_comment(client, token, repo_full_name, pr_number, format_diff_comment(diff))` still works unchanged - `marker` defaults to `COMMENT_MARKER`, matching its prior hardcoded behavior exactly.

- [ ] **Step 8: Update the existing `test_github_api.py` tests for the new `marker` parameter**

Read `github-app/tests/test_github_api.py`'s existing two `upsert_pr_comment` tests fresh - they call `upsert_pr_comment(client, "token", "octocat/hello-world", 42, f"{COMMENT_MARKER}\nbody")` without a `marker` argument, which still works unchanged since `marker` now defaults to `COMMENT_MARKER`. No test changes needed for this parameter - only the new managed-audit marker behavior is exercised in Step 5's job-level tests above, so add one more direct unit test:

Append to `github-app/tests/test_github_api.py`:

```python
def test_upsert_pr_comment_uses_custom_marker_when_given():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.content))
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": 1, "body": f"{COMMENT_MARKER}\nold diff comment"}])
        return httpx.Response(201, json={"id": 2})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    from scan_worker.github_api import upsert_pr_comment

    upsert_pr_comment(
        client, "token", "octocat/hello-world", 42, "<!-- aletheore-audit -->\nnew audit", marker="<!-- aletheore-audit -->"
    )

    # The only existing comment carries COMMENT_MARKER, not the audit marker -
    # a correct marker-scoped search must not match it, so this must POST (create), not PATCH.
    assert calls[1][0] == "POST"
```

- [ ] **Step 9: Run to verify everything passes**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_jobs.py tests/test_github_api.py tests/test_issue_comment_webhook.py -v`
Expected: PASS (all tests, including the two new managed-audit-PR-job tests and the marker-scoping test)

- [ ] **Step 10: Run the full suite**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest -q`
Expected: PASS (all tests)

- [ ] **Step 11: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/app_server/webhooks/issue_comment.py github-app/app_server/main.py \
        github-app/scan_worker/jobs.py github-app/scan_worker/github_api.py \
        github-app/tests/test_issue_comment_webhook.py github-app/tests/test_jobs.py \
        github-app/tests/test_github_api.py
git commit -m "feat(github-app): /aletheore audit PR-comment trigger for managed audits

Also fixes a real marker-collision bug found while wiring this up:
upsert_pr_comment always searched for the free-tier diff comment's
marker regardless of caller, which would have made the audit reply
either silently overwrite the diff comment or never find its own
prior reply. marker is now a parameter, defaulting to the diff
comment's marker so the existing call site is unaffected."
```

---

## Task 9: CLI/MCP-triggered managed audit API

**Files:**
- Create: `github-app/app_server/managed_audit_api.py`
- Modify: `github-app/app_server/main.py`
- Modify: `github-app/scan_worker/jobs.py`
- Test: `github-app/tests/test_managed_audit_api.py`
- Test: `github-app/tests/test_jobs.py` (API-triggered job coverage)

**Interfaces:**
- Consumes: `get_installation_by_token_hash`, `touch_api_token` (Task 1), `run_managed_audit` (Task 7).
- Produces: `managed_audit_router` (mounted in `main.py`); `run_managed_audit_api_job(evidence: dict) -> str` in `scan_worker/jobs.py` (returns report text as the RQ job's `.result`, no side-effecting comment).

- [ ] **Step 1: Write the failing API test**

Create `github-app/tests/test_managed_audit_api.py`:

```python
import hashlib
from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app_server.db import create_api_token, set_installation_plan, upsert_installation
from app_server.main import app


@pytest.mark.asyncio
async def test_managed_audit_requires_bearer_token(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/v1/managed-audit", json={"evidence": "toon-text"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_managed_audit_rejects_free_plan(pool):
    await upsert_installation(pool, 100, "octocat")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "octocat")
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/managed-audit",
            json={"evidence": "toon-text"},
            headers={"Authorization": "Bearer real-token"},
        )
    assert response.status_code == 402


@pytest.mark.asyncio
async def test_managed_audit_enqueues_job_for_paid_token(pool, monkeypatch):
    await upsert_installation(pool, 100, "octocat")
    await set_installation_plan(pool, 100, "pro")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "octocat")

    fake_job = MagicMock()
    fake_job.id = "job-123"
    fake_queue = MagicMock()
    fake_queue.enqueue.return_value = fake_job
    monkeypatch.setattr("app_server.managed_audit_api._get_queue", lambda redis_url: fake_queue)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/managed-audit",
            json={"evidence": "toon-text"},
            headers={"Authorization": "Bearer real-token"},
        )

    assert response.status_code == 202
    assert response.json()["job_id"] == "job-123"
    fake_queue.enqueue.assert_called_once()
    args, kwargs = fake_queue.enqueue.call_args
    assert args[0] == "scan_worker.jobs.run_managed_audit_api_job"


@pytest.mark.asyncio
async def test_get_job_status_returns_result_when_finished(pool, monkeypatch):
    fake_job = MagicMock()
    fake_job.is_finished = True
    fake_job.is_failed = False
    fake_job.result = "# Report\n\ncontent"
    monkeypatch.setattr("app_server.managed_audit_api._fetch_job", lambda job_id, redis_url: fake_job)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/managed-audit/job-123")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "finished"
    assert body["result"] == "# Report\n\ncontent"


@pytest.mark.asyncio
async def test_get_job_status_returns_pending_when_not_finished(pool, monkeypatch):
    fake_job = MagicMock()
    fake_job.is_finished = False
    fake_job.is_failed = False
    monkeypatch.setattr("app_server.managed_audit_api._fetch_job", lambda job_id, redis_url: fake_job)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/managed-audit/job-123")

    assert response.status_code == 200
    assert response.json()["status"] == "pending"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_managed_audit_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app_server.managed_audit_api'`

- [ ] **Step 3: Implement**

Create `github-app/app_server/managed_audit_api.py`:

```python
import hashlib

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app_server.config import get_settings
from app_server.db import get_installation_by_token_hash, touch_api_token

managed_audit_router = APIRouter()


def _get_queue(redis_url: str):
    from redis import Redis
    from rq import Queue

    return Queue("scans", connection=Redis.from_url(redis_url))


def _fetch_job(job_id: str, redis_url: str):
    from redis import Redis
    from rq.job import Job

    return Job.fetch(job_id, connection=Redis.from_url(redis_url))


@managed_audit_router.post("/v1/managed-audit")
async def start_managed_audit(request: Request):
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    raw_token = auth_header.removeprefix("Bearer ")
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    pool = request.app.state.db_pool
    installation = await get_installation_by_token_hash(pool, token_hash)
    if installation is None:
        raise HTTPException(status_code=401, detail="invalid or revoked token")
    if installation["plan"] == "free":
        raise HTTPException(status_code=402, detail="managed audits require a paid plan")

    await touch_api_token(pool, token_hash)

    body = await request.json()
    evidence = body["evidence"]

    settings = get_settings()
    queue = _get_queue(settings.redis_url)
    job = queue.enqueue("scan_worker.jobs.run_managed_audit_api_job", evidence=evidence)
    return JSONResponse(status_code=202, content={"job_id": job.id})


@managed_audit_router.get("/v1/managed-audit/{job_id}")
async def get_managed_audit_status(job_id: str):
    settings = get_settings()
    job = _fetch_job(job_id, settings.redis_url)
    if job.is_failed:
        return {"status": "failed"}
    if job.is_finished:
        return {"status": "finished", "result": job.result}
    return {"status": "pending"}
```

Modify `github-app/app_server/main.py` — add the import and router registration:

```python
from app_server.managed_audit_api import managed_audit_router
```

and:

```python
app.include_router(managed_audit_router)
```

- [ ] **Step 4: Run to verify the API tests pass**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_managed_audit_api.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Write the failing job test**

Append to `github-app/tests/test_jobs.py`:

```python
def test_managed_audit_api_job_returns_report_text(monkeypatch, tmp_path):
    monkeypatch.setattr("scan_worker.jobs.run_managed_audit", lambda repo_path: "# API Report\n\nreal content")

    seen_dirs = []
    import scan_worker.jobs as jobs_module

    original = jobs_module._job_temp_dir

    def spy():
        d = original()
        seen_dirs.append(d)
        return d

    monkeypatch.setattr("scan_worker.jobs._job_temp_dir", spy)

    from scan_worker.jobs import run_managed_audit_api_job

    result = run_managed_audit_api_job(evidence={"scanned_at": "2026-01-01", "repository": {"modules": []}})

    assert "API Report" in result
    assert len(seen_dirs) == 1
    assert not seen_dirs[0].exists()
```

- [ ] **Step 6: Run to verify it fails**

Run: `cd github-app && python -m pytest tests/test_jobs.py -k managed_audit_api -v`
Expected: FAIL with `ImportError: cannot import name 'run_managed_audit_api_job'`

- [ ] **Step 7: Implement**

Modify `github-app/scan_worker/jobs.py` — add the import:

```python
from aletheore.evidence import write_evidence
```

Append the new function:

```python
def run_managed_audit_api_job(evidence: dict) -> str:
    job_dir = _job_temp_dir()
    try:
        write_evidence(evidence, job_dir)
        return run_managed_audit(job_dir)
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)
```

- [ ] **Step 8: Run to verify everything passes**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_jobs.py tests/test_managed_audit_api.py -v`
Expected: PASS (all tests)

- [ ] **Step 9: Run the full suite**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest -q`
Expected: PASS (all tests)

- [ ] **Step 10: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/app_server/managed_audit_api.py github-app/app_server/main.py \
        github-app/scan_worker/jobs.py github-app/tests/test_managed_audit_api.py github-app/tests/test_jobs.py
git commit -m "feat(github-app): POST/GET /v1/managed-audit - CLI/MCP-triggered managed audits, token-gated"
```

---

## Task 10: CLI `aletheore audit --managed`

**Files:**
- Create: `prototype/aletheore/managed_audit_client.py`
- Modify: `prototype/aletheore/cli.py`
- Modify: `prototype/pyproject.toml`
- Test: `prototype/tests/test_managed_audit_client.py`

**Interfaces:**
- Consumes: `write_evidence` and `to_toon` (`aletheore.evidence`, `aletheore.toon_encoding`, both already imported in `cli.py`).
- Produces: `run_managed_audit_request(evidence: dict, token: str, api_base_url: str = "https://aletheore.com", http_client=None, poll_interval: float = 2.0, timeout: float = 300.0) -> str` in `aletheore/managed_audit_client.py`. Consumed by `cli.py`'s `audit --managed` and Task 11's MCP tool.

- [ ] **Step 1: Add `httpx` as a direct dependency**

Modify `prototype/pyproject.toml` — read it fresh first (already read this session), then add one line to the `dependencies` list (alphabetically, after `certifi`):

```
    "httpx>=0.28.1,<1.0",
```

- [ ] **Step 2: Write the failing test**

Create `prototype/tests/test_managed_audit_client.py`:

```python
import httpx
import pytest

from aletheore.managed_audit_client import ManagedAuditError, run_managed_audit_request


def test_successful_request_returns_report(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/v1/managed-audit" and request.method == "POST":
            return httpx.Response(202, json={"job_id": "job-1"})
        if request.url.path == "/v1/managed-audit/job-1":
            return httpx.Response(200, json={"status": "finished", "result": "# Report\n\ntext"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://aletheore.com")
    report = run_managed_audit_request({"scanned_at": "x"}, "real-token", http_client=client, poll_interval=0)

    assert "Report" in report
    assert calls[0].headers["Authorization"] == "Bearer real-token"


def test_pending_then_finished_polls_until_done(monkeypatch):
    responses = iter(
        [
            httpx.Response(200, json={"status": "pending"}),
            httpx.Response(200, json={"status": "pending"}),
            httpx.Response(200, json={"status": "finished", "result": "done"}),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"job_id": "job-1"})
        return next(responses)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://aletheore.com")
    report = run_managed_audit_request({"scanned_at": "x"}, "real-token", http_client=client, poll_interval=0)

    assert report == "done"


def test_unauthorized_raises_managed_audit_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "invalid or revoked token"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://aletheore.com")
    with pytest.raises(ManagedAuditError, match="invalid or revoked token"):
        run_managed_audit_request({"scanned_at": "x"}, "bad-token", http_client=client)


def test_free_plan_raises_managed_audit_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"detail": "managed audits require a paid plan"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://aletheore.com")
    with pytest.raises(ManagedAuditError, match="paid plan"):
        run_managed_audit_request({"scanned_at": "x"}, "free-token", http_client=client)


def test_failed_job_raises_managed_audit_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"job_id": "job-1"})
        return httpx.Response(200, json={"status": "failed"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://aletheore.com")
    with pytest.raises(ManagedAuditError, match="failed"):
        run_managed_audit_request({"scanned_at": "x"}, "real-token", http_client=client, poll_interval=0)


def test_timeout_raises_managed_audit_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"job_id": "job-1"})
        return httpx.Response(200, json={"status": "pending"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://aletheore.com")
    with pytest.raises(ManagedAuditError, match="timed out"):
        run_managed_audit_request(
            {"scanned_at": "x"}, "real-token", http_client=client, poll_interval=0, timeout=0.01
        )
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd prototype && python -m pytest tests/test_managed_audit_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aletheore.managed_audit_client'`

- [ ] **Step 4: Implement**

Create `prototype/aletheore/managed_audit_client.py`:

```python
import time

import httpx

from aletheore.toon_encoding import to_toon


class ManagedAuditError(Exception):
    pass


def run_managed_audit_request(
    evidence: dict,
    token: str,
    api_base_url: str = "https://aletheore.com",
    http_client: httpx.Client | None = None,
    poll_interval: float = 2.0,
    timeout: float = 300.0,
) -> str:
    client = http_client or httpx.Client(base_url=api_base_url)
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post("/v1/managed-audit", json={"evidence": to_toon(evidence)}, headers=headers)
    if response.status_code in (401, 402):
        raise ManagedAuditError(response.json().get("detail", "managed audit request rejected"))
    response.raise_for_status()
    job_id = response.json()["job_id"]

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status_response = client.get(f"/v1/managed-audit/{job_id}", headers=headers)
        status_response.raise_for_status()
        body = status_response.json()
        if body["status"] == "finished":
            return body["result"]
        if body["status"] == "failed":
            raise ManagedAuditError("managed audit job failed on the server")
        if poll_interval:
            time.sleep(poll_interval)

    raise ManagedAuditError(f"managed audit timed out after {timeout}s waiting for job {job_id}")
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd prototype && python -m pytest tests/test_managed_audit_client.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Wire the CLI flag**

Read `prototype/aletheore/cli.py`'s `audit` command and `_audit` function fresh before editing (already read fresh above). Add the import:

```python
from aletheore.credentials import get_api_key
from aletheore.managed_audit_client import ManagedAuditError, run_managed_audit_request
```

Add a new function near `_audit`:

```python
def _managed_audit(
    repo_path: str,
    token: str | None,
    check_vulnerabilities: bool,
    scan_git_history: bool,
    check_licenses: bool,
    map_endpoints: bool,
) -> int:
    resolved_token = token or get_api_key("ALETHEORE_API_TOKEN", "aletheore-managed-audit")
    if not resolved_token:
        console.print("[bold red]error:[/bold red] no managed-audit token available")
        return 1

    _exit_code, evidence, evidence_path = _scan(
        repo_path, check_vulnerabilities, scan_git_history, check_licenses, map_endpoints
    )
    repo = Path(repo_path).resolve()

    console.print("Running managed audit (using Aletheore's shared key)...")
    try:
        with _ElapsedTicker("Waiting on the managed audit service"):
            report_text = run_managed_audit_request(evidence, resolved_token)
    except ManagedAuditError as exc:
        console.print(f"[bold red]error:[/bold red] {exc}")
        console.print(f"Evidence is still available at {evidence_path} for manual use.")
        return 1

    report_path = repo / ".aletheore" / "audit-report.md"
    report_path.write_text(report_text)
    console.print(f"[green]Managed audit report written to[/green] {report_path}")
    return 0
```

Modify the `audit` typer command to add the `--managed`/`--token` options and branch on them:

```python
@app.command(help="audit a repository")
def audit(
    path: str = typer.Argument(".", help="repository path"),
    agent: Optional[str] = typer.Option(None, "--agent", help="force a specific agent adapter by name"),
    managed: bool = typer.Option(
        False, "--managed", help="run the audit using Aletheore's shared managed key instead of BYOK"
    ),
    token: Optional[str] = typer.Option(
        None, "--token", help="managed-audit API token (or set ALETHEORE_API_TOKEN)"
    ),
    check_vulnerabilities: bool = typer.Option(
        True,
        "--check-vulnerabilities/--no-check-vulnerabilities",
        help="OSV.dev dependency-vulnerability check (on by default)",
    ),
    scan_git_history: bool = typer.Option(
        True,
        "--scan-git-history/--no-scan-git-history",
        help="walk git history for secrets (on by default)",
    ),
    check_licenses: bool = typer.Option(
        True,
        "--check-licenses/--no-check-licenses",
        help="dependency-license check (on by default)",
    ),
    map_endpoints: bool = typer.Option(
        True,
        "--map-endpoints/--no-map-endpoints",
        help="static API endpoint mapping (on by default)",
    ),
) -> None:
    if managed:
        raise typer.Exit(
            code=_managed_audit(path, token, check_vulnerabilities, scan_git_history, check_licenses, map_endpoints)
        )
    raise typer.Exit(
        code=_audit(path, agent, check_vulnerabilities, scan_git_history, check_licenses, map_endpoints)
    )
```

- [ ] **Step 7: Run the prototype suite**

Run: `cd prototype && python -m pytest -q`
Expected: PASS (all tests, no regressions to the existing `audit` command's tests)

- [ ] **Step 8: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add prototype/pyproject.toml prototype/aletheore/managed_audit_client.py prototype/aletheore/cli.py \
        prototype/tests/test_managed_audit_client.py
git commit -m "feat: aletheore audit --managed - CLI-triggered managed audits via personal API token"
```

---

## Task 11: MCP tool for managed audits

**Files:**
- Modify: `prototype/aletheore/mcp_server.py`
- Test: `prototype/tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `run_managed_audit_request` (Task 10), `read_evidence` (already in `mcp_server.py`).
- Produces: MCP tool `aletheore_managed_audit`.

- [ ] **Step 1: Check the existing MCP test file's pattern**

Read `prototype/tests/test_mcp_server.py` fresh to confirm the exact test style used for other hand-registered tools (e.g. how `aletheore_symbol_source` or `aletheore_healthcheck` are tested) before writing a new test in the same style - this file's own conventions take precedence over any assumption made here.

- [ ] **Step 2: Write the failing test**

Append to `prototype/tests/test_mcp_server.py` (matching whatever async-tool-call pattern the existing tests in that file already use - if they call `build_server(repo_path)` then invoke a tool via the FastMCP test client, follow that exact pattern rather than inventing a new one):

```python
def test_managed_audit_tool_registered_and_calls_client(tmp_path, monkeypatch):
    import json

    repo_path = tmp_path
    (repo_path / ".aletheore").mkdir()
    (repo_path / ".aletheore" / "evidence.json").write_text(json.dumps({"scanned_at": "x"}))

    monkeypatch.setattr(
        "aletheore.mcp_server.run_managed_audit_request",
        lambda evidence, token, **kwargs: "# Report\n\nmanaged audit text",
    )
    monkeypatch.setenv("ALETHEORE_API_TOKEN", "real-token")

    from aletheore.mcp_server import build_server

    server = build_server(repo_path)
    tool_names = [tool.name for tool in server._tool_manager.list_tools()]
    assert "aletheore_managed_audit" in tool_names
```

Note: `server._tool_manager.list_tools()` matches FastMCP's introspection API used elsewhere for tool-registration assertions in this codebase - **confirm this against the actual existing tests in `test_mcp_server.py` before relying on it**, since the exact attribute name is being inferred here, not re-verified against a fresh read at plan-writing time for this specific assertion style.

- [ ] **Step 3: Run to verify it fails**

Run: `cd prototype && python -m pytest tests/test_mcp_server.py -k managed_audit -v`
Expected: FAIL (tool not registered yet - exact failure message depends on the assertion style confirmed in Step 1)

- [ ] **Step 4: Implement**

Modify `prototype/aletheore/mcp_server.py` — add the import:

```python
from aletheore.managed_audit_client import run_managed_audit_request
```

Add a new registration function, following the exact shape of `_register_symbol_source_tool`:

```python
def _register_managed_audit_tool(mcp_instance: FastMCP, repo_path: Path) -> None:
    @mcp_instance.tool(name="aletheore_managed_audit")
    def aletheore_managed_audit(token: str | None = None) -> str:
        """Run a full audit report using Aletheore's shared managed key (requires a paid API token)."""
        import os

        resolved_token = token or os.environ.get("ALETHEORE_API_TOKEN")
        if not resolved_token:
            return _toon_result({"error": "no managed-audit token available (set ALETHEORE_API_TOKEN or pass token)"})
        evidence = read_evidence(repo_path)
        report_text = run_managed_audit_request(evidence, resolved_token)
        return _toon_result({"report": report_text})
```

Modify `build_server` to call it, alongside `_register_symbol_source_tool`:

```python
    _register_managed_audit_tool(mcp_instance, repo_path)
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd prototype && python -m pytest tests/test_mcp_server.py -k managed_audit -v`
Expected: PASS

- [ ] **Step 6: Run the full prototype suite**

Run: `cd prototype && python -m pytest -q`
Expected: PASS (all tests)

- [ ] **Step 7: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add prototype/aletheore/mcp_server.py prototype/tests/test_mcp_server.py
git commit -m "feat: aletheore_managed_audit MCP tool"
```

---

## Task 12: Deployment — apply migration, update the live App, verify end-to-end

This task has no pytest steps for most of it — it's real infrastructure changes against the live server and the live GitHub App, verified the same way every prior deployment task this session was: real commands, real output checked, not assumed.

**Files:** none (server-side operations + manual GitHub App settings changes)

- [ ] **Step 1: Update `.env.example` and `github-app/README.md` to document the new required variables**

Modify `github-app/.env.example` — add after the existing `GITHUB_WEBHOOK_SECRET=` line:

```
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
SESSION_SECRET=
PUBLIC_BASE_URL=https://aletheore.com
ANTHROPIC_API_KEY=
```

Modify `github-app/README.md`'s "Deploying on KVM4" section to add these two steps after the existing webhook/permission steps:

```markdown
8. Add the App's Client ID/Client Secret (visible on its settings page), a random
   `SESSION_SECRET` (`python3 -c "import secrets; print(secrets.token_hex(32))"`), and a real
   `ANTHROPIC_API_KEY` (Aletheore's own shared key, used only server-side for managed audits) to
   `.env`.
9. Add the `checks: write` permission and subscribe to the `issue_comment` event in the App's
   settings (existing installers will be prompted to approve the new permission). Add
   `https://aletheore.com/auth/callback` as a Callback URL under "Identifying and authorizing
   users".
10. Apply `migrations/002_paid_tier.sql` to the live database (it will not run automatically -
    `docker-entrypoint-initdb.d` only executes against a fresh, empty Postgres data directory):
    `docker compose exec -T postgres psql -U aletheore -d aletheore_app < migrations/002_paid_tier.sql`
```

- [ ] **Step 2: Commit the docs**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/.env.example github-app/README.md
git commit -m "docs(github-app): document paid-tier env vars and deploy steps"
git push origin master
```

- [ ] **Step 3: On the KVM4 server - pull, add the new `.env` values**

```bash
ssh $PROD_SSH "cd /root/aletheore && git pull --ff-only origin master"
```

Generate a real session secret and edit `/root/aletheore/github-app/.env` on the server to add real values for `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` (both visible on the App's settings page), `SESSION_SECRET` (generate with the command shown in Step 1), `PUBLIC_BASE_URL=https://aletheore.com`, and a real `ANTHROPIC_API_KEY`.

- [ ] **Step 4: Update the GitHub App's settings (manual, in the browser)**

1. Go to the App's settings page.
2. Under "Identifying and authorizing users", add Callback URL `https://aletheore.com/auth/callback`.
3. Under "Permissions & events" → Repository permissions, add **Checks: Read and write**.
4. Under "Subscribe to events", check **Issue comment**.
5. Save. Existing installers will see a pending-approval notice for the new permission.

- [ ] **Step 5: Apply migration 002 to the live database**

```bash
ssh $PROD_SSH "cd /root/aletheore/github-app && docker compose exec -T postgres psql -U aletheore -d aletheore_app < migrations/002_paid_tier.sql"
```

Expected: `ALTER TABLE` x2, `CREATE TABLE` x2, `CREATE INDEX`, no errors.

- [ ] **Step 6: Rebuild and restart**

```bash
ssh $PROD_SSH "cd /root/aletheore/github-app && docker compose up -d --build"
```

Confirm zero restarts the same way every prior deploy in this session was verified - `docker compose ps` shows all services `Up`, then check `docker inspect <container> --format '{{.RestartCount}}'` for `app-server`, `scan-worker`, `postgres`, `redis`, `caddy` all report `0`.

- [ ] **Step 7: Real end-to-end verification (Success Criteria 1-6 from the spec)**

1. Visit `https://aletheore.com/auth/login` in a real browser, log in with a real GitHub account that administers the installed App - confirm it redirects through GitHub and lands back on `/dashboard` with a session cookie set.
2. From the admin page, generate a real API token; confirm it's shown once and the response's `id` matches a row visible via `list_api_tokens`.
3. Run `aletheore audit --managed --token <that-token>` against a real local repo; confirm a real report is written to `.aletheore/audit-report.md`.
4. On a real installed, paid repo, comment `/aletheore audit` on an open PR; confirm a reply comment with a real report appears within a few minutes.
5. Set a real Slack (or Teams) incoming webhook URL via the admin route; push a commit introducing a real (test) secret to that PR; confirm the Slack channel receives a message and the PR's Check Run turns `failure`; confirm a clean push turns it back to `success`.
6. Re-run the full test suite one more time (`TEST_DATABASE_URL=... python -m pytest -q` in `github-app/`, `python -m pytest -q` in `prototype/`) and confirm the free tier's own prior verification (real webhook, real 0-restart deploy) still holds - nothing in this task's changes should have touched the free-tier code paths' behavior, only added new ones alongside them.
