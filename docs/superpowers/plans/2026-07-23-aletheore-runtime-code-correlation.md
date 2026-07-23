# Runtime-to-Code Correlation (Phase 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the health-check sweep from alerting on single-blip transient failures, and give every confirmed endpoint failure the runtime context it currently lacks - which commit last touched the handler, and whether the response shape itself changed even while still reachable.

**Architecture:** Three additive pieces landing on top of the existing `run_health_check_sweep_job` (`github-app/scan_worker/jobs.py`). (1) Response-shape capture: `run_healthcheck()` (`prototype/aletheore/healthcheck.py`) records the top-level JSON key set of a successful response - never values - alongside the existing status/latency fields, stored in a new `response_shape` column. (2) Retry-hydration: when a check flips from reachable to unreachable, the sweep retries that one endpoint twice more (2s apart) *before* treating it as a real failure - a sweep runs every 180s with a 5s per-request timeout, so two retries add at most ~14s in the rare case of a genuine outage, and silently absorb single blips the rest of the time. (3) Commit correlation: only once a failure survives retries, a live GitHub API call (no repo clone needed) fetches the most recent commit that touched the handler's source file and attaches it to the alert - reusing the exact `evidence_resolution` `commit` field and Slack rendering that already exists for PR-side alerts, so no changes are needed to how alerts are formatted for that part.

**Tech Stack:** Python 3.12, `urllib.request` (existing health-check transport, no new dependency), `httpx` (existing GitHub API client), psycopg, pytest.

## Global Constraints

- Never store response body values - only the top-level JSON key set (a list of strings) and only when the response is reachable and `Content-Type` indicates JSON. This matches the existing "only derived evidence, never raw data" privacy posture already stated in `website/privacy.html`.
- Retry-hydration only applies to a reachability flip toward *down*. A flip toward *recovered* is not retried - recovery is good news, not a candidate for a false positive.
- Commit correlation only fires once a failure is confirmed (post-retries) - never per-check - to keep GitHub API usage rare and proportional to real incidents, not sweep frequency.
- Commit correlation must fail open: if the GitHub API call errors for any reason (rate limit, network, missing credentials), the alert still sends without the commit context rather than being dropped or crashing the sweep.
- `RETRY_ATTEMPTS = 2`, `RETRY_DELAY_SECONDS = 2.0` - fixed values, not configurable per installation in this phase.

---

### Task 1: Response-shape capture

**Files:**
- Modify: `prototype/aletheore/healthcheck.py`
- Modify: `prototype/tests/test_healthcheck.py`
- Create: `github-app/migrations/014_endpoint_health_response_shape.sql`
- Modify: `github-app/scan_worker/db.py:283-361` (`get_last_endpoint_health`, `insert_endpoint_health`)
- Test: `github-app/tests/test_scan_worker_db.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `run_healthcheck()`'s result entries gain a `"response_shape": list[str] | None` key. `insert_endpoint_health(dsn, installation_id, repo_full_name, method, path, reachable, status_code, latency_ms, response_shape=None, target_id=None, keep=20)` and `get_last_endpoint_health(...)` (unchanged signature) whose returned dict now includes `"response_shape"` - both consumed by Task 4's `jobs.py` wiring.

- [ ] **Step 1: Write the failing prototype tests**

Replace the `_mock_response` helper at the top of `prototype/tests/test_healthcheck.py` (it currently only sets `.status`, which leaves `.headers` and `.read` as auto-mocks that would misbehave once shape-capture code tries to use them) with:

```python
def _mock_response(status: int, headers: dict | None = None, body: bytes = b""):
    mock = MagicMock()
    mock.status = status
    mock.headers = headers or {}
    mock.read.return_value = body
    mock.__enter__.return_value = mock
    mock.__exit__.return_value = False
    return mock
```

This is a drop-in replacement - every existing call (`_mock_response(200)`, `_mock_response(404)`) still works via the new defaults (`headers={}`, `body=b""`), and `.headers.get("Content-Type", "")` now correctly returns `""` instead of an auto-mock, so none of the 4 existing tests that use this helper change behavior.

Add these new tests to `prototype/tests/test_healthcheck.py`:

```python
def test_run_healthcheck_captures_response_shape_for_json_object():
    endpoints = [
        {
            "method": "GET",
            "path": "/users/1",
            "framework": "flask",
            "file": "app.py",
            "line": 1,
            "handler": "get_user",
            "unresolved": False,
        }
    ]

    response = _mock_response(
        200,
        headers={"Content-Type": "application/json"},
        body=b'{"id": 1, "name": "Ada", "email": "ada@example.com"}',
    )

    with patch("aletheore.healthcheck.urllib.request.urlopen", return_value=response):
        result = run_healthcheck(endpoints, "http://localhost:5000")

    assert result["results"][0]["response_shape"] == ["email", "id", "name"]


def test_run_healthcheck_captures_response_shape_for_json_list_of_objects():
    endpoints = [
        {"method": "GET", "path": "/users", "framework": "flask", "file": "app.py", "line": 1, "handler": "x", "unresolved": False}
    ]

    response = _mock_response(
        200,
        headers={"Content-Type": "application/json"},
        body=b'[{"id": 1, "name": "Ada"}, {"id": 2, "name": "Bea"}]',
    )

    with patch("aletheore.healthcheck.urllib.request.urlopen", return_value=response):
        result = run_healthcheck(endpoints, "http://localhost:5000")

    assert result["results"][0]["response_shape"] == ["id", "name"]


def test_run_healthcheck_response_shape_is_none_for_non_json_content_type():
    endpoints = [
        {"method": "GET", "path": "/health", "framework": "flask", "file": "app.py", "line": 1, "handler": "x", "unresolved": False}
    ]

    response = _mock_response(200, headers={"Content-Type": "text/plain"}, body=b"OK")

    with patch("aletheore.healthcheck.urllib.request.urlopen", return_value=response):
        result = run_healthcheck(endpoints, "http://localhost:5000")

    assert result["results"][0]["response_shape"] is None


def test_run_healthcheck_response_shape_is_none_for_malformed_json():
    endpoints = [
        {"method": "GET", "path": "/broken", "framework": "flask", "file": "app.py", "line": 1, "handler": "x", "unresolved": False}
    ]

    response = _mock_response(200, headers={"Content-Type": "application/json"}, body=b"not actually json")

    with patch("aletheore.healthcheck.urllib.request.urlopen", return_value=response):
        result = run_healthcheck(endpoints, "http://localhost:5000")

    assert result["results"][0]["response_shape"] is None


def test_run_healthcheck_response_shape_is_none_on_unreachable():
    endpoints = [
        {"method": "GET", "path": "/x", "framework": "flask", "file": "app.py", "line": 1, "handler": "x", "unresolved": False}
    ]

    with patch(
        "aletheore.healthcheck.urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        result = run_healthcheck(endpoints, "http://localhost:9999")

    assert result["results"][0]["response_shape"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd prototype && python -m pytest tests/test_healthcheck.py -v -k response_shape`
Expected: FAIL with `KeyError: 'response_shape'`.

- [ ] **Step 3: Implement shape capture in `healthcheck.py`**

Add `import json` to the top of `prototype/aletheore/healthcheck.py` (alongside the existing `re`, `ssl`, `time`, etc. imports).

Add this constant and function right before `def run_healthcheck(`:

```python
MAX_BODY_BYTES_FOR_SHAPE = 65_536


def _response_shape(response) -> list[str] | None:
    content_type = response.headers.get("Content-Type", "")
    if "application/json" not in content_type:
        return None
    try:
        raw = response.read(MAX_BODY_BYTES_FOR_SHAPE)
        data = json.loads(raw)
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
    if isinstance(data, dict):
        return sorted(data.keys())
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return sorted(data[0].keys())
    return None
```

In `run_healthcheck`, change the try/except block that performs the request:

```python
        start = time.monotonic()
        try:
            request = urllib.request.Request(url)
            with urllib.request.urlopen(
                request, timeout=timeout, context=_SSL_CONTEXT
            ) as response:
                entry["status_code"] = response.status
                entry["reachable"] = True
                entry["response_shape"] = _response_shape(response)
        except urllib.error.HTTPError as exc:
            entry["status_code"] = exc.code
            entry["reachable"] = True
            entry["response_shape"] = None
        except (urllib.error.URLError, TimeoutError, OSError):
            entry["status_code"] = None
            entry["reachable"] = False
            entry["response_shape"] = None
        entry["latency_ms"] = round((time.monotonic() - start) * 1000, 1)
        results.append(entry)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd prototype && python -m pytest tests/test_healthcheck.py -v`
Expected: all PASS.

- [ ] **Step 5: Create the migration**

Create `github-app/migrations/014_endpoint_health_response_shape.sql`:

```sql
ALTER TABLE endpoint_health ADD COLUMN IF NOT EXISTS response_shape TEXT[];
```

- [ ] **Step 6: Write the failing DB-layer test**

Add to `github-app/tests/test_scan_worker_db.py`, right after `test_insert_and_get_last_endpoint_health`:

```python
@pytest.mark.asyncio
async def test_insert_and_get_last_endpoint_health_with_response_shape(pool):
    await _insert_installation(pool, 301, "a")

    insert_endpoint_health(
        TEST_DATABASE_URL, 301, "a/repo1", "GET", "/x", True, 200, 120.5,
        response_shape=["email", "id", "name"],
    )
    last = get_last_endpoint_health(TEST_DATABASE_URL, 301, "a/repo1", "GET", "/x")

    assert last["response_shape"] == ["email", "id", "name"]


@pytest.mark.asyncio
async def test_insert_endpoint_health_defaults_response_shape_to_none(pool):
    await _insert_installation(pool, 301, "a")

    insert_endpoint_health(TEST_DATABASE_URL, 301, "a/repo1", "GET", "/x", True, 200, 120.5)
    last = get_last_endpoint_health(TEST_DATABASE_URL, 301, "a/repo1", "GET", "/x")

    assert last["response_shape"] is None
```

- [ ] **Step 7: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_scan_worker_db.py -v -k response_shape`
Expected: FAIL (`relation "endpoint_health" has no column "response_shape"` or the returned dict lacks the key) or SKIP if no local Postgres.

- [ ] **Step 8: Update `db.py`**

Change `get_last_endpoint_health`'s query from:

```python
                SELECT reachable, status_code, latency_ms, checked_at
```

to:

```python
                SELECT reachable, status_code, latency_ms, response_shape, checked_at
```

Change `insert_endpoint_health`'s signature and INSERT statement from:

```python
def insert_endpoint_health(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    method: str,
    path: str,
    reachable: bool,
    status_code: int | None,
    latency_ms: float | None,
    target_id: int | None = None,
    keep: int = 20,
) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO endpoint_health
                    (installation_id, repo_full_name, endpoint_method, endpoint_path,
                     reachable, status_code, latency_ms, target_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (installation_id, repo_full_name, method, path, reachable, status_code, latency_ms, target_id),
            )
```

to:

```python
def insert_endpoint_health(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    method: str,
    path: str,
    reachable: bool,
    status_code: int | None,
    latency_ms: float | None,
    response_shape: list[str] | None = None,
    target_id: int | None = None,
    keep: int = 20,
) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO endpoint_health
                    (installation_id, repo_full_name, endpoint_method, endpoint_path,
                     reachable, status_code, latency_ms, response_shape, target_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    installation_id,
                    repo_full_name,
                    method,
                    path,
                    reachable,
                    status_code,
                    latency_ms,
                    response_shape,
                    target_id,
                ),
            )
```

(The `DELETE ... OFFSET %s` rotation query directly below is unaffected - leave it exactly as is.)

- [ ] **Step 9: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_scan_worker_db.py -v`
Expected: all PASS (or SKIP without local Postgres - confirmed in CI regardless).

- [ ] **Step 10: Run full suites to check for regressions**

Run: `cd prototype && python -m pytest -q && cd ../github-app && python -m pytest -q`
Expected: all PASS.

- [ ] **Step 11: Commit**

```bash
git add prototype/aletheore/healthcheck.py prototype/tests/test_healthcheck.py github-app/migrations/014_endpoint_health_response_shape.sql github-app/scan_worker/db.py github-app/tests/test_scan_worker_db.py
git commit -m "feat: capture response shape (keys only) for health checks"
```

---

### Task 2: Recent-commit lookup via GitHub API

**Files:**
- Modify: `github-app/scan_worker/github_api.py`
- Test: `github-app/tests/test_github_api.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `fetch_recent_commits_for_path(client: httpx.Client, token: str, repo_full_name: str, path: str, limit: int = 1) -> list[dict]`, returning dicts shaped `{"sha": str, "author": str | None, "date": str | None, "subject": str}` - the same shape `aletheore.evidence_resolution.resolve_recent_commit` already produces for its `commit` field, so it can be fed straight into `normalize_resolution(kind="commit", commit=..., confidence="weak")` in Task 4 without any new glue code.

No repo clone is needed for this - it's a live call to GitHub's `GET /repos/{repo}/commits?path=...` endpoint, which is why this can run from the health-check sweep (which never clones anything) rather than requiring `resolve_recent_commit`'s local-git approach.

- [ ] **Step 1: Write the failing test**

Add to `github-app/tests/test_github_api.py`:

```python
from scan_worker.github_api import fetch_recent_commits_for_path
```

```python
def test_fetch_recent_commits_for_path_returns_shaped_commits():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/octocat/hello-world/commits"
        assert dict(request.url.params) == {"path": "controllers/user.controller.ts", "per_page": "1"}
        return httpx.Response(
            200,
            json=[
                {
                    "sha": "abc123def456",
                    "commit": {
                        "author": {"name": "Ada Lovelace", "date": "2026-07-23T10:00:00Z"},
                        "message": "fix: guard against null user id\n\nlonger body here",
                    },
                }
            ],
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    commits = fetch_recent_commits_for_path(client, "token", "octocat/hello-world", "controllers/user.controller.ts")

    assert commits == [
        {
            "sha": "abc123def456",
            "author": "Ada Lovelace",
            "date": "2026-07-23T10:00:00Z",
            "subject": "fix: guard against null user id",
        }
    ]


def test_fetch_recent_commits_for_path_respects_limit():
    def handler(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params)["per_page"] == "3"
        return httpx.Response(200, json=[])

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    fetch_recent_commits_for_path(client, "token", "octocat/hello-world", "app.py", limit=3)


def test_fetch_recent_commits_for_path_returns_empty_list_for_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    commits = fetch_recent_commits_for_path(client, "token", "octocat/hello-world", "deleted_file.py")

    assert commits == []


def test_fetch_recent_commits_for_path_returns_empty_list_when_no_commits():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    commits = fetch_recent_commits_for_path(client, "token", "octocat/hello-world", "app.py")

    assert commits == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_github_api.py -v -k recent_commits`
Expected: FAIL with `ImportError: cannot import name 'fetch_recent_commits_for_path'`.

- [ ] **Step 3: Implement `fetch_recent_commits_for_path`**

Append to `github-app/scan_worker/github_api.py`:

```python
def fetch_recent_commits_for_path(
    client: httpx.Client, token: str, repo_full_name: str, path: str, limit: int = 1
) -> list[dict]:
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    response = client.get(
        f"/repos/{repo_full_name}/commits",
        headers=headers,
        params={"path": path, "per_page": limit},
    )
    if response.status_code == 404:
        return []
    response.raise_for_status()
    commits = []
    for item in response.json():
        commit = item.get("commit", {})
        author = commit.get("author", {}) or {}
        message = commit.get("message") or ""
        commits.append(
            {
                "sha": item.get("sha"),
                "author": author.get("name"),
                "date": author.get("date"),
                "subject": message.split("\n", 1)[0],
            }
        )
    return commits
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_github_api.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add github-app/scan_worker/github_api.py github-app/tests/test_github_api.py
git commit -m "feat: add GitHub API lookup for recent commits touching a path"
```

---

### Task 3: Shape-change Slack alert

**Files:**
- Modify: `github-app/scan_worker/slack.py`
- Test: `github-app/tests/test_slack.py`

**Interfaces:**
- Consumes: nothing new (reuses the existing `_format_evidence_context` helper already in this file).
- Produces: `format_shape_change_alert(repo_full_name: str, method: str, path: str, source_file: str | None, source_line: int | None, prior_shape: list[str], current_shape: list[str], evidence_resolution: dict | None = None) -> dict`, consumed by Task 4's `jobs.py` wiring.

- [ ] **Step 1: Write the failing tests**

Add to `github-app/tests/test_slack.py`:

```python
from scan_worker.slack import format_shape_change_alert
```

```python
def test_format_shape_change_alert_reports_added_and_dropped_keys():
    body = format_shape_change_alert(
        "octocat/hello-world",
        "GET",
        "/api/users",
        "controllers/user.controller.ts",
        42,
        prior_shape=["email", "id", "name"],
        current_shape=["id", "name", "role"],
    )

    assert "response shape changed" in body["text"]
    assert "added keys: role" in body["text"]
    assert "dropped keys: email" in body["text"]
    assert "controllers/user.controller.ts:42" in body["text"]


def test_format_shape_change_alert_includes_evidence_context():
    evidence_resolution = {
        "commit": {"sha": "abcdef123456", "subject": "drop email from response"},
    }
    body = format_shape_change_alert(
        "octocat/hello-world",
        "GET",
        "/api/users",
        None,
        None,
        prior_shape=["email", "id"],
        current_shape=["id"],
        evidence_resolution=evidence_resolution,
    )

    assert "Recent commit: `abcdef12`" in body["text"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_slack.py -v -k shape_change`
Expected: FAIL with `ImportError: cannot import name 'format_shape_change_alert'`.

- [ ] **Step 3: Implement `format_shape_change_alert`**

Append to `github-app/scan_worker/slack.py`:

```python
def format_shape_change_alert(
    repo_full_name: str,
    method: str,
    path: str,
    source_file: str | None,
    source_line: int | None,
    prior_shape: list[str],
    current_shape: list[str],
    evidence_resolution: dict | None = None,
) -> dict:
    location = (
        f" - handled by {source_file}:{source_line}"
        if source_file and source_line is not None
        else ""
    )
    added = sorted(set(current_shape) - set(prior_shape))
    dropped = sorted(set(prior_shape) - set(current_shape))
    changes = []
    if added:
        changes.append(f"added keys: {', '.join(added)}")
    if dropped:
        changes.append(f"dropped keys: {', '.join(dropped)}")
    change_summary = "; ".join(changes) if changes else "key order changed"
    text = (
        f"*Aletheore*: response shape changed on `{repo_full_name}`\n"
        f"`{method} {path}` {change_summary}{location}"
        f"{_format_evidence_context(evidence_resolution)}"
    )
    return {"text": text}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_slack.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add github-app/scan_worker/slack.py github-app/tests/test_slack.py
git commit -m "feat: add Slack alert for endpoint response shape changes"
```

---

### Task 4: Wire retry-hydration, commit correlation, and shape-change alerts into the sweep

**Files:**
- Modify: `github-app/scan_worker/jobs.py:1-24,424-552` (imports + `_endpoint_results`, `run_health_check_sweep_job`)
- Test: `github-app/tests/test_jobs.py`

**Interfaces:**
- Consumes: `response_shape` from Task 1's `run_healthcheck`/`insert_endpoint_health`/`get_last_endpoint_health`; `fetch_recent_commits_for_path` from Task 2; `format_shape_change_alert` from Task 3; `normalize_resolution`, `merge_resolution`, `empty_resolution` from `aletheore.evidence_resolution` (already a real module, `resolve_code_evidence` is already imported from it in this file).
- Produces: nothing new for later tasks - this is the final integration point.

- [ ] **Step 1: Write the failing tests**

First, update the shared `_patch_sweep` helper in `github-app/tests/test_jobs.py` to silence real sleeps (retries would otherwise add real wall-clock delay to every down-flip test) and accept an optional response-shape override. Replace the existing `_patch_sweep` function with:

```python
def _patch_sweep(
    monkeypatch,
    *,
    threshold_ms=None,
    prior=None,
    result_entry=None,
    evidence=None,
    retry_result_entry=None,
):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.time.sleep", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.list_health_check_targets_all",
        lambda dsn: [
            {
                "target_id": 900,
                "installation_id": 1,
                "repo_full_name": "octocat/hello-world",
                "label": "Primary",
                "base_url": "https://api.example.com",
                "latency_threshold_ms": threshold_ms,
                "webhook_url": "https://hooks.slack.com/health",
            }
        ],
    )
    monkeypatch.setattr(
        "scan_worker.jobs.get_latest_evidence",
        lambda dsn, iid, repo: evidence
        or {"repository": {"api_endpoints": {"endpoints": [{"method": "GET", "path": "/x"}]}}},
    )
    default_first = result_entry or {
        "method": "GET",
        "path": "/x",
        "reachable": True,
        "status_code": 200,
        "latency_ms": 90.0,
        "response_shape": None,
    }
    calls = {"count": 0}

    def fake_healthcheck(endpoints, base_url):
        calls["count"] += 1
        if calls["count"] == 1 or retry_result_entry is None:
            return {"results": [default_first]}
        return {"results": [retry_result_entry]}

    monkeypatch.setattr("scan_worker.jobs.run_healthcheck", fake_healthcheck)
    monkeypatch.setattr(
        "scan_worker.jobs.get_last_endpoint_health", lambda dsn, iid, repo, method, path, target_id=None: prior
    )
    monkeypatch.setattr("scan_worker.jobs.insert_endpoint_health", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr("scan_worker.jobs.send_health_alert", lambda url, msg, **k: sent.append(msg))
    return sent
```

This changes `fake_healthcheck` from a plain lambda into a call-counting function so a test can supply a different result for the retry call(s) than for the first call - every existing caller that doesn't pass `retry_result_entry` gets the exact same `default_first` result on every call, identical to the old behavior.

Now add new tests, right after `test_sweep_sends_reachability_down_alert`:

```python
def test_sweep_retries_before_confirming_down_and_recovers_silently(monkeypatch):
    sent = _patch_sweep(
        monkeypatch,
        prior={"reachable": True, "latency_ms": 100.0},
        result_entry={"method": "GET", "path": "/x", "reachable": False, "status_code": None, "latency_ms": 10.0, "response_shape": None},
        retry_result_entry={"method": "GET", "path": "/x", "reachable": True, "status_code": 200, "latency_ms": 95.0, "response_shape": None},
    )

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    assert sent == []


def test_sweep_confirms_down_after_retries_all_fail(monkeypatch):
    sent = _patch_sweep(
        monkeypatch,
        prior={"reachable": True, "latency_ms": 100.0},
        result_entry={"method": "GET", "path": "/x", "reachable": False, "status_code": None, "latency_ms": 10.0, "response_shape": None},
    )

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    assert len(sent) == 1
    assert "down" in sent[0]["text"]


def test_sweep_does_not_retry_a_recovery_flip(monkeypatch):
    healthcheck_calls = []
    sent = _patch_sweep(
        monkeypatch,
        prior={"reachable": False, "latency_ms": None},
        result_entry={"method": "GET", "path": "/x", "reachable": True, "status_code": 200, "latency_ms": 80.0, "response_shape": None},
    )
    monkeypatch.setattr(
        "scan_worker.jobs.run_healthcheck",
        lambda endpoints, base_url: healthcheck_calls.append(True)
        or {"results": [{"method": "GET", "path": "/x", "reachable": True, "status_code": 200, "latency_ms": 80.0, "response_shape": None}]},
    )

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    assert len(healthcheck_calls) == 1
    assert len(sent) == 1
    assert "recovered" in sent[0]["text"]


def test_sweep_attaches_recent_commit_on_confirmed_down(monkeypatch):
    sent = _patch_sweep(
        monkeypatch,
        prior={"reachable": True, "latency_ms": 100.0},
        evidence={
            "repository": {
                "api_endpoints": {
                    "endpoints": [
                        {"method": "GET", "path": "/x", "file": "controllers/user.controller.ts", "line": 42}
                    ]
                }
            }
        },
        result_entry={"method": "GET", "path": "/x", "reachable": False, "status_code": None, "latency_ms": 10.0, "response_shape": None},
    )
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "indie"})
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr(
        "scan_worker.jobs.fetch_recent_commits_for_path",
        lambda client, token, repo, path, limit=1: [
            {"sha": "abc123def456", "author": "Ada", "date": "2026-07-23T10:00:00Z", "subject": "touched the handler"}
        ],
    )

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    assert len(sent) == 1
    assert "Recent commit: `abc123de`" in sent[0]["text"]
    assert "touched the handler" in sent[0]["text"]


def test_sweep_alerts_without_commit_when_correlation_fails(monkeypatch):
    sent = _patch_sweep(
        monkeypatch,
        prior={"reachable": True, "latency_ms": 100.0},
        evidence={
            "repository": {
                "api_endpoints": {
                    "endpoints": [
                        {"method": "GET", "path": "/x", "file": "controllers/user.controller.ts", "line": 42}
                    ]
                }
            }
        },
        result_entry={"method": "GET", "path": "/x", "reachable": False, "status_code": None, "latency_ms": 10.0, "response_shape": None},
    )
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "indie"})

    def _raise(*a, **k):
        raise RuntimeError("github api unavailable")

    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", _raise)

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    assert len(sent) == 1
    assert "down" in sent[0]["text"]
    assert "Recent commit" not in sent[0]["text"]


def test_sweep_sends_shape_change_alert_while_still_reachable(monkeypatch):
    sent = _patch_sweep(
        monkeypatch,
        prior={"reachable": True, "latency_ms": 100.0, "response_shape": ["email", "id", "name"]},
        result_entry={"method": "GET", "path": "/x", "reachable": True, "status_code": 200, "latency_ms": 90.0, "response_shape": ["id", "name"]},
    )

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    assert len(sent) == 1
    assert "response shape changed" in sent[0]["text"]
    assert "dropped keys: email" in sent[0]["text"]


def test_sweep_skips_shape_alert_when_prior_shape_unknown(monkeypatch):
    sent = _patch_sweep(
        monkeypatch,
        prior={"reachable": True, "latency_ms": 100.0, "response_shape": None},
        result_entry={"method": "GET", "path": "/x", "reachable": True, "status_code": 200, "latency_ms": 90.0, "response_shape": ["id"]},
    )

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    assert sent == []
```

Now update `test_sweep_checks_every_target_independently` (it builds its own mocks inline rather than using `_patch_sweep`, and its `insert_endpoint_health` lambda has a fixed positional signature that the new `response_shape` keyword argument would break). Add this line right after its existing `monkeypatch.setattr("scan_worker.jobs.list_health_check_targets_all", ...)` block, before the `get_latest_evidence` patch:

```python
    monkeypatch.setattr("scan_worker.jobs.time.sleep", lambda *a, **k: None)
```

And change its `insert_endpoint_health` lambda from:

```python
    monkeypatch.setattr(
        "scan_worker.jobs.insert_endpoint_health",
        lambda dsn, iid, repo, method, path, reachable, status_code, latency_ms, target_id=None, keep=20: recorded.append(
            (target_id, reachable)
        ),
    )
```

to:

```python
    monkeypatch.setattr(
        "scan_worker.jobs.insert_endpoint_health",
        lambda dsn, iid, repo, method, path, reachable, status_code, latency_ms, response_shape=None, target_id=None, keep=20: recorded.append(
            (target_id, reachable)
        ),
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_jobs.py -v -k "sweep"`
Expected: several FAIL - the new retry/commit/shape tests fail because the behavior doesn't exist yet, and `test_sweep_checks_every_target_independently` may already pass since it doesn't yet exercise a real down-retry path, but confirm all current sweep tests' baseline status before Step 3.

- [ ] **Step 3: Update imports in `jobs.py`**

Add `import time` to the top-level stdlib imports (alongside `import asyncio`, `import inspect`, etc. - keep alphabetical: after `import subprocess` and before `import uuid`).

Change the `aletheore.evidence_resolution` import from:

```python
from aletheore.evidence_resolution import resolve_code_evidence
```

to:

```python
from aletheore.evidence_resolution import empty_resolution, merge_resolution, normalize_resolution, resolve_code_evidence
```

Change the `scan_worker.github_api` import from:

```python
from scan_worker.github_api import create_check_run, fetch_pr_changed_files, fetch_pr_diff, upsert_pr_comment
```

to:

```python
from scan_worker.github_api import (
    create_check_run,
    fetch_pr_changed_files,
    fetch_pr_diff,
    fetch_recent_commits_for_path,
    upsert_pr_comment,
)
```

Change the `scan_worker.slack` import from:

```python
from scan_worker.slack import (
    format_latency_alert,
    format_reachability_alert,
    send_health_alert,
    send_slack_alert,
)
```

to:

```python
from scan_worker.slack import (
    format_latency_alert,
    format_reachability_alert,
    format_shape_change_alert,
    send_health_alert,
    send_slack_alert,
)
```

- [ ] **Step 4: Update `_endpoint_results` to carry `response_shape` through**

`_endpoint_results` already returns whatever `run_healthcheck` produces per entry unmodified aside from adding `file`/`line`/`evidence_resolution` - since Task 1 already made `run_healthcheck` include `"response_shape"` in every entry, no change is needed here. Confirm this by re-reading the current `_endpoint_results` function - it does not need editing in this task.

- [ ] **Step 5: Add retry-hydration and commit-correlation helpers, and rewrite `run_health_check_sweep_job`**

Add these two constants and one helper function right before `def run_health_check_sweep_job(`:

```python
HEALTH_CHECK_DOWN_RETRY_ATTEMPTS = 2
HEALTH_CHECK_DOWN_RETRY_DELAY_SECONDS = 2.0


def _recheck_single_endpoint(entry: dict, base_url: str) -> dict:
    minimal_endpoint = {"method": entry.get("method"), "path": entry["path"]}
    results = run_healthcheck([minimal_endpoint], base_url).get("results", [])
    if not results:
        return {"reachable": False, "status_code": None, "latency_ms": None, "response_shape": None}
    return results[0]


def _attach_recent_commit_for_failure(
    settings,
    installation_id: int,
    repo_full_name: str,
    source_file: str,
    evidence_resolution: dict | None,
) -> dict | None:
    try:
        app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
        token = _token_sync(installation_id, app_jwt)
        client = httpx.Client(base_url="https://api.github.com")
        commits = fetch_recent_commits_for_path(client, token, repo_full_name, source_file, limit=1)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("scan_worker.jobs").warning(
            "commit correlation failed (%s); alerting without it", type(exc).__name__
        )
        return evidence_resolution
    if not commits:
        return evidence_resolution
    commit_attachment = normalize_resolution(kind="commit", commit=commits[0], confidence="weak")
    base = evidence_resolution or empty_resolution("endpoint")
    return merge_resolution(base, commit_attachment)
```

Replace the entire body of `run_health_check_sweep_job` (the `for entry in _endpoint_results(evidence, base_url):` loop) with:

```python
@log_job
def run_health_check_sweep_job() -> None:
    settings = get_settings()
    dsn = settings.database_url

    for target in list_health_check_targets_all(dsn):
        installation_id = target["installation_id"]
        repo_full_name = target["repo_full_name"]
        target_id = target["target_id"]
        base_url = target["base_url"]
        threshold_ms = target["latency_threshold_ms"]

        evidence = get_latest_evidence(dsn, installation_id, repo_full_name)
        if evidence is None:
            continue

        for entry in _endpoint_results(evidence, base_url):
            if entry.get("skipped"):
                continue
            method = entry["method"]
            path = entry["path"]
            source_file = entry.get("file")
            source_line = entry.get("line")
            evidence_resolution = entry.get("evidence_resolution")
            reachable = entry["reachable"]
            status_code = entry.get("status_code")
            latency_ms = entry.get("latency_ms")
            response_shape = entry.get("response_shape")
            prior = get_last_endpoint_health(
                dsn,
                installation_id,
                repo_full_name,
                method,
                path,
                target_id=target_id,
            )

            reachability_flipped = (prior is None and not reachable) or (
                prior is not None and prior.get("reachable") != reachable
            )

            if reachability_flipped and not reachable:
                for _ in range(HEALTH_CHECK_DOWN_RETRY_ATTEMPTS):
                    time.sleep(HEALTH_CHECK_DOWN_RETRY_DELAY_SECONDS)
                    retry_result = _recheck_single_endpoint(entry, base_url)
                    if retry_result.get("reachable"):
                        reachable = True
                        status_code = retry_result.get("status_code")
                        latency_ms = retry_result.get("latency_ms")
                        response_shape = retry_result.get("response_shape")
                        break
                reachability_flipped = (prior is None and not reachable) or (
                    prior is not None and prior.get("reachable") != reachable
                )

            if reachability_flipped:
                if not reachable and source_file:
                    evidence_resolution = _attach_recent_commit_for_failure(
                        settings, installation_id, repo_full_name, source_file, evidence_resolution
                    )
                _send_if_webhook_configured(
                    target,
                    format_reachability_alert(
                        repo_full_name,
                        method,
                        path,
                        source_file,
                        source_line,
                        reachable,
                        evidence_resolution=evidence_resolution,
                    ),
                )

            if _latency_flipped(prior, reachable, latency_ms, threshold_ms):
                _send_if_webhook_configured(
                    target,
                    format_latency_alert(
                        repo_full_name,
                        method,
                        path,
                        source_file,
                        source_line,
                        latency_ms,
                        threshold_ms,
                        latency_ms > threshold_ms,
                        evidence_resolution=evidence_resolution,
                    ),
                )

            shape_changed = (
                reachable
                and not reachability_flipped
                and prior is not None
                and prior.get("reachable") is True
                and prior.get("response_shape") is not None
                and response_shape is not None
                and prior["response_shape"] != response_shape
            )
            if shape_changed:
                _send_if_webhook_configured(
                    target,
                    format_shape_change_alert(
                        repo_full_name,
                        method,
                        path,
                        source_file,
                        source_line,
                        prior["response_shape"],
                        response_shape,
                        evidence_resolution=evidence_resolution,
                    ),
                )

            insert_endpoint_health(
                dsn,
                installation_id,
                repo_full_name,
                method,
                path,
                reachable,
                status_code,
                latency_ms,
                response_shape=response_shape,
                target_id=target_id,
            )
```

Note the added `and not reachability_flipped` in the `shape_changed` condition: a reachability flip already sends its own alert this tick, and `prior.get("reachable") is True` combined with `reachability_flipped` being true would only occur on a flip *to* down (where `reachable` is now false, so `shape_changed`'s leading `reachable and` already excludes it) - the explicit `not reachability_flipped` guard is defensive belt-and-suspenders so a shape-change alert never doubles up with a reachability alert in the same tick.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_jobs.py -v -k "sweep"`
Expected: all PASS.

- [ ] **Step 7: Run the full github-app and prototype suites to check for regressions**

Run: `cd github-app && python -m pytest -q && cd ../prototype && python -m pytest -q`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add github-app/scan_worker/jobs.py github-app/tests/test_jobs.py
git commit -m "feat: retry before alerting on endpoint down, correlate confirmed failures to recent commits"
```

---

## Self-Review

**Spec coverage:**
- Retry-hydration queue (no more trusting a single failed check) → Task 4, `HEALTH_CHECK_DOWN_RETRY_ATTEMPTS`/`HEALTH_CHECK_DOWN_RETRY_DELAY_SECONDS`, only on down-flips, recovery-flips untouched per the user's stated scope. ✅
- Recent-commit correlation on endpoint failure → Task 2 (`fetch_recent_commits_for_path`, live GitHub API, no clone) + Task 4 (`_attach_recent_commit_for_failure`, fires only post-retry-confirmation, fails open). ✅
- Time-travel diagnostic replay (shape only, never values, per the user's explicit privacy-scoped decision) → Task 1 (`_response_shape` captures top-level JSON keys only) + Task 3 (`format_shape_change_alert`) + Task 4 (wiring + `test_sweep_sends_shape_change_alert_while_still_reachable`). ✅
- Managed audits and Flash review are untouched - this entire plan only modifies the health-check sweep path (`healthcheck.py`, `github_api.py`, `slack.py`, `jobs.py`'s `run_health_check_sweep_job`), consistent with this being a distinct phase from Phase 3's model-call paths. ✅

**Placeholder scan:** No "TBD"/"TODO" in any task; every step shows complete code, not descriptions of code.

**Type consistency:** `fetch_recent_commits_for_path(...) -> list[dict]`'s dict shape (`sha`/`author`/`date`/`subject`) matches exactly what `normalize_resolution(kind="commit", commit=commits[0], ...)` expects (matching `resolve_recent_commit`'s existing shape in `evidence_resolution.py`, confirmed by reading that function before writing this plan). `insert_endpoint_health`'s new `response_shape: list[str] | None = None` parameter matches `_response_shape(...) -> list[str] | None`'s return type and `get_last_endpoint_health`'s returned `"response_shape"` value throughout Task 4's sweep logic. `format_shape_change_alert`'s `prior_shape`/`current_shape: list[str]` parameters are only ever called with non-`None` values in Task 4 (the `shape_changed` condition explicitly requires both to be non-`None` before calling it).

**Scope check:** All three pieces are independently testable and additive - Task 1 (shape capture) has no dependency on Tasks 2-4 and is useful once merged even before wiring; Task 2 (commit lookup) and Task 3 (shape alert) are pure new functions with zero existing callers until Task 4 wires them in. Task 4 depends on all three prior tasks but not the other way around.
