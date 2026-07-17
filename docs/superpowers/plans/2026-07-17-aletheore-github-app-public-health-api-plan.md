# Public Health Metrics API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `GET /v1/health/{org}/{repo}` — a public, unauthenticated, CORS-open endpoint returning the latest `endpoint_health` row per endpoint for a repo, so customers can build their own status page against it.

**Architecture:** One new route function in `github-app/app_server/dashboard.py` (already the home of the other public read-only endpoint, `GET /app/{org}/{repo}`). No new storage, no new job — reads the `endpoint_health` table from migration `003_health_monitoring.sql`, already live in the schema.

## Global Constraints

- No plan/entitlement check in this endpoint's code — `endpoint_health` rows only exist for paid installations with monitoring configured, so gating is automatic via data existence.
- `Access-Control-Allow-Origin: *` on every response from this route.
- 404 when a repo has no `endpoint_health` rows, matching `GET /app/{org}/{repo}`'s existing convention.

---

## Task 1: `GET /v1/health/{org}/{repo}`

**Files:**
- Modify: `github-app/app_server/dashboard.py`
- Test: `github-app/tests/test_dashboard.py`

**Interfaces:**
- Produces: route `GET /v1/health/{org}/{repo}` on `dashboard_router` (already mounted in `main.py` via `app.include_router(dashboard_router)` — no `main.py` change needed).

- [ ] **Step 1: Write the failing tests**

Append to `github-app/tests/test_dashboard.py` (this file already has `pool`/`ASGITransport` fixtures and imports in use — follow its existing style):

```python
@pytest.mark.asyncio
async def test_public_health_returns_latest_per_endpoint(pool):
    await upsert_installation(pool, 500, "octocat")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO endpoint_health
                (installation_id, repo_full_name, endpoint_method, endpoint_path,
                 reachable, status_code, latency_ms, checked_at)
            VALUES
                (500, 'octocat/hello-world', 'GET', '/api/users', true, 200, 90.5, now() - interval '1 minute'),
                (500, 'octocat/hello-world', 'GET', '/api/users', true, 200, 88.0, now()),
                (500, 'octocat/hello-world', 'GET', '/api/orders', false, NULL, 5000.0, now())
            """
        )

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/health/octocat/hello-world")

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    body = response.json()
    assert body["repo_full_name"] == "octocat/hello-world"
    endpoints = {(e["method"], e["path"]): e for e in body["endpoints"]}
    assert len(endpoints) == 2
    assert endpoints[("GET", "/api/users")]["latency_ms"] == 88.0  # latest row, not the older one
    assert endpoints[("GET", "/api/orders")]["reachable"] is False
    assert endpoints[("GET", "/api/orders")]["status_code"] is None


@pytest.mark.asyncio
async def test_public_health_404s_with_no_data(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/health/octocat/no-such-repo")
    assert response.status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_dashboard.py -k public_health -v`
Expected: FAIL with 404 (route doesn't exist) on both tests

- [ ] **Step 3: Implement**

Modify `github-app/app_server/dashboard.py` — change the import line to add `Response`:

```python
from fastapi import APIRouter, HTTPException, Request, Response
```

Append:

```python
@dashboard_router.get("/v1/health/{org}/{repo}")
async def get_public_health(org: str, repo: str, request: Request, response: Response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    repo_full_name = f"{org}/{repo}"
    pool = request.app.state.db_pool
    rows = await pool.fetch(
        """
        SELECT DISTINCT ON (endpoint_method, endpoint_path)
            endpoint_method, endpoint_path, reachable, status_code, latency_ms, checked_at
        FROM endpoint_health
        WHERE repo_full_name = $1
        ORDER BY endpoint_method, endpoint_path, checked_at DESC
        """,
        repo_full_name,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="no health data for this repo")

    return {
        "repo_full_name": repo_full_name,
        "endpoints": [
            {
                "method": r["endpoint_method"],
                "path": r["endpoint_path"],
                "reachable": r["reachable"],
                "status_code": r["status_code"],
                "latency_ms": float(r["latency_ms"]) if r["latency_ms"] is not None else None,
                "checked_at": r["checked_at"].isoformat(),
            }
            for r in rows
        ],
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_dashboard.py -v`
Expected: PASS (all tests, including the pre-existing dashboard tests)

- [ ] **Step 5: Run the full suite**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest -q`
Expected: PASS (all tests, no regressions)

- [ ] **Step 6: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/app_server/dashboard.py github-app/tests/test_dashboard.py
git commit -m "feat(github-app): public CORS-open health-metrics API for customer status pages"
```
