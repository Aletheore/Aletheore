# Automatic DeepSeek V4 Flash PR Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An automatic, Pro-tier-only code review that fires on every PR push, reads the real diff via GitHub's compare API (no cloning), and posts citation-constrained findings (exact file+line, concrete checkable issue only — no style opinions) as their own PR comment, using DeepSeek V4 Flash.

**Architecture:** A new job (`run_flash_review_job`) enqueued alongside the existing `run_pr_scan_job` from the same `pull_request` webhook. Reviews only the diff since the last-reviewed commit for that PR (not the whole PR every push), debounced by 2 minutes per PR to absorb rapid force-push spam, and gated by the shared per-installation spend cap from the companion plan (`docs/superpowers/plans/2026-07-18-shared-llm-spend-cap.md`) — **that plan must be implemented first**, since every task below imports functions it creates (`get_llm_spend_this_month`, `record_llm_spend`, `get_extra_seats` from `scan_worker/db.py`; `monthly_cap_for_installation`, `cost_for_usage` from `app_server/llm_cost.py`; the `on_usage` hook on `OpenAICompatibleAdapter`).

**Tech Stack:** Python 3.12, httpx, psycopg, pytest. No new dependencies.

## Global Constraints

- Pro-tier only — free-tier installations get no Flash review at all, silently (matches the existing `_maybe_send_slack_alert`/`_maybe_create_check_run` pattern in `scan_worker/jobs.py`).
- Findings must be citation-constrained: exact `file` + exact `line` from the diff, plus a concrete, checkable `issue` description — never a style opinion, never "consider refactoring." Enforced by parsing the model's structured JSON output, not just prompting for it — anything that doesn't parse to the expected shape is treated as zero findings, never posted as malformed prose.
- Debounce: 2 minutes per PR, checked and reserved atomically before any GitHub or LLM call.
- Spend: shared with managed audits via the cap from the companion plan — this feature does not have its own separate budget.
- All file paths below are relative to `github-app/` unless prefixed with `prototype/`.

---

### Task 1: Migration — `flash_review_state` table

**Files:**
- Create: `migrations/006_flash_review_state.sql`

**Interfaces:**
- Produces: table `flash_review_state(installation_id, repo_full_name, pr_number, last_reviewed_sha, last_attempted_at)` — consumed by Task 4.

- [ ] **Step 1: Write the migration**

```sql
CREATE TABLE IF NOT EXISTS flash_review_state (
    installation_id    BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name      TEXT NOT NULL,
    pr_number           INT NOT NULL,
    last_reviewed_sha   TEXT,
    last_attempted_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (installation_id, repo_full_name, pr_number)
);
```

- [ ] **Step 2: Apply it to the local test Postgres and update the test glob**

Run: `PGPASSWORD=test psql -h localhost -p 55433 -U postgres -d aletheore_test -f migrations/006_flash_review_state.sql`
Expected: `CREATE TABLE`.

In `tests/conftest.py`, update the migration glob from `00[2345]_*.sql` (set by the companion spend-cap plan) to `00[23456]_*.sql`.

- [ ] **Step 3: Commit**

```bash
git add migrations/006_flash_review_state.sql tests/conftest.py
git commit -m "feat: add flash_review_state table"
```

---

### Task 2: Fetch a real PR diff via GitHub's compare API

**Files:**
- Modify: `scan_worker/github_api.py`
- Test: `tests/test_github_api.py` (create if it doesn't already exist — check first: `ls tests/test_github_api.py`; if absent, create it following the httpx `MockTransport` pattern already used throughout this test suite, e.g. in `tests/test_managed_audit_api.py`)

**Interfaces:**
- Produces: `fetch_pr_diff(client: httpx.Client, token: str, repo_full_name: str, base_ref: str, head_ref: str) -> str` — consumed by Task 5.

- [ ] **Step 1: Write the failing tests**

```python
import httpx
import pytest

from scan_worker.github_api import fetch_pr_diff


def test_fetch_pr_diff_concatenates_real_patches():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/octocat/hello-world/compare/aaa...bbb"
        return httpx.Response(
            200,
            json={
                "files": [
                    {
                        "filename": "app.py",
                        "patch": "@@ -1,2 +1,3 @@\n def hello():\n+    print('hi')\n     pass",
                    },
                    {"filename": "image.png", "patch": None},
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    diff_text = fetch_pr_diff(client, "fake-token", "octocat/hello-world", "aaa", "bbb")

    assert "app.py" in diff_text
    assert "print('hi')" in diff_text
    assert "image.png" not in diff_text


def test_fetch_pr_diff_returns_empty_string_when_no_files_changed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"files": []})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    diff_text = fetch_pr_diff(client, "fake-token", "octocat/hello-world", "aaa", "bbb")

    assert diff_text == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_github_api.py -v`
Expected: FAIL with `ImportError: cannot import name 'fetch_pr_diff'`

- [ ] **Step 3: Implement it**

Add to `scan_worker/github_api.py`:

```python
def fetch_pr_diff(
    client: httpx.Client, token: str, repo_full_name: str, base_ref: str, head_ref: str
) -> str:
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    response = client.get(
        f"/repos/{repo_full_name}/compare/{base_ref}...{head_ref}", headers=headers
    )
    response.raise_for_status()
    data = response.json()
    parts = []
    for file in data.get("files", []):
        patch = file.get("patch")
        if patch:
            parts.append(f"--- {file['filename']} ---\n{patch}")
    return "\n\n".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_github_api.py -v`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add scan_worker/github_api.py tests/test_github_api.py
git commit -m "feat: fetch real PR diff text via GitHub compare API"
```

---

### Task 3: Citation-constrained Flash review call

**Files:**
- Create: `scan_worker/flash_review.py`
- Test: `tests/test_flash_review.py`

**Interfaces:**
- Consumes: `OpenAICompatibleAdapter(..., on_usage=...)` and its `simple_completion()` method (from the companion spend-cap plan).
- Produces: `review_diff(diff_text: str, on_usage: Callable[[int, int], None] | None = None) -> list[dict]`, each dict shaped `{"file": str, "line": int, "issue": str}` — consumed by Task 5.

- [ ] **Step 1: Write the failing tests**

```python
from unittest.mock import MagicMock, patch

from scan_worker.flash_review import review_diff


def test_review_diff_returns_empty_list_for_empty_diff():
    assert review_diff("") == []
    assert review_diff("   \n  ") == []


@patch("scan_worker.flash_review.OpenAICompatibleAdapter")
def test_review_diff_parses_valid_findings(mock_adapter_class):
    mock_adapter = MagicMock()
    mock_adapter.simple_completion.return_value = (
        '[{"file": "app.py", "line": 42, "issue": "unclosed file handle, never calls .close()"}]'
    )
    mock_adapter_class.return_value = mock_adapter

    findings = review_diff("--- app.py ---\n@@ ... @@\n+f = open('x')")

    assert findings == [
        {"file": "app.py", "line": 42, "issue": "unclosed file handle, never calls .close()"}
    ]


@patch("scan_worker.flash_review.OpenAICompatibleAdapter")
def test_review_diff_treats_malformed_json_as_no_findings(mock_adapter_class):
    mock_adapter = MagicMock()
    mock_adapter.simple_completion.return_value = "not valid json at all"
    mock_adapter_class.return_value = mock_adapter

    assert review_diff("some diff text") == []


@patch("scan_worker.flash_review.OpenAICompatibleAdapter")
def test_review_diff_drops_findings_missing_required_fields(mock_adapter_class):
    mock_adapter = MagicMock()
    mock_adapter.simple_completion.return_value = (
        '[{"file": "app.py", "issue": "missing a line number"}, '
        '{"file": "b.py", "line": 3, "issue": "this one is valid"}]'
    )
    mock_adapter_class.return_value = mock_adapter

    findings = review_diff("some diff text")

    assert findings == [{"file": "b.py", "line": 3, "issue": "this one is valid"}]


@patch("scan_worker.flash_review.OpenAICompatibleAdapter")
def test_review_diff_threads_on_usage_to_the_adapter(mock_adapter_class):
    mock_adapter = MagicMock()
    mock_adapter.simple_completion.return_value = "[]"
    mock_adapter_class.return_value = mock_adapter

    on_usage = lambda p, c: None
    review_diff("some diff text", on_usage=on_usage)

    _, kwargs = mock_adapter_class.call_args
    assert kwargs["on_usage"] is on_usage
    assert kwargs["model"] == "deepseek-v4-flash"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_flash_review.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scan_worker.flash_review'`

- [ ] **Step 3: Implement it**

```python
# scan_worker/flash_review.py
import json
from collections.abc import Callable

from aletheore.adapters.openai_compatible import OpenAICompatibleAdapter

FLASH_REVIEW_SYSTEM_PROMPT = """You are reviewing a code diff for potential issues. You must
respond with ONLY a JSON array of findings, no other text, no markdown code fences, no
explanation outside the array. Each finding must be an object with exactly these fields:
"file" (the exact file path shown in the diff), "line" (the exact line number from the diff,
as an integer), and "issue" (a concrete, specific, checkable description of an actual problem
at that exact line - never a style opinion, never "consider refactoring", never a vague
concern that isn't tied to something you can point at). Only report a finding if you can name
a specific, real issue at a specific line. If you find nothing worth flagging, respond with
exactly: []"""


def review_diff(diff_text: str, on_usage: Callable[[int, int], None] | None = None) -> list[dict]:
    if not diff_text.strip():
        return []

    adapter = OpenAICompatibleAdapter(
        name="DeepSeek",
        base_url="https://api.deepseek.com",
        api_key_env_var="DEEPSEEK_API_KEY",
        model="deepseek-v4-flash",
        on_usage=on_usage,
    )
    raw_output = adapter.simple_completion(FLASH_REVIEW_SYSTEM_PROMPT, diff_text, cwd=".")

    try:
        findings = json.loads(raw_output)
    except json.JSONDecodeError:
        return []

    if not isinstance(findings, list):
        return []

    valid: list[dict] = []
    for finding in findings:
        if (
            isinstance(finding, dict)
            and isinstance(finding.get("file"), str)
            and finding.get("file")
            and isinstance(finding.get("line"), int)
            and isinstance(finding.get("issue"), str)
            and finding.get("issue")
        ):
            valid.append(
                {"file": finding["file"], "line": finding["line"], "issue": finding["issue"]}
            )
    return valid
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_flash_review.py -v`
Expected: All 5 pass.

- [ ] **Step 5: Commit**

```bash
git add scan_worker/flash_review.py tests/test_flash_review.py
git commit -m "feat: add citation-constrained Flash diff review"
```

---

### Task 4: Per-PR debounce and last-reviewed-commit tracking

**Files:**
- Modify: `scan_worker/db.py`
- Test: `tests/test_scan_worker_db.py`

**Interfaces:**
- Produces: `check_and_reserve_flash_review_attempt(dsn: str, installation_id: int, repo_full_name: str, pr_number: int, debounce_seconds: int = 120) -> bool`, `get_last_reviewed_sha(dsn: str, installation_id: int, repo_full_name: str, pr_number: int) -> str | None`, `set_last_reviewed_sha(dsn: str, installation_id: int, repo_full_name: str, pr_number: int, sha: str) -> None` — consumed by Task 5.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_scan_worker_db.py`'s import block: `check_and_reserve_flash_review_attempt, get_last_reviewed_sha, set_last_reviewed_sha,`.

```python
@pytest.mark.asyncio
async def test_check_and_reserve_flash_review_attempt_allows_first_and_blocks_second(pool):
    await _insert_installation(pool, 301, "a")
    first = check_and_reserve_flash_review_attempt(TEST_DATABASE_URL, 301, "a/repo1", 42)
    second = check_and_reserve_flash_review_attempt(TEST_DATABASE_URL, 301, "a/repo1", 42)
    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_check_and_reserve_flash_review_attempt_allows_after_debounce_elapses(pool):
    await _insert_installation(pool, 301, "a")
    check_and_reserve_flash_review_attempt(TEST_DATABASE_URL, 301, "a/repo1", 42, debounce_seconds=0)
    allowed = check_and_reserve_flash_review_attempt(
        TEST_DATABASE_URL, 301, "a/repo1", 42, debounce_seconds=0
    )
    assert allowed is True


@pytest.mark.asyncio
async def test_get_last_reviewed_sha_returns_none_before_any_review(pool):
    await _insert_installation(pool, 301, "a")
    check_and_reserve_flash_review_attempt(TEST_DATABASE_URL, 301, "a/repo1", 42)
    assert get_last_reviewed_sha(TEST_DATABASE_URL, 301, "a/repo1", 42) is None


@pytest.mark.asyncio
async def test_set_and_get_last_reviewed_sha_round_trips(pool):
    await _insert_installation(pool, 301, "a")
    check_and_reserve_flash_review_attempt(TEST_DATABASE_URL, 301, "a/repo1", 42)
    set_last_reviewed_sha(TEST_DATABASE_URL, 301, "a/repo1", 42, "deadbeef")
    assert get_last_reviewed_sha(TEST_DATABASE_URL, 301, "a/repo1", 42) == "deadbeef"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_scan_worker_db.py -k "flash_review" -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement them**

Add to `scan_worker/db.py`:

```python
def check_and_reserve_flash_review_attempt(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    pr_number: int,
    debounce_seconds: int = 120,
) -> bool:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO flash_review_state
                    (installation_id, repo_full_name, pr_number, last_attempted_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (installation_id, repo_full_name, pr_number) DO UPDATE
                SET last_attempted_at = EXCLUDED.last_attempted_at
                WHERE flash_review_state.last_attempted_at <= now() - %s * interval '1 second'
                RETURNING last_attempted_at
                """,
                (installation_id, repo_full_name, pr_number, debounce_seconds),
            )
            row = cur.fetchone()
        conn.commit()
    return row is not None


def get_last_reviewed_sha(
    dsn: str, installation_id: int, repo_full_name: str, pr_number: int
) -> str | None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT last_reviewed_sha FROM flash_review_state
                WHERE installation_id = %s AND repo_full_name = %s AND pr_number = %s
                """,
                (installation_id, repo_full_name, pr_number),
            )
            row = cur.fetchone()
            return row[0] if row and row[0] else None


def set_last_reviewed_sha(
    dsn: str, installation_id: int, repo_full_name: str, pr_number: int, sha: str
) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE flash_review_state SET last_reviewed_sha = %s
                WHERE installation_id = %s AND repo_full_name = %s AND pr_number = %s
                """,
                (sha, installation_id, repo_full_name, pr_number),
            )
        conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_scan_worker_db.py -v`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add scan_worker/db.py tests/test_scan_worker_db.py
git commit -m "feat: add per-PR Flash review debounce and last-reviewed-sha tracking"
```

---

### Task 5: `run_flash_review_job`

**Files:**
- Modify: `scan_worker/jobs.py`
- Test: `tests/test_jobs.py`

**Interfaces:**
- Consumes: `fetch_pr_diff` (Task 2), `review_diff` (Task 3), `check_and_reserve_flash_review_attempt`/`get_last_reviewed_sha`/`set_last_reviewed_sha` (Task 4), `get_llm_spend_this_month`/`record_llm_spend`/`get_extra_seats` and `monthly_cap_for_installation`/`cost_for_usage` (companion spend-cap plan, already wired into this file by that plan's Task 7).
- Produces: `run_flash_review_job(installation_id: int, repo_full_name: str, pr_number: int, base_sha: str, head_sha: str) -> None` — consumed by Task 6.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_jobs.py`:

```python
def test_flash_review_job_skips_free_tier(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "free"}
    )
    llm_called = []
    monkeypatch.setattr("scan_worker.jobs.review_diff", lambda *a, **k: llm_called.append(True))
    from scan_worker.jobs import run_flash_review_job

    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert llm_called == []


def test_flash_review_job_skips_when_debounced(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "pro"}
    )
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: False
    )
    llm_called = []
    monkeypatch.setattr("scan_worker.jobs.review_diff", lambda *a, **k: llm_called.append(True))
    from scan_worker.jobs import run_flash_review_job

    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert llm_called == []


def test_flash_review_job_skips_when_spend_cap_reached(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "pro"}
    )
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True
    )
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 999.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    llm_called = []
    monkeypatch.setattr("scan_worker.jobs.review_diff", lambda *a, **k: llm_called.append(True))
    from scan_worker.jobs import run_flash_review_job

    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert llm_called == []


def test_flash_review_job_posts_findings_and_updates_state(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "pro"}
    )
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True
    )
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- app.py ---\n+bug")
    monkeypatch.setattr(
        "scan_worker.jobs.review_diff",
        lambda diff_text, on_usage=None: [{"file": "app.py", "line": 1, "issue": "real problem"}],
    )
    recorded_spend = []
    monkeypatch.setattr(
        "scan_worker.jobs.record_llm_spend", lambda dsn, iid, cost: recorded_spend.append(cost)
    )
    set_sha_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.set_last_reviewed_sha",
        lambda dsn, iid, repo, pr, sha: set_sha_calls.append(sha),
    )
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(
            body=body, marker=kwargs.get("marker")
        ),
    )
    from scan_worker.jobs import FLASH_REVIEW_MARKER, run_flash_review_job

    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert "app.py:1" in posted["body"]
    assert "real problem" in posted["body"]
    assert posted["marker"] == FLASH_REVIEW_MARKER
    assert set_sha_calls == ["bbb"]
    assert recorded_spend == [0.0]  # review_diff mocked with no on_usage call, so 0.0 accumulated


def test_flash_review_job_posts_no_issues_found_when_findings_empty(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "pro"}
    )
    monkeypatch.setattr(
        "scan_worker.jobs.check_and_reserve_flash_review_attempt", lambda *a, **k: True
    )
    monkeypatch.setattr("scan_worker.jobs.get_llm_spend_this_month", lambda *a, **k: 0.0)
    monkeypatch.setattr("scan_worker.jobs.get_extra_seats", lambda *a, **k: 0)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.get_last_reviewed_sha", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_diff", lambda *a, **k: "--- app.py ---\n+fine")
    monkeypatch.setattr("scan_worker.jobs.review_diff", lambda diff_text, on_usage=None: [])
    monkeypatch.setattr("scan_worker.jobs.record_llm_spend", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.set_last_reviewed_sha", lambda *a, **k: None)
    posted = {}
    monkeypatch.setattr(
        "scan_worker.jobs.upsert_pr_comment",
        lambda client, token, repo_full_name, pr_number, body, **kwargs: posted.update(body=body),
    )
    from scan_worker.jobs import run_flash_review_job

    run_flash_review_job(1, "octocat/hello-world", 42, "aaa", "bbb")

    assert "no issues found" in posted["body"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_jobs.py -k flash_review -v`
Expected: FAIL — `run_flash_review_job` doesn't exist yet.

- [ ] **Step 3: Implement it**

Update the imports at the top of `scan_worker/jobs.py`:

```python
from scan_worker.flash_review import review_diff
from scan_worker.github_api import create_check_run, fetch_pr_diff, upsert_pr_comment
from scan_worker.db import (
    check_and_reserve_flash_review_attempt,
    check_and_reserve_managed_audit,
    get_extra_seats,
    get_installation as get_installation_row,
    get_last_endpoint_health,
    get_last_reviewed_sha,
    get_latest_evidence,
    get_llm_spend_this_month,
    insert_endpoint_health,
    insert_repo_history,
    list_monitored_installations,
    list_repos_for_installation,
    record_llm_spend,
    set_last_reviewed_sha,
)
```

(This replaces the existing `from scan_worker.github_api import create_check_run, upsert_pr_comment` line with the version above that also imports `fetch_pr_diff`.)

Add near `AUDIT_COMMENT_MARKER`:

```python
FLASH_REVIEW_MARKER = "<!-- aletheore-flash-review -->"
```

Add the new job function:

```python
def run_flash_review_job(
    installation_id: int,
    repo_full_name: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
) -> None:
    settings = get_settings()
    installation = get_installation_row(settings.database_url, installation_id)
    if installation is None or installation["plan"] == "free":
        return

    if not check_and_reserve_flash_review_attempt(
        settings.database_url, installation_id, repo_full_name, pr_number
    ):
        return

    extra_seats = get_extra_seats(settings.database_url, installation_id)
    monthly_cap = monthly_cap_for_installation(7.00, extra_seats)
    current_spend = get_llm_spend_this_month(settings.database_url, installation_id)
    if current_spend >= monthly_cap:
        return

    app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
    token = _token_sync(installation_id, app_jwt)
    client = httpx.Client(base_url="https://api.github.com")

    last_reviewed_sha = get_last_reviewed_sha(
        settings.database_url, installation_id, repo_full_name, pr_number
    )
    diff_base = last_reviewed_sha or base_sha
    diff_text = fetch_pr_diff(client, token, repo_full_name, diff_base, head_sha)

    spend_accumulator = {"total": 0.0}

    def _on_usage(prompt_tokens: int, completion_tokens: int) -> None:
        spend_accumulator["total"] += cost_for_usage(
            "deepseek-v4-flash", prompt_tokens, completion_tokens
        )

    findings = review_diff(diff_text, on_usage=_on_usage)
    record_llm_spend(settings.database_url, installation_id, spend_accumulator["total"])

    if findings:
        lines = [f"{FLASH_REVIEW_MARKER}\n### Aletheore Flash review\n"]
        for finding in findings:
            lines.append(f"- `{finding['file']}:{finding['line']}` — {finding['issue']}")
        body = "\n".join(lines)
    else:
        body = f"{FLASH_REVIEW_MARKER}\n### Aletheore Flash review\n\nNo issues found in this diff."

    upsert_pr_comment(client, token, repo_full_name, pr_number, body, marker=FLASH_REVIEW_MARKER)
    set_last_reviewed_sha(settings.database_url, installation_id, repo_full_name, pr_number, head_sha)
```

Also add `from app_server.llm_cost import cost_for_usage, monthly_cap_for_installation` to the imports if the companion spend-cap plan's Task 7 didn't already add it (it did — this line already exists in the file after that plan is implemented; do not duplicate it).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_jobs.py -v`
Expected: All pass, including every pre-existing test in this file.

- [ ] **Step 5: Commit**

```bash
git add scan_worker/jobs.py tests/test_jobs.py
git commit -m "feat: add run_flash_review_job"
```

---

### Task 6: Wire the webhook to enqueue the new job

**Files:**
- Modify: `app_server/webhooks/pull_request.py`
- Test: `tests/test_pull_request_webhook.py`

**Interfaces:**
- Consumes: `run_flash_review_job` (Task 5), enqueued by name (RQ job string, matching the existing `run_pr_scan_job` pattern).

- [ ] **Step 1: Update the existing tests — they will break otherwise**

The existing tests in `tests/test_pull_request_webhook.py` assert `fake_queue.enqueue.assert_called_once()`, which will start failing once a second job is enqueued from the same handler. Replace the whole file with:

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
async def test_opened_enqueues_both_jobs():
    fake_queue = MagicMock()
    await handle_pull_request_event(_payload("opened"), "redis://unused", queue=fake_queue)

    assert fake_queue.enqueue.call_count == 2
    job_names = {call.args[0] for call in fake_queue.enqueue.call_args_list}
    assert job_names == {"scan_worker.jobs.run_pr_scan_job", "scan_worker.jobs.run_flash_review_job"}
    for call in fake_queue.enqueue.call_args_list:
        _, kwargs = call
        assert kwargs["installation_id"] == 111
        assert kwargs["repo_full_name"] == "octocat/hello-world"
        assert kwargs["pr_number"] == 42
        assert kwargs["base_sha"] == "aaa111"
        assert kwargs["head_sha"] == "bbb222"


@pytest.mark.asyncio
async def test_synchronize_enqueues_both_jobs():
    fake_queue = MagicMock()
    await handle_pull_request_event(_payload("synchronize"), "redis://unused", queue=fake_queue)
    assert fake_queue.enqueue.call_count == 2


@pytest.mark.asyncio
async def test_reopened_enqueues_both_jobs():
    fake_queue = MagicMock()
    await handle_pull_request_event(_payload("reopened"), "redis://unused", queue=fake_queue)
    assert fake_queue.enqueue.call_count == 2


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

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_pull_request_webhook.py -v`
Expected: FAIL — `call_count == 2` assertions fail with `call_count == 1` (only the existing job is enqueued so far).

- [ ] **Step 3: Wire in the second enqueue call**

In `app_server/webhooks/pull_request.py`:

```python
ENQUEUE_ACTIONS = ("opened", "reopened", "synchronize")


async def handle_pull_request_event(payload: dict, redis_url: str, queue=None) -> None:
    if payload.get("action") not in ENQUEUE_ACTIONS:
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
    queue.enqueue(
        "scan_worker.jobs.run_flash_review_job",
        installation_id=payload["installation"]["id"],
        repo_full_name=payload["repository"]["full_name"],
        pr_number=payload["number"],
        base_sha=payload["pull_request"]["base"]["sha"],
        head_sha=payload["pull_request"]["head"]["sha"],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_pull_request_webhook.py -v`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add app_server/webhooks/pull_request.py tests/test_pull_request_webhook.py
git commit -m "feat: enqueue run_flash_review_job alongside run_pr_scan_job"
```

---

### Task 7: Real end-to-end verification

**Files:** none modified — verification only.

- [ ] **Step 1: Run the full test suite**

Run: `cd github-app && TEST_DATABASE_URL="postgresql://postgres:test@localhost:55433/aletheore_test" python3 -m pytest tests/ -q`
Expected: all pass.

- [ ] **Step 2: Build a real tiny PR-like diff and run a real Flash review against it**

```bash
cd /tmp
rm -rf flash-review-test && mkdir flash-review-test && cd flash-review-test
git init -q
git config user.email "test@example.com"
git config user.name "Test"
cat > app.py <<'EOF'
def divide(a, b):
    return a / b
EOF
git add . && git commit -q -m "base"
BASE_SHA=$(git rev-parse HEAD)
cat > app.py <<'EOF'
def divide(a, b):
    return a / b  # no check for b == 0
EOF
git add . && git commit -q -m "head"
HEAD_SHA=$(git rev-parse HEAD)
git diff "$BASE_SHA" "$HEAD_SHA" > /tmp/flash-review-test/real.diff
cat /tmp/flash-review-test/real.diff
```

- [ ] **Step 3: Call `review_diff` directly against this real diff, with the real DeepSeek key**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion/github-app
export DEEPSEEK_API_KEY=$(ssh -o BatchMode=yes -o ConnectTimeout=8 root@187.127.169.89 "cd /root/aletheore/github-app && docker compose exec -T scan-worker printenv DEEPSEEK_API_KEY")
python3 -c "
from scan_worker.flash_review import review_diff
diff_text = open('/tmp/flash-review-test/real.diff').read()
usage_totals = {'prompt': 0, 'completion': 0}
def on_usage(p, c):
    usage_totals['prompt'] += p
    usage_totals['completion'] += c
findings = review_diff(diff_text, on_usage=on_usage)
print('Findings:', findings)
print('Usage:', usage_totals)
"
```

Expected: `findings` is a non-empty list containing at least one entry citing `app.py` and a line number, with an `issue` describing the real, actual problem (dividing by `b` with no zero-check) — a genuine, correct, citation-constrained finding on a real, deliberately-flawed diff. `usage_totals` shows nonzero real token counts.

- [ ] **Step 4: Confirm the citation constraint held — no vague/style findings**

Manually read the `findings` output from Step 3. Confirm every entry names a real file and a real line number present in `real.diff`, and that the `issue` text describes a concrete, checkable problem (not "this could be cleaner" or similar vague language). If a vague finding appears, the prompt in `scan_worker/flash_review.py`'s `FLASH_REVIEW_SYSTEM_PROMPT` needs strengthening before this ships — return to Task 3 and tighten the wording, then re-run this verification task from Step 1.

- [ ] **Step 5: No commit needed — this task is verification-only**

If Steps 1-4 all pass, the feature is confirmed working end-to-end with a real DeepSeek Flash call producing a real, correct, citation-constrained finding.
