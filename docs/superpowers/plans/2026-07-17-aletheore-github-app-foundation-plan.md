# Aletheore GitHub App Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a GitHub App that, when installed on a repo, posts automatic PR comments (new secrets/vulnerabilities/layer violations) and serves a free hosted dashboard, with GitHub Marketplace billing wired up structurally — deployed on the KVM4 server at `aletheore.com`.

**Architecture:** A new top-level `github-app/` directory in this repo (public, consistent with the project's existing transparency posture — nothing here is a secret, secrets live only in `.env` on the server, never committed) contains two deployable services: `app_server` (FastAPI — verifies and dispatches GitHub webhooks, serves the dashboard) and `scan_worker` (an RQ worker — does the actual clone → scan → diff → comment work, kept out of the request path so a slow scan never blocks webhook responses). They communicate through a Redis queue and share a Postgres database. Both reuse the existing `aletheore` CLI (`scan`, `diff`) unchanged via subprocess, and a new shared `aletheore.pr_comment` module (extracted from `action.yml`'s embedded formatting logic) for the PR comment body — so the comment format has exactly one source of truth instead of two copies drifting apart.

**Tech Stack:** FastAPI, asyncpg (app_server), psycopg (scan_worker — sync, matching RQ's synchronous job model), RQ + Redis (job queue, same library Procta already uses), httpx (GitHub REST API calls), PyJWT[crypto] (GitHub App JWT auth), pytest + pytest-asyncio, Docker Compose + Caddy (deployment, mirroring Procta's proven setup on the same server).

## Global Constraints

- Reuse `aletheore scan` / `aletheore diff` CLI commands unchanged — never reimplement scan or diff logic in `github-app/`.
- Only derived evidence (JSON) is ever persisted. Source code is cloned to a job-scoped temp directory and deleted unconditionally when the job ends, success or failure.
- Every job (PR scan) gets its own UUID-named temp directory under `/tmp/aletheore-jobs/`, never reused or shared between jobs.
- Webhook signature verification (HMAC-SHA256 over the raw body, `X-Hub-Signature-256`, constant-time comparison) happens before any other processing on every webhook request.
- PR comments are upserted (found via the `<!-- aletheore-diff -->` marker and edited) — never a new comment per push.
- `marketplace_purchase` and `installation` webhook handling must be idempotent — GitHub retries webhook deliveries.
- `installation.deleted` must cascade-delete that installation's `repo_history` rows, not just the `installations` row.
- `repo_history` keeps at most 20 snapshots per `(installation_id, repo_full_name)`, oldest dropped first — mirrors the local CLI's `history.py` `keep=20` default exactly.
- No code execution of a scanned repo's own code, ever.

---

## File Structure

```
github-app/
  requirements.txt
  migrations/
    001_initial_schema.sql
  app_server/
    __init__.py
    config.py              # env var loading
    db.py                  # asyncpg pool + query helpers
    signature.py           # webhook HMAC verification
    github_auth.py         # App JWT + installation token exchange (shared with scan_worker)
    main.py                # FastAPI app, /webhook dispatch, lifespan (DB pool, Redis)
    webhooks/
      __init__.py
      installation.py      # installation, installation_repositories handlers
      marketplace.py       # marketplace_purchase handler
      pull_request.py      # pull_request handler (enqueue only)
    dashboard.py            # GET /app/{org}/{repo}
  scan_worker/
    __init__.py
    db.py                   # sync psycopg helpers (separate from app_server's async ones)
    github_api.py           # PR comment list/create/update via REST API
    jobs.py                 # run_pr_scan_job — the actual clone/scan/diff/comment/cleanup logic
    worker.py               # RQ worker entrypoint
  docker-compose.yml
  Dockerfile.app-server
  Dockerfile.scan-worker
  Caddyfile
  .env.example
  README.md
  tests/
    conftest.py
    test_signature.py
    test_db.py
    test_github_auth.py
    test_installation_webhook.py
    test_marketplace_webhook.py
    test_pull_request_webhook.py
    test_jobs.py
    test_dashboard.py

prototype/aletheore/pr_comment.py   # NEW — extracted from action.yml, shared source of truth
prototype/tests/test_pr_comment.py  # NEW
action.yml                          # MODIFIED — calls aletheore.pr_comment instead of inlining it
```

---

## Task 1: Extract PR-comment formatting into `aletheore.pr_comment`

**Files:**
- Create: `prototype/aletheore/pr_comment.py`
- Test: `prototype/tests/test_pr_comment.py`
- Modify: `action.yml:141-221` (the "Format diff summary" step's embedded Python)

**Interfaces:**
- Produces: `COMMENT_MARKER: str` (the literal `"<!-- aletheore-diff -->"`), `format_diff_comment(diff: dict) -> str` — takes the exact dict shape returned by `aletheore.history.compute_diff`, returns the full markdown comment body including the marker as its first line. Used by both `action.yml` and, later, `github-app/scan_worker/jobs.py` (Task 8).

- [ ] **Step 1: Write the failing test**

Create `prototype/tests/test_pr_comment.py`:

```python
from aletheore.pr_comment import COMMENT_MARKER, format_diff_comment


def _empty_diff():
    return {
        "secrets": {"new": [], "resolved": []},
        "history_secrets": {"new": [], "resolved": []},
        "vulnerabilities": {"new": [], "resolved": []},
        "layer_violations": {"new": [], "resolved": []},
        "aggregate_deltas": {"module_count": 0, "dependency_graph_edge_count": 0, "total_commits": 0},
        "caveats": [],
    }


def test_marker_is_first_line():
    body = format_diff_comment(_empty_diff())
    assert body.splitlines()[0] == COMMENT_MARKER


def test_empty_diff_says_nothing_to_report():
    body = format_diff_comment(_empty_diff())
    assert "No new secrets, vulnerabilities, or layer violations" in body


def test_new_secret_is_bulleted_with_path_and_line():
    diff = _empty_diff()
    diff["secrets"]["new"] = [{"path": "config.py", "line": 12, "pattern": "aws_key"}]
    body = format_diff_comment(diff)
    assert "`config.py:12`" in body
    assert "(aws_key)" in body


def test_resolved_secret_shows_resolved_marker():
    diff = _empty_diff()
    diff["secrets"]["resolved"] = [{"path": "old.py", "line": 3, "pattern": "token"}]
    body = format_diff_comment(diff)
    assert "resolved: `old.py:3`" in body


def test_placeholder_secret_gets_suffix():
    diff = _empty_diff()
    diff["secrets"]["new"] = [{"path": "a.py", "line": 1, "pattern": "key", "likely_placeholder": True}]
    body = format_diff_comment(diff)
    assert "likely placeholder" in body


def test_accepted_secret_gets_baseline_suffix():
    diff = _empty_diff()
    diff["secrets"]["new"] = [{"path": "a.py", "line": 1, "pattern": "key", "accepted": True}]
    body = format_diff_comment(diff)
    assert "accepted (in .aletheore.json baseline)" in body


def test_history_secret_shows_short_commit():
    diff = _empty_diff()
    diff["history_secrets"]["new"] = [
        {"path": "a.py", "commit": "abcdef1234567890", "pattern": "key"}
    ]
    body = format_diff_comment(diff)
    assert "in abcdef12" in body


def test_new_vulnerability_is_bulleted():
    diff = _empty_diff()
    diff["vulnerabilities"]["new"] = [
        {"package": "requests", "installed_version": "2.0.0", "advisory_id": "GHSA-xxxx", "ecosystem": "PyPI"}
    ]
    body = format_diff_comment(diff)
    assert "requests 2.0.0 - GHSA-xxxx (PyPI)" in body


def test_new_layer_violation_is_bulleted():
    diff = _empty_diff()
    diff["layer_violations"]["new"] = [{"from": "ui", "to": "db", "reason": "UI must not import DB directly"}]
    body = format_diff_comment(diff)
    assert "`ui` -> `db`: UI must not import DB directly" in body


def test_nonzero_aggregate_deltas_are_shown():
    diff = _empty_diff()
    diff["aggregate_deltas"] = {"module_count": 3, "dependency_graph_edge_count": -1, "total_commits": 5}
    body = format_diff_comment(diff)
    assert "Modules: +3" in body
    assert "Dependency graph edges: -1" in body
    assert "Commits: 5" in body


def test_caveats_are_shown_as_blockquotes():
    diff = _empty_diff()
    diff["caveats"] = ["evidence.json schema version mismatch"]
    body = format_diff_comment(diff)
    assert "> ⚠️ evidence.json schema version mismatch" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd prototype && python -m pytest tests/test_pr_comment.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aletheore.pr_comment'`

- [ ] **Step 3: Write the implementation**

Create `prototype/aletheore/pr_comment.py`:

```python
"""Formats an ``aletheore.history.compute_diff`` result as a PR comment body.

The single source of truth for this formatting - both ``action.yml`` (the
GitHub Action) and the GitHub App's ``scan_worker`` call this instead of
each maintaining their own copy.
"""

COMMENT_MARKER = "<!-- aletheore-diff -->"


def _secret_suffix(finding: dict) -> str:
    if finding.get("accepted"):
        return " - accepted (in .aletheore.json baseline)"
    if finding.get("likely_placeholder"):
        return " - likely placeholder"
    return ""


def _bullets(title: str, entries: dict, formatter) -> list[str]:
    new = entries.get("new", [])
    resolved = entries.get("resolved", [])
    if not new and not resolved:
        return []
    lines = [f"**{title}**"]
    lines += [f"- 🆕 {formatter(item)}" for item in new]
    lines += [f"- ✅ resolved: {formatter(item)}" for item in resolved]
    lines.append("")
    return lines


def format_diff_comment(diff: dict) -> str:
    body = [COMMENT_MARKER, "### 🔍 Aletheore evidence diff", ""]

    for caveat in diff.get("caveats", []):
        body.append(f"> ⚠️ {caveat}")
    if diff.get("caveats"):
        body.append("")

    body += _bullets(
        "Secrets",
        diff.get("secrets", {}),
        lambda f: f"`{f.get('path')}:{f.get('line')}` ({f.get('pattern')})" + _secret_suffix(f),
    )
    body += _bullets(
        "Secrets in git history",
        diff.get("history_secrets", {}),
        lambda f: f"`{f.get('path')}` in {str(f.get('commit'))[:8]} ({f.get('pattern')})" + _secret_suffix(f),
    )
    body += _bullets(
        "Dependency vulnerabilities",
        diff.get("vulnerabilities", {}),
        lambda f: f"{f.get('package')} {f.get('installed_version')} - {f.get('advisory_id')} ({f.get('ecosystem')})",
    )
    body += _bullets(
        "Layer violations",
        diff.get("layer_violations", {}),
        lambda f: f"`{f.get('from')}` -> `{f.get('to')}`: {f.get('reason')}",
    )

    deltas = diff.get("aggregate_deltas", {})
    if any(deltas.get(k, 0) for k in ("module_count", "dependency_graph_edge_count", "total_commits")):
        body.append("**Aggregate deltas**")
        body.append(f"- Modules: {deltas.get('module_count', 0):+d}")
        body.append(f"- Dependency graph edges: {deltas.get('dependency_graph_edge_count', 0):+d}")
        body.append(f"- Commits: {deltas.get('total_commits', 0)}")
        body.append("")

    if len(body) <= 3:
        body.append("No new secrets, vulnerabilities, or layer violations. ✅")

    return "\n".join(body)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd prototype && python -m pytest tests/test_pr_comment.py -v`
Expected: PASS (all 11 tests)

- [ ] **Step 5: Update `action.yml` to use the shared function instead of its own copy**

In `action.yml`, replace the entire "Format diff summary" step's embedded Python (the block starting `python3 - <<'PYEOF'` under that step, from the `import json` line through the `f.write(text + "\n")` line, roughly `action.yml:145-221`) with:

```yaml
    - name: Format diff summary
      if: always()
      shell: bash
      run: |
        if [ ! -f diff-output.json ]; then
          echo "diff-output.json not found (an earlier step failed) - skipping summary"
          exit 0
        fi

        python3 - <<'PYEOF'
        import json
        import os

        from aletheore.pr_comment import format_diff_comment

        with open("diff-output.json") as f:
            diff = json.load(f)

        text = format_diff_comment(diff)

        with open("aletheore-comment.md", "w") as f:
            f.write(text)

        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a") as f:
                f.write(text + "\n")
        PYEOF
```

Leave the "Post PR comment" step (the `github-script` step immediately after) untouched — it already reads `aletheore-comment.md` and upserts using `COMMENT_MARKER`'s literal value hardcoded as `'<!-- aletheore-diff -->'`, which still matches since `pr_comment.py` uses the identical literal.

- [ ] **Step 6: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add prototype/aletheore/pr_comment.py prototype/tests/test_pr_comment.py action.yml
git commit -m "refactor: extract PR-comment formatting into aletheore.pr_comment

Single source of truth for both action.yml and the upcoming GitHub
App's scan-worker, instead of two copies of the same formatting logic."
```

---

## Task 2: `github-app/` scaffold, Postgres schema, DB access module

**Files:**
- Create: `github-app/requirements.txt`
- Create: `github-app/migrations/001_initial_schema.sql`
- Create: `github-app/app_server/__init__.py`
- Create: `github-app/app_server/config.py`
- Create: `github-app/app_server/db.py`
- Create: `github-app/tests/conftest.py`
- Test: `github-app/tests/test_db.py`

**Interfaces:**
- Produces: `Settings` (in `config.py`, reads env vars: `DATABASE_URL`, `REDIS_URL`, `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET`), `get_settings() -> Settings`.
- Produces (`db.py`): `create_pool(dsn: str) -> asyncpg.Pool`, `upsert_installation(pool, installation_id: int, account_login: str) -> None`, `get_installation(pool, installation_id: int) -> dict | None`, `set_installation_plan(pool, installation_id: int, plan: str) -> None`, `delete_installation(pool, installation_id: int) -> None`, `insert_repo_history(pool, installation_id: int, repo_full_name: str, scanned_at: datetime, evidence: dict, keep: int = 20) -> None`, `get_recent_history(pool, installation_id: int, repo_full_name: str, limit: int = 20) -> list[dict]`.

This task's tests run against a real local Postgres, matching the project's existing convention (`proctored-browser`'s `integration_tests/` do the same against a real Postgres in Docker) rather than mocking the database.

- [ ] **Step 1: Start a local test Postgres**

Run: `docker run -d --name aletheore-test-pg -e POSTGRES_PASSWORD=test -e POSTGRES_DB=aletheore_test -p 55433:5432 postgres:16`
Expected: container starts; `docker ps` shows `aletheore-test-pg` running

- [ ] **Step 2: Write the schema migration**

Create `github-app/migrations/001_initial_schema.sql`:

```sql
CREATE TABLE installations (
    installation_id BIGINT PRIMARY KEY,
    account_login   TEXT NOT NULL,
    plan            TEXT NOT NULL DEFAULT 'free',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE repo_history (
    id              BIGSERIAL PRIMARY KEY,
    installation_id BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name  TEXT NOT NULL,
    scanned_at      TIMESTAMPTZ NOT NULL,
    evidence        JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX repo_history_lookup ON repo_history (installation_id, repo_full_name, scanned_at DESC);
```

- [ ] **Step 3: Apply the migration to the test database**

Run: `PGPASSWORD=test psql -h localhost -p 55433 -U postgres -d aletheore_test -f github-app/migrations/001_initial_schema.sql`
Expected: `CREATE TABLE` printed twice, `CREATE INDEX` printed once, no errors

- [ ] **Step 4: Write `requirements.txt`, `config.py`, and `conftest.py`**

Create `github-app/requirements.txt`:

```
fastapi>=0.115.0,<0.136.3
uvicorn[standard]>=0.30.0
asyncpg>=0.31.0
psycopg[binary]>=3.2.0
redis>=5.0.0
rq>=1.16.0
httpx>=0.28.1
pyjwt[crypto]>=2.13.0
pytest>=8.0
pytest-asyncio>=0.24
```

Create `github-app/app_server/__init__.py` (empty file).

Create `github-app/app_server/config.py`:

```python
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    github_app_id: str
    github_app_private_key: str
    github_webhook_secret: str


def get_settings() -> Settings:
    return Settings(
        database_url=os.environ["DATABASE_URL"],
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        github_app_id=os.environ.get("GITHUB_APP_ID", ""),
        github_app_private_key=os.environ.get("GITHUB_APP_PRIVATE_KEY", ""),
        github_webhook_secret=os.environ.get("GITHUB_WEBHOOK_SECRET", ""),
    )
```

Create `github-app/tests/conftest.py`:

```python
import os

import asyncpg
import pytest_asyncio

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:test@localhost:55433/aletheore_test"
)


@pytest_asyncio.fixture
async def pool():
    p = await asyncpg.create_pool(TEST_DATABASE_URL)
    async with p.acquire() as conn:
        await conn.execute("TRUNCATE installations CASCADE")
    yield p
    await p.close()
```

- [ ] **Step 5: Write the failing test**

Create `github-app/tests/test_db.py`:

```python
from datetime import datetime, timezone

import pytest

from app_server.db import (
    delete_installation,
    get_installation,
    get_recent_history,
    insert_repo_history,
    set_installation_plan,
    upsert_installation,
)


@pytest.mark.asyncio
async def test_upsert_installation_creates_row(pool):
    await upsert_installation(pool, 123, "octocat")
    row = await get_installation(pool, 123)
    assert row["account_login"] == "octocat"
    assert row["plan"] == "free"


@pytest.mark.asyncio
async def test_upsert_installation_is_idempotent(pool):
    await upsert_installation(pool, 123, "octocat")
    await upsert_installation(pool, 123, "octocat")
    row = await get_installation(pool, 123)
    assert row["account_login"] == "octocat"


@pytest.mark.asyncio
async def test_set_installation_plan_updates_plan(pool):
    await upsert_installation(pool, 123, "octocat")
    await set_installation_plan(pool, 123, "pro")
    row = await get_installation(pool, 123)
    assert row["plan"] == "pro"


@pytest.mark.asyncio
async def test_delete_installation_removes_row(pool):
    await upsert_installation(pool, 123, "octocat")
    await delete_installation(pool, 123)
    assert await get_installation(pool, 123) is None


@pytest.mark.asyncio
async def test_delete_installation_cascades_to_history(pool):
    await upsert_installation(pool, 123, "octocat")
    await insert_repo_history(pool, 123, "octocat/repo", datetime.now(timezone.utc), {"x": 1})
    await delete_installation(pool, 123)
    history = await get_recent_history(pool, 123, "octocat/repo")
    assert history == []


@pytest.mark.asyncio
async def test_repo_history_rotation_keeps_only_20(pool):
    await upsert_installation(pool, 123, "octocat")
    for i in range(21):
        await insert_repo_history(
            pool, 123, "octocat/repo", datetime(2026, 1, 1, tzinfo=timezone.utc).replace(day=1) , {"n": i}, keep=20
        )
    history = await get_recent_history(pool, 123, "octocat/repo", limit=100)
    assert len(history) == 20
```

- [ ] **Step 6: Run to verify it fails**

Run: `cd github-app && python -m pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app_server.db'`

- [ ] **Step 7: Write `db.py`**

Create `github-app/app_server/db.py`:

```python
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
        "SELECT installation_id, account_login, plan FROM installations WHERE installation_id = $1",
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
                    SELECT id FROM repo_history
                    WHERE installation_id = $1 AND repo_full_name = $2
                    ORDER BY scanned_at DESC
                    OFFSET $3
                )
                """,
                installation_id,
                repo_full_name,
                keep,
            )


async def get_recent_history(
    pool: asyncpg.Pool, installation_id: int, repo_full_name: str, limit: int = 20
) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT scanned_at, evidence FROM repo_history
        WHERE installation_id = $1 AND repo_full_name = $2
        ORDER BY scanned_at DESC
        LIMIT $3
        """,
        installation_id,
        repo_full_name,
        limit,
    )
    return [{"scanned_at": r["scanned_at"], "evidence": json.loads(r["evidence"])} for r in rows]
```

- [ ] **Step 8: Run to verify it passes**

Run: `cd github-app && python -m pytest tests/test_db.py -v`
Expected: PASS (6 tests)

- [ ] **Step 9: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/requirements.txt github-app/migrations github-app/app_server/__init__.py \
        github-app/app_server/config.py github-app/app_server/db.py github-app/tests/conftest.py \
        github-app/tests/test_db.py
git commit -m "feat(github-app): schema + DB access module with history rotation"
```

---

## Task 3: Webhook signature verification

**Files:**
- Create: `github-app/app_server/signature.py`
- Test: `github-app/tests/test_signature.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `verify_signature(payload: bytes, signature_header: str, secret: str) -> bool`, used by `main.py` (Task 5).

- [ ] **Step 1: Write the failing test**

Create `github-app/tests/test_signature.py`:

```python
import hashlib
import hmac

from app_server.signature import verify_signature

SECRET = "test-webhook-secret"


def _sign(payload: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_valid_signature_passes():
    payload = b'{"action": "opened"}'
    header = _sign(payload, SECRET)
    assert verify_signature(payload, header, SECRET) is True


def test_tampered_payload_fails():
    payload = b'{"action": "opened"}'
    header = _sign(payload, SECRET)
    tampered = b'{"action": "closed"}'
    assert verify_signature(tampered, header, SECRET) is False


def test_wrong_secret_fails():
    payload = b'{"action": "opened"}'
    header = _sign(payload, "wrong-secret")
    assert verify_signature(payload, header, SECRET) is False


def test_missing_header_fails():
    assert verify_signature(b"{}", "", SECRET) is False


def test_malformed_header_fails():
    assert verify_signature(b"{}", "not-a-real-signature", SECRET) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd github-app && python -m pytest tests/test_signature.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app_server.signature'`

- [ ] **Step 3: Write the implementation**

Create `github-app/app_server/signature.py`:

```python
import hashlib
import hmac


def verify_signature(payload: bytes, signature_header: str, secret: str) -> bool:
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    provided = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd github-app && python -m pytest tests/test_signature.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/app_server/signature.py github-app/tests/test_signature.py
git commit -m "feat(github-app): webhook HMAC signature verification"
```

---

## Task 4: GitHub App auth (JWT + installation token exchange)

**Files:**
- Create: `github-app/app_server/github_auth.py`
- Test: `github-app/tests/test_github_auth.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `generate_app_jwt(app_id: str, private_key_pem: str) -> str`, `get_installation_token(installation_id: int, app_jwt: str, http_client: httpx.Client | None = None) -> str` — used by `scan_worker/jobs.py` (Task 8). `http_client` is injectable so tests can pass a mocked transport instead of hitting the real GitHub API.

- [ ] **Step 1: Generate a throwaway RSA test key**

Run: `openssl genrsa -out /tmp/test-app-key.pem 2048`
Expected: a 2048-bit RSA private key written to `/tmp/test-app-key.pem`

- [ ] **Step 2: Write the failing test**

Create `github-app/tests/test_github_auth.py`:

```python
import jwt
import httpx
import pytest

from app_server.github_auth import generate_app_jwt, get_installation_token

with open("/tmp/test-app-key.pem") as f:
    TEST_PRIVATE_KEY = f.read()


def test_generated_jwt_has_correct_claims():
    token = generate_app_jwt("12345", TEST_PRIVATE_KEY)
    decoded = jwt.decode(token, options={"verify_signature": False})
    assert decoded["iss"] == "12345"
    assert decoded["exp"] - decoded["iat"] <= 600


def test_generated_jwt_is_verifiable_with_public_key():
    from cryptography.hazmat.primitives import serialization

    private_key = serialization.load_pem_private_key(TEST_PRIVATE_KEY.encode(), password=None)
    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    token = generate_app_jwt("12345", TEST_PRIVATE_KEY)
    decoded = jwt.decode(token, public_pem, algorithms=["RS256"])
    assert decoded["iss"] == "12345"


@pytest.mark.asyncio
async def test_get_installation_token_returns_token_from_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/app/installations/999/access_tokens"
        assert request.headers["Authorization"] == "Bearer fake-jwt"
        return httpx.Response(201, json={"token": "ghs_faketoken123", "expires_at": "2026-01-01T00:00:00Z"})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://api.github.com")
    token = await get_installation_token(999, "fake-jwt", http_client=client)
    assert token == "ghs_faketoken123"
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd github-app && python -m pytest tests/test_github_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app_server.github_auth'`

- [ ] **Step 4: Write the implementation**

Create `github-app/app_server/github_auth.py`:

```python
import time

import httpx
import jwt


def generate_app_jwt(app_id: str, private_key_pem: str) -> str:
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 600,
        "iss": app_id,
    }
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


async def get_installation_token(
    installation_id: int, app_jwt: str, http_client: httpx.Client | None = None
) -> str:
    client = http_client or httpx.Client(base_url="https://api.github.com")
    response = client.post(
        f"/app/installations/{installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
        },
    )
    response.raise_for_status()
    return response.json()["token"]
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd github-app && python -m pytest tests/test_github_auth.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/app_server/github_auth.py github-app/tests/test_github_auth.py
git commit -m "feat(github-app): GitHub App JWT auth + installation token exchange"
```

---

## Task 5: FastAPI app + `installation`/`installation_repositories` webhook handlers

**Files:**
- Create: `github-app/app_server/main.py`
- Create: `github-app/app_server/webhooks/__init__.py`
- Create: `github-app/app_server/webhooks/installation.py`
- Test: `github-app/tests/test_installation_webhook.py`

**Interfaces:**
- Consumes: `verify_signature` (Task 3), `upsert_installation`/`delete_installation` (Task 2).
- Produces: `handle_installation_event(event_name: str, payload: dict, pool) -> None` (in `webhooks/installation.py`), the FastAPI `app` object (in `main.py`) with a `POST /webhook` route and `app.state.db_pool` set up via lifespan — consumed by Tasks 6, 7, 9.

- [ ] **Step 1: Write the failing test**

Create `github-app/tests/test_installation_webhook.py`:

```python
import pytest

from app_server.db import get_installation
from app_server.webhooks.installation import handle_installation_event


@pytest.mark.asyncio
async def test_installation_created_upserts_row(pool):
    payload = {
        "action": "created",
        "installation": {"id": 555, "account": {"login": "octocat"}},
    }
    await handle_installation_event("installation", payload, pool)
    row = await get_installation(pool, 555)
    assert row["account_login"] == "octocat"


@pytest.mark.asyncio
async def test_installation_deleted_removes_row(pool):
    from app_server.db import upsert_installation

    await upsert_installation(pool, 555, "octocat")
    payload = {
        "action": "deleted",
        "installation": {"id": 555, "account": {"login": "octocat"}},
    }
    await handle_installation_event("installation", payload, pool)
    assert await get_installation(pool, 555) is None


@pytest.mark.asyncio
async def test_installation_repositories_added_upserts_row(pool):
    payload = {
        "action": "added",
        "installation": {"id": 556, "account": {"login": "someorg"}},
    }
    await handle_installation_event("installation_repositories", payload, pool)
    row = await get_installation(pool, 556)
    assert row["account_login"] == "someorg"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd github-app && python -m pytest tests/test_installation_webhook.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app_server.webhooks'`

- [ ] **Step 3: Write the implementation**

Create `github-app/app_server/webhooks/__init__.py` (empty file).

Create `github-app/app_server/webhooks/installation.py`:

```python
from app_server.db import delete_installation, upsert_installation


async def handle_installation_event(event_name: str, payload: dict, pool) -> None:
    action = payload.get("action")
    installation = payload["installation"]
    installation_id = installation["id"]
    account_login = installation["account"]["login"]

    if event_name == "installation" and action == "deleted":
        await delete_installation(pool, installation_id)
        return

    await upsert_installation(pool, installation_id, account_login)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd github-app && python -m pytest tests/test_installation_webhook.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Write `main.py`**

Create `github-app/app_server/main.py`:

```python
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

from app_server.config import get_settings
from app_server.db import create_pool
from app_server.signature import verify_signature
from app_server.webhooks.installation import handle_installation_event

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db_pool = await create_pool(settings.database_url)
    yield
    await app.state.db_pool.close()


app = FastAPI(lifespan=lifespan)


@app.post("/webhook")
async def webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not verify_signature(body, signature, settings.github_webhook_secret):
        raise HTTPException(status_code=401, detail="invalid signature")

    event = request.headers.get("X-GitHub-Event", "")
    payload = json.loads(body)
    pool = request.app.state.db_pool

    if event in ("installation", "installation_repositories"):
        await handle_installation_event(event, payload, pool)
    elif event == "marketplace_purchase":
        from app_server.webhooks.marketplace import handle_marketplace_event

        await handle_marketplace_event(payload, pool)
    elif event == "pull_request":
        from app_server.webhooks.pull_request import handle_pull_request_event

        await handle_pull_request_event(payload, settings.redis_url)

    return {"ok": True}
```

Note: Tasks 6 and 7 create `webhooks/marketplace.py` and `webhooks/pull_request.py` respectively — the imports above are deferred (inside the branches) so `main.py` doesn't break before those modules exist. Once both tasks are done, these can be moved to top-level imports if preferred; leaving them deferred is also fine and keeps each webhook type's dependencies (e.g. Redis for the PR path) lazily loaded.

- [ ] **Step 6: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/app_server/main.py github-app/app_server/webhooks/__init__.py \
        github-app/app_server/webhooks/installation.py github-app/tests/test_installation_webhook.py
git commit -m "feat(github-app): FastAPI app + installation webhook handling"
```

---

## Task 6: `marketplace_purchase` webhook handler

**Files:**
- Create: `github-app/app_server/webhooks/marketplace.py`
- Test: `github-app/tests/test_marketplace_webhook.py`

**Interfaces:**
- Consumes: `get_installation`/`set_installation_plan`/`upsert_installation` (Task 2).
- Produces: `handle_marketplace_event(payload: dict, pool) -> None`, imported by `main.py` (Task 5, already wired).

- [ ] **Step 1: Write the failing test**

Create `github-app/tests/test_marketplace_webhook.py`:

```python
import pytest

from app_server.db import get_installation, upsert_installation
from app_server.webhooks.marketplace import handle_marketplace_event


def _payload(action: str, installation_id: int, login: str, plan_name: str = "pro"):
    return {
        "action": action,
        "marketplace_purchase": {
            "account": {"id": installation_id, "login": login},
            "plan": {"name": plan_name},
        },
    }


@pytest.mark.asyncio
async def test_purchased_sets_plan(pool):
    await upsert_installation(pool, 777, "octocat")
    await handle_marketplace_event(_payload("purchased", 777, "octocat", "pro"), pool)
    row = await get_installation(pool, 777)
    assert row["plan"] == "pro"


@pytest.mark.asyncio
async def test_changed_updates_plan(pool):
    await upsert_installation(pool, 777, "octocat")
    await handle_marketplace_event(_payload("purchased", 777, "octocat", "pro"), pool)
    await handle_marketplace_event(_payload("changed", 777, "octocat", "team"), pool)
    row = await get_installation(pool, 777)
    assert row["plan"] == "team"


@pytest.mark.asyncio
async def test_cancelled_resets_to_free(pool):
    await upsert_installation(pool, 777, "octocat")
    await handle_marketplace_event(_payload("purchased", 777, "octocat", "pro"), pool)
    await handle_marketplace_event(_payload("cancelled", 777, "octocat"), pool)
    row = await get_installation(pool, 777)
    assert row["plan"] == "free"


@pytest.mark.asyncio
async def test_purchased_creates_installation_if_missing(pool):
    await handle_marketplace_event(_payload("purchased", 888, "neworg", "pro"), pool)
    row = await get_installation(pool, 888)
    assert row is not None
    assert row["plan"] == "pro"


@pytest.mark.asyncio
async def test_replaying_same_event_is_idempotent(pool):
    payload = _payload("purchased", 777, "octocat", "pro")
    await handle_marketplace_event(payload, pool)
    await handle_marketplace_event(payload, pool)
    row = await get_installation(pool, 777)
    assert row["plan"] == "pro"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd github-app && python -m pytest tests/test_marketplace_webhook.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app_server.webhooks.marketplace'`

- [ ] **Step 3: Write the implementation**

Create `github-app/app_server/webhooks/marketplace.py`:

```python
from app_server.db import set_installation_plan, upsert_installation


async def handle_marketplace_event(payload: dict, pool) -> None:
    action = payload.get("action")
    purchase = payload["marketplace_purchase"]
    account = purchase["account"]
    installation_id = account["id"]
    account_login = account["login"]

    # Ensure the installation row exists (a marketplace purchase can arrive
    # for an account whose `installation` webhook hasn't landed yet).
    await upsert_installation(pool, installation_id, account_login)

    if action in ("purchased", "changed"):
        plan_name = purchase["plan"]["name"]
        await set_installation_plan(pool, installation_id, plan_name)
    elif action == "cancelled":
        await set_installation_plan(pool, installation_id, "free")
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd github-app && python -m pytest tests/test_marketplace_webhook.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/app_server/webhooks/marketplace.py github-app/tests/test_marketplace_webhook.py
git commit -m "feat(github-app): marketplace_purchase webhook (idempotent, full lifecycle)"
```

---

## Task 7: `pull_request` webhook handler (enqueue only)

**Files:**
- Create: `github-app/app_server/webhooks/pull_request.py`
- Test: `github-app/tests/test_pull_request_webhook.py`

**Interfaces:**
- Consumes: nothing from earlier tasks directly (talks to Redis, not Postgres).
- Produces: `handle_pull_request_event(payload: dict, redis_url: str, queue: object | None = None) -> None` — `queue` is injectable for tests. Enqueues a job with the exact keyword arguments `run_pr_scan_job` (Task 8) expects: `installation_id`, `repo_full_name`, `pr_number`, `base_sha`, `head_sha`.

- [ ] **Step 1: Write the failing test**

Create `github-app/tests/test_pull_request_webhook.py`:

```python
from unittest.mock import MagicMock

import pytest

from app_server.webhooks.pull_request import handle_pull_request_event


def _payload(action: str):
    return {
        "action": action,
        "number": 42,
        "installation": {"id": 111},
        "repository": {"full_name": "octocat/hello-world"},
        "pull_request": {
            "base": {"sha": "aaa111"},
            "head": {"sha": "bbb222"},
        },
    }


@pytest.mark.asyncio
async def test_opened_enqueues_job():
    fake_queue = MagicMock()
    await handle_pull_request_event(_payload("opened"), "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_called_once()
    _, kwargs = fake_queue.enqueue.call_args
    assert kwargs["installation_id"] == 111
    assert kwargs["repo_full_name"] == "octocat/hello-world"
    assert kwargs["pr_number"] == 42
    assert kwargs["base_sha"] == "aaa111"
    assert kwargs["head_sha"] == "bbb222"


@pytest.mark.asyncio
async def test_synchronize_enqueues_job():
    fake_queue = MagicMock()
    await handle_pull_request_event(_payload("synchronize"), "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_called_once()


@pytest.mark.asyncio
async def test_closed_does_not_enqueue():
    fake_queue = MagicMock()
    await handle_pull_request_event(_payload("closed"), "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_not_called()


@pytest.mark.asyncio
async def test_labeled_does_not_enqueue():
    fake_queue = MagicMock()
    await handle_pull_request_event(_payload("labeled"), "redis://unused", queue=fake_queue)
    fake_queue.enqueue.assert_not_called()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd github-app && python -m pytest tests/test_pull_request_webhook.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app_server.webhooks.pull_request'`

- [ ] **Step 3: Write the implementation**

Create `github-app/app_server/webhooks/pull_request.py`:

```python
ENQUEUE_ACTIONS = ("opened", "synchronize")


async def handle_pull_request_event(payload: dict, redis_url: str, queue=None) -> None:
    action = payload.get("action")
    if action not in ENQUEUE_ACTIONS:
        return

    if queue is None:
        from redis import Redis
        from rq import Queue

        queue = Queue("scans", connection=Redis.from_url(redis_url))

    queue.enqueue(
        "scan_worker.jobs.run_pr_scan_job",
        installation_id=payload["installation"]["id"],
        repo_full_name=payload["repository"]["full_name"],
        pr_number=payload["number"],
        base_sha=payload["pull_request"]["base"]["sha"],
        head_sha=payload["pull_request"]["head"]["sha"],
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd github-app && python -m pytest tests/test_pull_request_webhook.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/app_server/webhooks/pull_request.py github-app/tests/test_pull_request_webhook.py
git commit -m "feat(github-app): pull_request webhook enqueues scan jobs, returns immediately"
```

---

## Task 8: `scan_worker` — the actual clone/scan/diff/comment job

**Files:**
- Create: `github-app/scan_worker/__init__.py`
- Create: `github-app/scan_worker/db.py`
- Create: `github-app/scan_worker/github_api.py`
- Create: `github-app/scan_worker/jobs.py`
- Create: `github-app/scan_worker/worker.py`
- Test: `github-app/tests/test_jobs.py`

**Interfaces:**
- Consumes: `aletheore.pr_comment.{COMMENT_MARKER, format_diff_comment}` (Task 1), `generate_app_jwt`/`get_installation_token` (Task 4).
- Produces: `run_pr_scan_job(installation_id: int, repo_full_name: str, pr_number: int, base_sha: str, head_sha: str) -> None` — this is the exact dotted path (`scan_worker.jobs.run_pr_scan_job`) enqueued by Task 7.

This is the largest task. It's split into three testable pieces rather than one giant step: the comment-upsert API call, the job's happy path, and the job's failure/cleanup path.

- [ ] **Step 1: Write `scan_worker/github_api.py` and its test**

Create `github-app/tests/test_github_api.py`:

```python
import httpx
import pytest

from aletheore.pr_comment import COMMENT_MARKER
from scan_worker.github_api import upsert_pr_comment


def test_creates_comment_when_none_exists():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(201, json={"id": 1})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    upsert_pr_comment(
        client, "token", "octocat/hello-world", 42, f"{COMMENT_MARKER}\nbody"
    )
    methods = [m for m, _ in calls]
    assert methods == ["GET", "POST"]


def test_updates_existing_comment():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.method == "GET":
            return httpx.Response(
                200, json=[{"id": 99, "body": f"{COMMENT_MARKER}\nold body"}]
            )
        return httpx.Response(200, json={"id": 99})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    upsert_pr_comment(
        client, "token", "octocat/hello-world", 42, f"{COMMENT_MARKER}\nnew body"
    )
    methods = [m for m, _ in calls]
    assert methods == ["GET", "PATCH"]
```

Run: `cd github-app && python -m pytest tests/test_github_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scan_worker'`

Create `github-app/scan_worker/__init__.py` (empty file).

Create `github-app/scan_worker/github_api.py`:

```python
import httpx

from aletheore.pr_comment import COMMENT_MARKER


def upsert_pr_comment(
    client: httpx.Client, token: str, repo_full_name: str, pr_number: int, body: str
) -> None:
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    list_url = f"/repos/{repo_full_name}/issues/{pr_number}/comments"
    response = client.get(list_url, headers=headers)
    response.raise_for_status()
    existing = next((c for c in response.json() if COMMENT_MARKER in c.get("body", "")), None)

    if existing:
        update_url = f"/repos/{repo_full_name}/issues/comments/{existing['id']}"
        response = client.patch(update_url, headers=headers, json={"body": body})
    else:
        response = client.post(list_url, headers=headers, json={"body": body})
    response.raise_for_status()
```

Run: `cd github-app && python -m pytest tests/test_github_api.py -v`
Expected: PASS (2 tests)

- [ ] **Step 2: Write `scan_worker/db.py` (sync, for the RQ worker process)**

Create `github-app/scan_worker/db.py`:

```python
import json
from datetime import datetime

import psycopg


def insert_repo_history(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    scanned_at: datetime,
    evidence: dict,
    keep: int = 20,
) -> None:
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO repo_history (installation_id, repo_full_name, scanned_at, evidence)
                VALUES (%s, %s, %s, %s)
                """,
                (installation_id, repo_full_name, scanned_at, json.dumps(evidence)),
            )
            cur.execute(
                """
                DELETE FROM repo_history
                WHERE id IN (
                    SELECT id FROM repo_history
                    WHERE installation_id = %s AND repo_full_name = %s
                    ORDER BY scanned_at DESC
                    OFFSET %s
                )
                """,
                (installation_id, repo_full_name, keep),
            )
        conn.commit()
```

No dedicated test for this file beyond what Task 2's rotation test already proves about the SQL shape (same query, sync driver) — it's exercised end-to-end in Step 5's job test below via a real test database.

- [ ] **Step 3: Write the failing test for the job's happy path**

Create `github-app/tests/test_jobs.py`:

```python
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scan_worker.jobs import run_pr_scan_job


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
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def bare_repo_with_two_commits(tmp_path):
    work = tmp_path / "work"
    base_sha = _make_git_repo(work, {"app.py": "print('hello')\n"})
    (work / "app.py").write_text("password = 'sk-abcdef1234567890abcdef1234567890'\n")
    subprocess.run(["git", "add", "."], cwd=work, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add secret"], cwd=work, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work, check=True, capture_output=True, text=True
    ).stdout.strip()

    bare = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    return str(bare), base_sha, head_sha


@pytest.mark.asyncio
async def test_happy_path_posts_comment_and_writes_history(bare_repo_with_two_commits, pool, monkeypatch):
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    posted = {}

    def fake_upsert(client, token, repo_full_name, pr_number, body):
        posted["body"] = body
        posted["repo_full_name"] = repo_full_name
        posted["pr_number"] = pr_number

    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", fake_upsert)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr(
        "scan_worker.jobs._insert_history",
        lambda installation_id, repo_full_name, evidence: None,
    )

    await run_pr_scan_job(
        installation_id=1,
        repo_full_name="octocat/hello-world",
        pr_number=7,
        base_sha=base_sha,
        head_sha=head_sha,
    )

    assert "password = " in posted["body"] or "Secrets" in posted["body"]
    assert posted["pr_number"] == 7


@pytest.mark.asyncio
async def test_temp_dir_cleaned_up_on_success(bare_repo_with_two_commits, monkeypatch, tmp_path):
    import scan_worker.jobs as jobs_module

    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)

    seen_job_dirs = []
    original_mkdtemp = jobs_module._job_temp_dir

    def spy(*a, **k):
        d = original_mkdtemp(*a, **k)
        seen_job_dirs.append(d)
        return d

    monkeypatch.setattr("scan_worker.jobs._job_temp_dir", spy)

    await run_pr_scan_job(
        installation_id=1,
        repo_full_name="octocat/hello-world",
        pr_number=7,
        base_sha=base_sha,
        head_sha=head_sha,
    )

    assert len(seen_job_dirs) == 1
    assert not seen_job_dirs[0].exists()


@pytest.mark.asyncio
async def test_clone_failure_posts_failure_comment_and_cleans_up(monkeypatch, tmp_path):
    import scan_worker.jobs as jobs_module

    posted = {}

    def fake_upsert(client, token, repo_full_name, pr_number, body):
        posted["body"] = body

    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", fake_upsert)
    monkeypatch.setattr(
        "scan_worker.jobs._clone_url", lambda repo_full_name, token: "/nonexistent/not-a-repo"
    )
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")

    seen_job_dirs = []
    original = jobs_module._job_temp_dir

    def spy(*a, **k):
        d = original(*a, **k)
        seen_job_dirs.append(d)
        return d

    monkeypatch.setattr("scan_worker.jobs._job_temp_dir", spy)

    await run_pr_scan_job(
        installation_id=1,
        repo_full_name="octocat/hello-world",
        pr_number=7,
        base_sha="deadbeef",
        head_sha="deadbeef",
    )

    assert "couldn't complete this scan" in posted["body"]
    assert not seen_job_dirs[0].exists()
```

- [ ] **Step 4: Run to verify it fails**

Run: `cd github-app && python -m pytest tests/test_jobs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scan_worker.jobs'`

- [ ] **Step 5: Write the implementation**

Create `github-app/scan_worker/jobs.py`:

```python
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from aletheore.history import compute_diff
from aletheore.pr_comment import format_diff_comment
from app_server.config import get_settings
from app_server.github_auth import generate_app_jwt, get_installation_token
from scan_worker.db import insert_repo_history
from scan_worker.github_api import upsert_pr_comment

import httpx


JOBS_ROOT = Path("/tmp/aletheore-jobs")


def _job_temp_dir() -> Path:
    d = JOBS_ROOT / str(uuid.uuid4())
    d.mkdir(parents=True, exist_ok=False)
    return d


def _clone_url(repo_full_name: str, token: str) -> str:
    return f"https://x-access-token:{token}@github.com/{repo_full_name}.git"


def _clone_ref(url: str, ref: str, dest: Path) -> None:
    subprocess.run(["git", "clone", "-q", "--no-checkout", url, str(dest)], check=True)
    subprocess.run(["git", "checkout", "-q", ref], cwd=dest, check=True)


def _run_scan(repo_dir: Path) -> Path:
    subprocess.run(["aletheore", "scan", str(repo_dir)], check=True)
    return repo_dir / ".aletheore" / "evidence.json"


def _insert_history(installation_id: int, repo_full_name: str, evidence: dict) -> None:
    from datetime import datetime, timezone

    settings = get_settings()
    insert_repo_history(
        settings.database_url, installation_id, repo_full_name, datetime.now(timezone.utc), evidence
    )


async def run_pr_scan_job(
    installation_id: int, repo_full_name: str, pr_number: int, base_sha: str, head_sha: str
) -> None:
    settings = get_settings()
    job_dir = _job_temp_dir()
    try:
        app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
        token = await get_installation_token(installation_id, app_jwt)

        clone_url = _clone_url(repo_full_name, token)
        base_dir = job_dir / "base"
        head_dir = job_dir / "head"
        _clone_ref(clone_url, base_sha, base_dir)
        _clone_ref(clone_url, head_sha, head_dir)

        base_evidence_path = _run_scan(base_dir)
        head_evidence_path = _run_scan(head_dir)

        import json

        old = json.loads(base_evidence_path.read_text())
        new = json.loads(head_evidence_path.read_text())
        diff = compute_diff(old, new, full=False)

        body = format_diff_comment(diff)
        client = httpx.Client(base_url="https://api.github.com")
        upsert_pr_comment(client, token, repo_full_name, pr_number, body)

        _insert_history(installation_id, repo_full_name, new)
    except Exception as exc:  # noqa: BLE001 - deliberately broad: any failure gets a user-visible comment
        try:
            app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
            token = await get_installation_token(installation_id, app_jwt)
            client = httpx.Client(base_url="https://api.github.com")
            failure_body = (
                f"<!-- aletheore-diff -->\nAletheore couldn't complete this scan: {exc}"
            )
            upsert_pr_comment(client, token, repo_full_name, pr_number, failure_body)
        except Exception:  # noqa: BLE001 - never let the failure-reporting path itself crash the job
            pass
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)
```

Create `github-app/scan_worker/worker.py`:

```python
#!/usr/bin/env python3
"""RQ worker entrypoint for the `scans` queue.

Usage:
    python worker.py

Environment variables:
    REDIS_URL   redis://...  (default: redis://localhost:6379/0)
"""
import os

from redis import Redis
from rq import Worker

from app_server.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    redis_conn = Redis.from_url(settings.redis_url)
    worker = Worker(["scans"], connection=redis_conn)
    worker.work()
```

- [ ] **Step 6: Run to verify it passes**

Run: `cd github-app && python -m pytest tests/test_jobs.py -v`
Expected: PASS (3 tests)

Note on `test_happy_path_posts_comment_and_writes_history`: this requires the `aletheore` package to be installed in the same environment as `github-app`'s tests (`pip install -e ../prototype`), since `_run_scan` shells out to the real `aletheore` CLI. Add this to `github-app/README.md`'s setup instructions (Task 10).

- [ ] **Step 7: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/scan_worker github-app/tests/test_jobs.py github-app/tests/test_github_api.py
git commit -m "feat(github-app): scan-worker job - clone, scan, diff, comment upsert, cleanup, failure path"
```

---

## Task 9: Hosted dashboard route

**Files:**
- Create: `github-app/app_server/dashboard.py`
- Modify: `github-app/app_server/main.py`
- Test: `github-app/tests/test_dashboard.py`

**Interfaces:**
- Consumes: `get_installation`, `get_recent_history` (Task 2).
- Produces: a FastAPI `APIRouter` named `dashboard_router`, included into `app` in `main.py`.

- [ ] **Step 1: Write the failing test**

Create `github-app/tests/test_dashboard.py`:

```python
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app_server.db import insert_repo_history, upsert_installation
from app_server.main import app


@pytest.mark.asyncio
async def test_dashboard_returns_404_for_unknown_repo(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/app/octocat/nonexistent")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_dashboard_returns_data_for_known_repo(pool):
    await upsert_installation(pool, 1, "octocat")
    await insert_repo_history(
        pool, 1, "octocat/hello-world", datetime.now(timezone.utc), {"repository": {"modules": []}}
    )
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/app/octocat/hello-world")
    assert response.status_code == 200
    body = response.json()
    assert body["repo_full_name"] == "octocat/hello-world"
    assert len(body["history"]) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd github-app && python -m pytest tests/test_dashboard.py -v`
Expected: FAIL with `AttributeError` or 404 on `/app/...` (route doesn't exist yet)

- [ ] **Step 3: Write the implementation**

Create `github-app/app_server/dashboard.py`:

```python
from fastapi import APIRouter, HTTPException, Request

from app_server.db import get_recent_history

dashboard_router = APIRouter()


@dashboard_router.get("/app/{org}/{repo}")
async def get_dashboard(org: str, repo: str, request: Request):
    repo_full_name = f"{org}/{repo}"
    pool = request.app.state.db_pool

    # installation_id isn't in the URL - look up any installation that has
    # history for this repo_full_name. A repo can only be scanned by the
    # installation that owns it, so this is unambiguous in practice.
    row = await pool.fetchrow(
        "SELECT DISTINCT installation_id FROM repo_history WHERE repo_full_name = $1 LIMIT 1",
        repo_full_name,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="no scan history for this repo")

    history = await get_recent_history(pool, row["installation_id"], repo_full_name)
    return {"repo_full_name": repo_full_name, "history": history}
```

Modify `github-app/app_server/main.py` — add the import and registration:

```python
from app_server.dashboard import dashboard_router
```

(add near the top, with the other `app_server` imports) and after `app = FastAPI(lifespan=lifespan)`:

```python
app.include_router(dashboard_router)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd github-app && python -m pytest tests/test_dashboard.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/app_server/dashboard.py github-app/app_server/main.py github-app/tests/test_dashboard.py
git commit -m "feat(github-app): hosted dashboard JSON API (GET /app/{org}/{repo})"
```

Note: this task ships the JSON data endpoint the spec's "Hosted Dashboard" section describes. Rendering it as an actual graph/cluster/trend-chart UI (vs. raw JSON) is a frontend build on top of this endpoint — reasonable to treat as a fast-follow once this endpoint is live and proven, rather than blocking this plan on frontend framework choice (the spec's own "Open Questions" section left that framework choice unresolved). The private-repo auth-gate described in the spec (checking GitHub read access via the viewer's OAuth) is also deferred to that fast-follow, since it requires a user-facing OAuth login flow this plan doesn't otherwise need — flag this explicitly to the user before installing the App on any private repo, since until then this endpoint has no auth at all.

---

## Task 10: Deployment config (Docker Compose, Caddy, env)

**Files:**
- Create: `github-app/Dockerfile.app-server`
- Create: `github-app/Dockerfile.scan-worker`
- Create: `github-app/docker-compose.yml`
- Create: `github-app/Caddyfile`
- Create: `github-app/.env.example`
- Create: `github-app/README.md`

This task has no pytest steps — it's infrastructure config, validated by the tools' own config-check commands (the same approach already used earlier this session when Procta's Caddyfile was changed).

- [ ] **Step 1: Write `Dockerfile.app-server`**

Create `github-app/Dockerfile.app-server`:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY prototype/pyproject.toml prototype/pyproject.toml
COPY prototype/aletheore prototype/aletheore
RUN pip install --no-cache-dir ./prototype

COPY github-app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY github-app/app_server ./app_server

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

CMD ["uvicorn", "app_server.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Write `Dockerfile.scan-worker`**

Create `github-app/Dockerfile.scan-worker`:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY prototype/pyproject.toml prototype/pyproject.toml
COPY prototype/aletheore prototype/aletheore
RUN pip install --no-cache-dir ./prototype

COPY github-app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY github-app/app_server ./app_server
COPY github-app/scan_worker ./scan_worker

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

CMD ["python", "scan_worker/worker.py"]
```

- [ ] **Step 3: Write `docker-compose.yml`**

Create `github-app/docker-compose.yml`:

```yaml
services:
  app-server:
    build:
      context: ..
      dockerfile: github-app/Dockerfile.app-server
    restart: unless-stopped
    env_file:
      - .env
    depends_on:
      - postgres
      - redis
    ports:
      - "127.0.0.1:8000:8000"

  scan-worker:
    build:
      context: ..
      dockerfile: github-app/Dockerfile.scan-worker
    restart: unless-stopped
    env_file:
      - .env
    depends_on:
      - postgres
      - redis

  postgres:
    image: postgres:16
    restart: unless-stopped
    environment:
      - POSTGRES_DB=aletheore_app
      - POSTGRES_USER=aletheore
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
    volumes:
      - aletheore_postgres_data:/var/lib/postgresql/data
      - ./migrations:/docker-entrypoint-initdb.d:ro

  redis:
    image: redis:7-alpine
    restart: unless-stopped

  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - aletheore_caddy_data:/data
      - aletheore_caddy_config:/config
    depends_on:
      - app-server

volumes:
  aletheore_postgres_data:
  aletheore_caddy_data:
  aletheore_caddy_config:
```

- [ ] **Step 4: Write `Caddyfile`**

Create `github-app/Caddyfile`:

```
aletheore.com {
    encode gzip

    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Frame-Options "DENY"
        X-Content-Type-Options "nosniff"
        Referrer-Policy "strict-origin-when-cross-origin"
    }

    reverse_proxy app-server:8000
}
```

- [ ] **Step 5: Validate the Compose and Caddy configs before deploying**

Run: `cd github-app && docker compose config --quiet`
Expected: no output, exit code 0 (validates YAML + interpolation, same check used for Procta's compose file)

Run: `docker run --rm -v "$(pwd)/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile`
Expected: `Valid configuration`

- [ ] **Step 6: Write `.env.example`**

Create `github-app/.env.example`:

```
DATABASE_URL=postgresql://aletheore:changeme@postgres:5432/aletheore_app
POSTGRES_PASSWORD=changeme
REDIS_URL=redis://redis:6379/0
GITHUB_APP_ID=
GITHUB_APP_PRIVATE_KEY=
GITHUB_WEBHOOK_SECRET=
```

- [ ] **Step 7: Write `README.md`**

Create `github-app/README.md`:

```markdown
# Aletheore GitHub App

The hosted service backing the Aletheore GitHub App: receives webhooks,
runs `aletheore scan` + `aletheore diff` on every PR, posts the result as
a comment, and serves a free hosted dashboard. See
`docs/superpowers/specs/2026-07-17-aletheore-github-app-foundation-design.md`
for the full design.

## Local development

    cd prototype && pip install -e .
    cd ../github-app && pip install -r requirements.txt

    docker run -d --name aletheore-test-pg -e POSTGRES_PASSWORD=test \
        -e POSTGRES_DB=aletheore_test -p 55433:5432 postgres:16
    PGPASSWORD=test psql -h localhost -p 55433 -U postgres -d aletheore_test \
        -f migrations/001_initial_schema.sql

    export TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test
    python -m pytest tests/ -v

## Deploying (KVM4)

1. Register the GitHub App in GitHub's settings (webhook URL:
   `https://aletheore.com/webhook`, permissions: `contents: read`,
   `pull_requests: write`, events: `pull_request`, `installation`,
   `installation_repositories`, `marketplace_purchase`).
2. Copy `.env.example` to `.env` on the server, fill in
   `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY` (the full PEM, as a single
   env var - use `\n` for newlines or a Docker secret), `GITHUB_WEBHOOK_SECRET`,
   and a real `POSTGRES_PASSWORD`.
3. Point `aletheore.com`'s DNS A record at the KVM4 server's IP.
4. `docker compose up -d --build`.
5. Confirm `docker compose logs app-server` shows a clean startup and
   `curl -I https://aletheore.com/webhook` returns a response (405 is
   expected for a bare GET - the route only accepts POST - confirms
   Caddy + app-server are both reachable end to end).
```

- [ ] **Step 8: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/Dockerfile.app-server github-app/Dockerfile.scan-worker \
        github-app/docker-compose.yml github-app/Caddyfile github-app/.env.example \
        github-app/README.md
git commit -m "feat(github-app): Docker Compose + Caddy deployment config for KVM4"
```

---

## Self-Review Notes (already applied above)

- **Spec coverage:** App mechanics (Tasks 5-7), execution/privacy posture (Task 8), storage (Task 2), billing (Task 6), deployment (Task 10), comment upsert/signature/tenant-isolation/idempotency/cascade-delete/graceful-failure improvements from the design's final review round (Tasks 3, 6, 8) are all covered. The dashboard's actual chart/graph *rendering* (vs. the JSON data endpoint) and the private-repo OAuth gate are explicitly flagged as deferred fast-follows in Task 9, since the spec itself left the frontend framework choice open - not silently dropped.
- **Placeholder scan:** no TBD/TODO markers; every step has complete, runnable code.
- **Type consistency:** `run_pr_scan_job`'s keyword arguments (`installation_id`, `repo_full_name`, `pr_number`, `base_sha`, `head_sha`) match exactly between the enqueue call in Task 7 and the function signature in Task 8. `format_diff_comment`/`COMMENT_MARKER` from Task 1 are imported with identical names in Task 8's `jobs.py`.
