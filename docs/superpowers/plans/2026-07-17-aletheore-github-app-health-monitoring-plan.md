# GitHub App Endpoint Health Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship scheduled endpoint health monitoring for paid installations — Ofelia triggers a sweep every 3 minutes, `run_healthcheck` (existing, unchanged) pings each installation's most recently scanned endpoints, and Slack/Teams alerts fire on a reachability flip and/or a customer-set latency-threshold flip.

**Architecture:** One new RQ job (`run_health_check_sweep_job`) in `scan_worker/jobs.py`, triggered on a schedule by an `ofelia` sidecar (proven pattern already running on the sibling Procta project) that execs a one-line enqueue inside the already-running `scan-worker` container. New Postgres columns/table track per-installation config and per-endpoint history so flips can be detected by comparing to the prior row.

## Global Constraints

- Reuse `run_healthcheck(endpoints, base_url)` (`aletheore/healthcheck.py`) unchanged.
- Latency comparison only applies when `reachable is True` — an unreachable endpoint's `latency_ms` reflects a failed attempt's duration, not a meaningful response time.
- Both alert types (reachability, latency) fire only on a flip, never on every check while a condition persists.
- `health_check_base_url`/`health_check_latency_threshold_ms` unset means that check is off — never guess or default a threshold.
- Migration file only — must be applied manually to the live database (`docker-entrypoint-initdb.d` only runs on a fresh Postgres init).

---

## Task 1: Schema + DB functions

**Files:**
- Create: `github-app/migrations/003_health_monitoring.sql`
- Modify: `github-app/app_server/db.py` (add `set_health_check_config`, extend `get_installation`'s SELECT)
- Modify: `github-app/scan_worker/db.py` (add `list_monitored_installations`, `list_repos_for_installation`, `get_latest_evidence`, `get_last_endpoint_health`, `insert_endpoint_health`; extend `get_installation`'s SELECT)
- Test: `github-app/tests/test_db.py`, `github-app/tests/test_scan_worker_db.py`

**Interfaces:**
- Produces: `set_health_check_config(pool, installation_id, base_url: str | None, threshold_ms: int | None) -> None` (async); `list_monitored_installations(dsn) -> list[dict]`, `list_repos_for_installation(dsn, installation_id) -> list[str]`, `get_latest_evidence(dsn, installation_id, repo_full_name) -> dict | None`, `get_last_endpoint_health(dsn, installation_id, repo_full_name, method, path) -> dict | None`, `insert_endpoint_health(dsn, installation_id, repo_full_name, method, path, reachable, status_code, latency_ms, keep=20) -> None` (all sync). Consumed by Tasks 2 and 4.

- [ ] **Step 1: Write the migration**

```sql
ALTER TABLE installations ADD COLUMN health_check_base_url TEXT;
ALTER TABLE installations ADD COLUMN health_check_latency_threshold_ms INT;

CREATE TABLE endpoint_health (
    id               BIGSERIAL PRIMARY KEY,
    installation_id  BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name   TEXT NOT NULL,
    endpoint_method  TEXT NOT NULL,
    endpoint_path    TEXT NOT NULL,
    reachable        BOOLEAN NOT NULL,
    status_code      INT,
    latency_ms       NUMERIC,
    checked_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX endpoint_health_lookup
    ON endpoint_health (installation_id, repo_full_name, endpoint_method, endpoint_path, checked_at DESC);
```

Apply to test DB: `PGPASSWORD=test psql -h localhost -p 55433 -U postgres -d aletheore_test -f github-app/migrations/003_health_monitoring.sql`

- [ ] **Step 2: Write failing tests**

Append to `github-app/tests/test_db.py`:

```python
from app_server.db import get_installation, set_health_check_config, upsert_installation


@pytest.mark.asyncio
async def test_set_health_check_config(pool):
    await upsert_installation(pool, 300, "octocat")
    await set_health_check_config(pool, 300, "https://api.example.com", 3000)
    row = await get_installation(pool, 300)
    assert row["health_check_base_url"] == "https://api.example.com"
    assert row["health_check_latency_threshold_ms"] == 3000


@pytest.mark.asyncio
async def test_set_health_check_config_clears_with_none(pool):
    await upsert_installation(pool, 300, "octocat")
    await set_health_check_config(pool, 300, "https://api.example.com", 3000)
    await set_health_check_config(pool, 300, None, None)
    row = await get_installation(pool, 300)
    assert row["health_check_base_url"] is None
    assert row["health_check_latency_threshold_ms"] is None
```

Create `github-app/tests/test_scan_worker_db.py` additions (append; `dsn` fixture already exists in that file):

```python
from datetime import datetime, timezone

from scan_worker.db import (
    get_last_endpoint_health,
    get_latest_evidence,
    insert_endpoint_health,
    insert_repo_history,
    list_monitored_installations,
    list_repos_for_installation,
)


def test_list_monitored_installations_filters_plan_and_url(dsn):
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO installations (installation_id, account_login, plan, health_check_base_url) "
                "VALUES (301, 'a', 'pro', 'https://a.example.com'), "
                "(302, 'b', 'free', 'https://b.example.com'), "
                "(303, 'c', 'pro', NULL)"
            )
        conn.commit()

    result = list_monitored_installations(dsn)
    ids = {row["installation_id"] for row in result}
    assert ids == {301}


def test_list_repos_for_installation(dsn):
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO installations (installation_id, account_login) VALUES (301, 'a')")
        conn.commit()
    insert_repo_history(dsn, 301, "a/repo1", datetime.now(timezone.utc), {"x": 1})
    insert_repo_history(dsn, 301, "a/repo2", datetime.now(timezone.utc), {"x": 1})

    repos = list_repos_for_installation(dsn, 301)
    assert set(repos) == {"a/repo1", "a/repo2"}


def test_get_latest_evidence_returns_most_recent(dsn):
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO installations (installation_id, account_login) VALUES (301, 'a')")
        conn.commit()
    insert_repo_history(dsn, 301, "a/repo1", datetime(2026, 1, 1, tzinfo=timezone.utc), {"v": 1})
    insert_repo_history(dsn, 301, "a/repo1", datetime(2026, 1, 2, tzinfo=timezone.utc), {"v": 2})

    evidence = get_latest_evidence(dsn, 301, "a/repo1")
    assert evidence["v"] == 2


def test_insert_and_get_last_endpoint_health(dsn):
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO installations (installation_id, account_login) VALUES (301, 'a')")
        conn.commit()

    assert get_last_endpoint_health(dsn, 301, "a/repo1", "GET", "/x") is None
    insert_endpoint_health(dsn, 301, "a/repo1", "GET", "/x", True, 200, 120.5)
    last = get_last_endpoint_health(dsn, 301, "a/repo1", "GET", "/x")
    assert last["reachable"] is True
    assert last["latency_ms"] == 120.5


def test_endpoint_health_rotation_keeps_20(dsn):
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO installations (installation_id, account_login) VALUES (301, 'a')")
        conn.commit()
    for _ in range(21):
        insert_endpoint_health(dsn, 301, "a/repo1", "GET", "/x", True, 200, 100.0, keep=20)

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM endpoint_health WHERE installation_id = 301")
            assert cur.fetchone()[0] == 20
```

- [ ] **Step 3: Run to verify failure**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_db.py tests/test_scan_worker_db.py -v`
Expected: FAIL (`ImportError`/`AttributeError` — functions don't exist)

- [ ] **Step 4: Implement `app_server/db.py` additions**

Modify `get_installation`'s SELECT to add the two new columns:

```python
async def get_installation(pool: asyncpg.Pool, installation_id: int) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT installation_id, account_login, plan, webhook_url, max_api_tokens,
               health_check_base_url, health_check_latency_threshold_ms
        FROM installations
        WHERE installation_id = $1
        """,
        installation_id,
    )
    return dict(row) if row else None
```

Append:

```python
async def set_health_check_config(
    pool: asyncpg.Pool, installation_id: int, base_url: str | None, threshold_ms: int | None
) -> None:
    await pool.execute(
        """
        UPDATE installations
        SET health_check_base_url = $2, health_check_latency_threshold_ms = $3, updated_at = now()
        WHERE installation_id = $1
        """,
        installation_id,
        base_url,
        threshold_ms,
    )
```

- [ ] **Step 5: Implement `scan_worker/db.py` additions**

Modify `get_installation`'s SELECT the same way (add the two new columns to that function's query too).

Append:

```python
def list_monitored_installations(dsn: str) -> list[dict]:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT installation_id, health_check_base_url, health_check_latency_threshold_ms
                FROM installations
                WHERE plan != 'free' AND health_check_base_url IS NOT NULL
                """
            )
            columns = [d[0] for d in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]


def list_repos_for_installation(dsn: str, installation_id: int) -> list[str]:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT repo_full_name FROM repo_history WHERE installation_id = %s",
                (installation_id,),
            )
            return [row[0] for row in cur.fetchall()]


def get_latest_evidence(dsn: str, installation_id: int, repo_full_name: str) -> dict | None:
    import json

    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT evidence FROM repo_history
                WHERE installation_id = %s AND repo_full_name = %s
                ORDER BY scanned_at DESC, id DESC
                LIMIT 1
                """,
                (installation_id, repo_full_name),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return json.loads(row[0]) if isinstance(row[0], str) else row[0]


def get_last_endpoint_health(
    dsn: str, installation_id: int, repo_full_name: str, method: str, path: str
) -> dict | None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT reachable, status_code, latency_ms, checked_at
                FROM endpoint_health
                WHERE installation_id = %s AND repo_full_name = %s
                      AND endpoint_method = %s AND endpoint_path = %s
                ORDER BY checked_at DESC, id DESC
                LIMIT 1
                """,
                (installation_id, repo_full_name, method, path),
            )
            row = cur.fetchone()
            if row is None:
                return None
            columns = [d[0] for d in cur.description]
            return dict(zip(columns, row))


def insert_endpoint_health(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    method: str,
    path: str,
    reachable: bool,
    status_code: int | None,
    latency_ms: float | None,
    keep: int = 20,
) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO endpoint_health
                    (installation_id, repo_full_name, endpoint_method, endpoint_path,
                     reachable, status_code, latency_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (installation_id, repo_full_name, method, path, reachable, status_code, latency_ms),
            )
            cur.execute(
                """
                DELETE FROM endpoint_health
                WHERE id IN (
                    SELECT id FROM endpoint_health
                    WHERE installation_id = %s AND repo_full_name = %s
                          AND endpoint_method = %s AND endpoint_path = %s
                    ORDER BY checked_at DESC, id DESC
                    OFFSET %s
                )
                """,
                (installation_id, repo_full_name, method, path, keep),
            )
        conn.commit()
```

- [ ] **Step 6: Run to verify pass**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_db.py tests/test_scan_worker_db.py -v`
Expected: PASS (all tests)

- [ ] **Step 7: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/migrations/003_health_monitoring.sql github-app/app_server/db.py \
        github-app/scan_worker/db.py github-app/tests/test_db.py github-app/tests/test_scan_worker_db.py
git commit -m "feat(github-app): health-monitoring schema + DB functions"
```

---

## Task 2: Admin route for health-check config

**Files:**
- Modify: `github-app/app_server/admin.py`
- Test: `github-app/tests/test_admin.py`

**Interfaces:**
- Consumes: `set_health_check_config` (Task 1).
- Produces: `PUT /admin/{org}/{repo}/health-check-url`, body `{"health_check_base_url": str | None, "health_check_latency_threshold_ms": int | None}`.

- [ ] **Step 1: Write failing test**

Append to `github-app/tests/test_admin.py` (reuses the existing `_logged_in_client` helper already in that file):

```python
@pytest.mark.asyncio
async def test_set_health_check_config(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, installation_id=100)
    async with client:
        response = await client.put(
            "/admin/octocat/hello-world/health-check-url",
            json={"health_check_base_url": "https://api.example.com", "health_check_latency_threshold_ms": 3000},
        )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_set_health_check_config_requires_login(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/admin/octocat/hello-world/health-check-url",
            json={"health_check_base_url": "https://api.example.com", "health_check_latency_threshold_ms": None},
        )
    assert response.status_code == 401
```

- [ ] **Step 2: Run to verify failure**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_admin.py -k health_check_config -v`
Expected: FAIL (404 — route doesn't exist)

- [ ] **Step 3: Implement**

Append to `github-app/app_server/admin.py` (add `set_health_check_config` to the existing `from app_server.db import (...)` block):

```python
@admin_router.put("/admin/{org}/{repo}/health-check-url")
async def set_health_check_config_route(org: str, repo: str, request: Request):
    installation = await _require_admin_installation(request, org, repo)
    body = await request.json()
    await set_health_check_config(
        request.app.state.db_pool,
        installation["installation_id"],
        body.get("health_check_base_url"),
        body.get("health_check_latency_threshold_ms"),
    )
    return {"ok": True}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_admin.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/app_server/admin.py github-app/tests/test_admin.py
git commit -m "feat(github-app): admin route for health-check base URL + latency threshold"
```

---

## Task 3: Slack formatters for health alerts

**Files:**
- Modify: `github-app/scan_worker/slack.py`
- Test: `github-app/tests/test_slack.py`

**Interfaces:**
- Produces: `format_reachability_alert(repo_full_name, method, path, now_reachable: bool) -> dict`, `format_latency_alert(repo_full_name, method, path, latency_ms, threshold_ms, now_over: bool) -> dict`, `send_health_alert(webhook_url, message: dict, http_client=None) -> None`. Consumed by Task 4.

- [ ] **Step 1: Write failing tests**

Append to `github-app/tests/test_slack.py`:

```python
from scan_worker.slack import format_latency_alert, format_reachability_alert, send_health_alert


def test_format_reachability_alert_down():
    body = format_reachability_alert("octocat/hello-world", "GET", "/api/users", now_reachable=False)
    assert "down" in body["text"]
    assert "octocat/hello-world" in body["text"]
    assert "/api/users" in body["text"]


def test_format_reachability_alert_recovered():
    body = format_reachability_alert("octocat/hello-world", "GET", "/api/users", now_reachable=True)
    assert "recovered" in body["text"]


def test_format_latency_alert_over():
    body = format_latency_alert("octocat/hello-world", "GET", "/api/users", 4120.0, 3000, now_over=True)
    assert "slow" in body["text"]
    assert "4120" in body["text"]
    assert "3000" in body["text"]


def test_format_latency_alert_under():
    body = format_latency_alert("octocat/hello-world", "GET", "/api/users", 850.0, 3000, now_over=False)
    assert "under threshold" in body["text"]


def test_send_health_alert_posts_message():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    send_health_alert("https://hooks.slack.com/x", {"text": "test"}, http_client=client)
    assert len(calls) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `cd github-app && python -m pytest tests/test_slack.py -k "reachability_alert or latency_alert or send_health" -v`
Expected: FAIL (`ImportError`)

- [ ] **Step 3: Implement**

Append to `github-app/scan_worker/slack.py`:

```python
def format_reachability_alert(repo_full_name: str, method: str, path: str, now_reachable: bool) -> dict:
    if now_reachable:
        text = (
            f"*Aletheore*: endpoint recovered on `{repo_full_name}`\n"
            f"`{method} {path}` is reachable again"
        )
    else:
        text = (
            f"*Aletheore*: endpoint down on `{repo_full_name}`\n"
            f"`{method} {path}` is unreachable (was reachable as of the last check)"
        )
    return {"text": text}


def format_latency_alert(
    repo_full_name: str, method: str, path: str, latency_ms: float, threshold_ms: int, now_over: bool
) -> dict:
    if now_over:
        text = (
            f"*Aletheore*: endpoint slow on `{repo_full_name}`\n"
            f"`{method} {path}` took {latency_ms:.0f}ms (threshold: {threshold_ms}ms)"
        )
    else:
        text = (
            f"*Aletheore*: endpoint back under threshold on `{repo_full_name}`\n"
            f"`{method} {path}` took {latency_ms:.0f}ms (threshold: {threshold_ms}ms)"
        )
    return {"text": text}


def send_health_alert(webhook_url: str, message: dict, http_client: httpx.Client | None = None) -> None:
    client = http_client or httpx.Client()
    response = client.post(webhook_url, json=message)
    response.raise_for_status()
```

- [ ] **Step 4: Run to verify pass**

Run: `cd github-app && python -m pytest tests/test_slack.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/scan_worker/slack.py github-app/tests/test_slack.py
git commit -m "feat(github-app): Slack formatters for reachability + latency health alerts"
```

---

## Task 4: The sweep job

**Files:**
- Modify: `github-app/scan_worker/jobs.py`
- Test: `github-app/tests/test_jobs.py`

**Interfaces:**
- Consumes: `list_monitored_installations`, `list_repos_for_installation`, `get_latest_evidence`, `get_last_endpoint_health`, `insert_endpoint_health` (Task 1); `format_reachability_alert`, `format_latency_alert`, `send_health_alert` (Task 3); `run_healthcheck` (`aletheore.healthcheck`, already imported project-wide).
- Produces: `run_health_check_sweep_job() -> None` — the RQ-enqueued entrypoint.

- [ ] **Step 1: Write failing tests**

Append to `github-app/tests/test_jobs.py`:

```python
def test_sweep_sends_reachability_down_alert(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.list_monitored_installations",
        lambda dsn: [{"installation_id": 1, "health_check_base_url": "https://api.example.com",
                      "health_check_latency_threshold_ms": None}],
    )
    monkeypatch.setattr("scan_worker.jobs.list_repos_for_installation", lambda dsn, iid: ["octocat/hello-world"])
    monkeypatch.setattr(
        "scan_worker.jobs.get_latest_evidence",
        lambda dsn, iid, repo: {"repository": {"api_endpoints": {"endpoints": [{"method": "GET", "path": "/x"}]}}},
    )
    monkeypatch.setattr(
        "scan_worker.jobs.run_healthcheck",
        lambda endpoints, base_url: {
            "results": [{"method": "GET", "path": "/x", "reachable": False, "status_code": None, "latency_ms": 10.0}]
        },
    )
    monkeypatch.setattr(
        "scan_worker.jobs.get_last_endpoint_health",
        lambda dsn, iid, repo, method, path: {"reachable": True, "latency_ms": 100.0},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_endpoint_health", lambda *a, **k: None)

    sent = []
    monkeypatch.setattr("scan_worker.jobs.send_health_alert", lambda url, msg, **k: sent.append(msg))

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    assert len(sent) == 1
    assert "down" in sent[0]["text"]


def test_sweep_sends_nothing_when_reachable_stays_same(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.list_monitored_installations",
        lambda dsn: [{"installation_id": 1, "health_check_base_url": "https://api.example.com",
                      "health_check_latency_threshold_ms": None}],
    )
    monkeypatch.setattr("scan_worker.jobs.list_repos_for_installation", lambda dsn, iid: ["octocat/hello-world"])
    monkeypatch.setattr(
        "scan_worker.jobs.get_latest_evidence",
        lambda dsn, iid, repo: {"repository": {"api_endpoints": {"endpoints": [{"method": "GET", "path": "/x"}]}}},
    )
    monkeypatch.setattr(
        "scan_worker.jobs.run_healthcheck",
        lambda endpoints, base_url: {
            "results": [{"method": "GET", "path": "/x", "reachable": True, "status_code": 200, "latency_ms": 90.0}]
        },
    )
    monkeypatch.setattr(
        "scan_worker.jobs.get_last_endpoint_health",
        lambda dsn, iid, repo, method, path: {"reachable": True, "latency_ms": 95.0},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_endpoint_health", lambda *a, **k: None)

    sent = []
    monkeypatch.setattr("scan_worker.jobs.send_health_alert", lambda url, msg, **k: sent.append(msg))

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    assert sent == []


def test_sweep_sends_latency_over_alert(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.list_monitored_installations",
        lambda dsn: [{"installation_id": 1, "health_check_base_url": "https://api.example.com",
                      "health_check_latency_threshold_ms": 3000}],
    )
    monkeypatch.setattr("scan_worker.jobs.list_repos_for_installation", lambda dsn, iid: ["octocat/hello-world"])
    monkeypatch.setattr(
        "scan_worker.jobs.get_latest_evidence",
        lambda dsn, iid, repo: {"repository": {"api_endpoints": {"endpoints": [{"method": "GET", "path": "/x"}]}}},
    )
    monkeypatch.setattr(
        "scan_worker.jobs.run_healthcheck",
        lambda endpoints, base_url: {
            "results": [{"method": "GET", "path": "/x", "reachable": True, "status_code": 200, "latency_ms": 4200.0}]
        },
    )
    monkeypatch.setattr(
        "scan_worker.jobs.get_last_endpoint_health",
        lambda dsn, iid, repo, method, path: {"reachable": True, "latency_ms": 1000.0},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_endpoint_health", lambda *a, **k: None)

    sent = []
    monkeypatch.setattr("scan_worker.jobs.send_health_alert", lambda url, msg, **k: sent.append(msg))

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    assert len(sent) == 1
    assert "slow" in sent[0]["text"]


def test_sweep_skips_latency_when_unreachable(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr(
        "scan_worker.jobs.list_monitored_installations",
        lambda dsn: [{"installation_id": 1, "health_check_base_url": "https://api.example.com",
                      "health_check_latency_threshold_ms": 3000}],
    )
    monkeypatch.setattr("scan_worker.jobs.list_repos_for_installation", lambda dsn, iid: ["octocat/hello-world"])
    monkeypatch.setattr(
        "scan_worker.jobs.get_latest_evidence",
        lambda dsn, iid, repo: {"repository": {"api_endpoints": {"endpoints": [{"method": "GET", "path": "/x"}]}}},
    )
    monkeypatch.setattr(
        "scan_worker.jobs.run_healthcheck",
        lambda endpoints, base_url: {
            "results": [{"method": "GET", "path": "/x", "reachable": False, "status_code": None, "latency_ms": 5000.0}]
        },
    )
    monkeypatch.setattr(
        "scan_worker.jobs.get_last_endpoint_health",
        lambda dsn, iid, repo, method, path: {"reachable": False, "latency_ms": 5000.0},
    )
    monkeypatch.setattr("scan_worker.jobs.insert_endpoint_health", lambda *a, **k: None)

    sent = []
    monkeypatch.setattr("scan_worker.jobs.send_health_alert", lambda url, msg, **k: sent.append(msg))

    from scan_worker.jobs import run_health_check_sweep_job

    run_health_check_sweep_job()

    # reachable stayed False (no reachability flip) and latency is never
    # evaluated while unreachable, so nothing should fire.
    assert sent == []
```

- [ ] **Step 2: Run to verify failure**

Run: `cd github-app && python -m pytest tests/test_jobs.py -k sweep -v`
Expected: FAIL (`ImportError: cannot import name 'run_health_check_sweep_job'`)

- [ ] **Step 3: Implement**

Modify `github-app/scan_worker/jobs.py` — add imports:

```python
from aletheore.healthcheck import run_healthcheck
from scan_worker.db import (
    get_installation as get_installation_row,
    get_last_endpoint_health,
    get_latest_evidence,
    insert_endpoint_health,
    insert_repo_history,
    list_monitored_installations,
    list_repos_for_installation,
)
from scan_worker.slack import format_latency_alert, format_reachability_alert, send_health_alert
```

(This replaces the existing narrower `from scan_worker.db import get_installation as get_installation_row, insert_repo_history` import line with the fuller set above — keep the `get_installation_row` alias, it's already used by `_maybe_send_slack_alert`/`_maybe_create_check_run`.)

Append:

```python
def _send_if_webhook_configured(installation: dict, message: dict) -> None:
    webhook_url = installation.get("webhook_url")
    if webhook_url:
        send_health_alert(webhook_url, message)


def run_health_check_sweep_job() -> None:
    settings = get_settings()
    dsn = settings.database_url

    for installation in list_monitored_installations(dsn):
        installation_id = installation["installation_id"]
        base_url = installation["health_check_base_url"]
        threshold_ms = installation["health_check_latency_threshold_ms"]

        for repo_full_name in list_repos_for_installation(dsn, installation_id):
            evidence = get_latest_evidence(dsn, installation_id, repo_full_name)
            if evidence is None:
                continue
            endpoints = evidence.get("repository", {}).get("api_endpoints", {}).get("endpoints", [])
            if not endpoints:
                continue

            result = run_healthcheck(endpoints, base_url)
            for entry in result["results"]:
                if entry.get("skipped"):
                    continue
                method = entry["method"]
                path = entry["path"]
                reachable = entry["reachable"]
                status_code = entry.get("status_code")
                latency_ms = entry.get("latency_ms")

                prior = get_last_endpoint_health(dsn, installation_id, repo_full_name, method, path)

                reachability_flipped = (prior is None and not reachable) or (
                    prior is not None and prior["reachable"] != reachable
                )
                if reachability_flipped:
                    _send_if_webhook_configured(
                        installation, format_reachability_alert(repo_full_name, method, path, reachable)
                    )

                if threshold_ms is not None and reachable:
                    now_over = latency_ms is not None and latency_ms > threshold_ms
                    prior_had_latency = prior is not None and prior["reachable"] and prior.get("latency_ms") is not None
                    prior_over = prior_had_latency and prior["latency_ms"] > threshold_ms
                    latency_flipped = (not prior_had_latency and now_over) or (prior_had_latency and prior_over != now_over)
                    if latency_flipped:
                        _send_if_webhook_configured(
                            installation,
                            format_latency_alert(repo_full_name, method, path, latency_ms, threshold_ms, now_over),
                        )

                insert_endpoint_health(
                    dsn, installation_id, repo_full_name, method, path, reachable, status_code, latency_ms
                )
```

Note: `installation.get("webhook_url")` requires `list_monitored_installations`'s SELECT to include `webhook_url` alongside the health-check columns - go back to Task 1's `list_monitored_installations` implementation and add `webhook_url` to its SELECT list before this task's tests will reflect real behavior (the mocked tests above already assume `installation.get("webhook_url")` works, since they pass fixed dicts, but the real query needs the column too).

- [ ] **Step 4: Fix Task 1's `list_monitored_installations` to include `webhook_url`**

Modify `github-app/scan_worker/db.py`'s `list_monitored_installations`:

```python
def list_monitored_installations(dsn: str) -> list[dict]:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT installation_id, health_check_base_url, health_check_latency_threshold_ms, webhook_url
                FROM installations
                WHERE plan != 'free' AND health_check_base_url IS NOT NULL
                """
            )
            columns = [d[0] for d in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
```

Add one more test to `github-app/tests/test_scan_worker_db.py` confirming this:

```python
def test_list_monitored_installations_includes_webhook_url(dsn):
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO installations (installation_id, account_login, plan, health_check_base_url, webhook_url) "
                "VALUES (304, 'd', 'pro', 'https://d.example.com', 'https://hooks.slack.com/d')"
            )
        conn.commit()

    result = list_monitored_installations(dsn)
    row = next(r for r in result if r["installation_id"] == 304)
    assert row["webhook_url"] == "https://hooks.slack.com/d"
```

- [ ] **Step 5: Run to verify pass**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_jobs.py tests/test_scan_worker_db.py -v`
Expected: PASS (all tests)

- [ ] **Step 6: Run the full suite**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest -q`
Expected: PASS (all tests, no regressions)

- [ ] **Step 7: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/scan_worker/jobs.py github-app/scan_worker/db.py github-app/tests/test_jobs.py \
        github-app/tests/test_scan_worker_db.py
git commit -m "feat(github-app): health-check sweep job - reachability + latency flip detection"
```

---

## Task 5: Ofelia scheduler

**Files:**
- Modify: `github-app/docker-compose.yml`

- [ ] **Step 1: Add the `ofelia` service and `scan-worker` labels**

Add to the `scan-worker` service block (alongside its existing config):

```yaml
    labels:
      ofelia.enabled: "true"
      ofelia.job-exec.health-sweep.schedule: "@every 3m"
      ofelia.job-exec.health-sweep.command: >
        python -c "from redis import Redis; from rq import Queue;
        Queue('scans', connection=Redis.from_url('redis://redis:6379/0')).enqueue('scan_worker.jobs.run_health_check_sweep_job')"
```

Add a new top-level service:

```yaml
  ofelia:
    image: mcuadros/ofelia:latest
    restart: unless-stopped
    depends_on:
      - scan-worker
    command: daemon --docker
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
```

- [ ] **Step 2: Validate**

Run: `cd github-app && POSTGRES_PASSWORD=test docker compose config --quiet`
Expected: no output, exit 0

- [ ] **Step 3: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/docker-compose.yml
git commit -m "feat(github-app): Ofelia scheduler - health-check sweep every 3 minutes"
```

---

## Task 6: Deploy + live verification

- [ ] **Step 1: Pull and apply migration on the live server**

```bash
ssh root@187.127.169.89 "cd /root/aletheore && git pull --ff-only origin master"
ssh root@187.127.169.89 "cd /root/aletheore/github-app && docker compose exec -T postgres psql -U aletheore -d aletheore_app < migrations/003_health_monitoring.sql"
```

- [ ] **Step 2: Rebuild and restart**

```bash
ssh root@187.127.169.89 "cd /root/aletheore/github-app && docker compose up -d --build"
```

Confirm `docker compose ps` shows all services `Up` including the new `ofelia` container, and `RestartCount: 0` across the board (same bar every prior deploy this session was held to).

- [ ] **Step 3: Real end-to-end verification (Success Criteria 1-5 from the spec)**

1. Set a real `health_check_base_url` for a real paid, monitored installation via the admin route.
2. Take a real endpoint down; confirm a real "endpoint down" Slack message arrives within one 3-minute sweep.
3. Bring it back up; confirm a real "endpoint recovered" message.
4. Set a real `health_check_latency_threshold_ms`; make a real endpoint respond slower than it; confirm a real "slow" message, then confirm "back under threshold" once it speeds back up.
5. Confirm an installation with no `health_check_base_url` set produces no outbound health-check requests in `docker compose logs scan-worker` during a live sweep.
