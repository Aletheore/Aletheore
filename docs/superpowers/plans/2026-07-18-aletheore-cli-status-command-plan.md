# Aletheore CLI `status` Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `aletheore status` - reports installed version, PyPI update availability, and login state (with a live-verified org name) in one command.

**Architecture:** One new bearer-token-hash-authenticated backend route (`GET /v1/whoami`) reusing the existing `get_installation_by_token_hash`; two small private, dependency-injected helper functions in `cli.py` (`_check_for_update`, `_fetch_whoami`) matching the existing `_audit`/`_mcp`/`_dashboard` module-level-helper convention; a thin `status` command wiring them together.

**Tech Stack:** FastAPI + asyncpg (backend, matching `managed_audit_api.py`'s existing style), httpx + typer + rich (CLI), pytest + pytest-asyncio + httpx.MockTransport (tests, matching both existing test suites exactly).

## Global Constraints

- `GET /v1/whoami` authenticates via raw bearer-token-hash lookup only (`get_installation_by_token_hash`) - no session cookie, no GitHub API call, matching `start_managed_audit`'s existing auth style exactly (not the bearer-GitHub-token style from the device-flow work).
- `_check_for_update` and `_fetch_whoami` must accept an injectable `http_client: httpx.Client | None = None` parameter (mirroring `device_auth.py`'s and `managed_audit_client.py`'s established DI pattern) so tests never hit a real network.
- Network failures in either check degrade gracefully (a printed message), never crash the whole command or raise past `status()`.
- Login state is resolved exactly the way `_managed_audit` already resolves a token: `ALETHEORE_API_TOKEN` env var first, then the saved credentials file (`has_api_key`/`get_api_key`, provider name `aletheore-managed-audit`) - no new storage mechanism.
- Not fixing the stale `__version__` string in `aletheore/__init__.py` - out of scope, `importlib.metadata.version("aletheore")` is used instead and is unaffected by that drift.

---

## Task 1: Backend `GET /v1/whoami`

**Files:**
- Modify: `github-app/app_server/managed_audit_api.py`
- Test: `github-app/tests/test_managed_audit_api.py`

**Interfaces:**
- Consumes: existing `get_installation_by_token_hash(pool, token_hash) -> dict | None` from `app_server/db.py` (already imported in this file).
- Produces: route `GET /v1/whoami` on `managed_audit_router` (already mounted in `main.py` - no `main.py` change needed).

- [ ] **Step 1: Write the failing tests**

Append to `github-app/tests/test_managed_audit_api.py`:

```python
@pytest.mark.asyncio
async def test_whoami_requires_bearer_token(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/whoami")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_whoami_rejects_unknown_token(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/whoami", headers={"Authorization": "Bearer no-such-token"}
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_whoami_returns_account_login_and_plan_for_valid_token(pool):
    await upsert_installation(pool, 100, "acme")
    await set_installation_plan(pool, 100, "pro")
    token_hash = hashlib.sha256(b"real-token").hexdigest()
    await create_api_token(pool, 100, token_hash, "laptop", "acme")

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/whoami", headers={"Authorization": "Bearer real-token"}
        )

    assert response.status_code == 200
    assert response.json() == {"account_login": "acme", "plan": "pro"}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_managed_audit_api.py -k whoami -v`
Expected: FAIL with 404 (route doesn't exist) on all three tests

- [ ] **Step 3: Implement**

Append to `github-app/app_server/managed_audit_api.py` (`hashlib` and `get_installation_by_token_hash` are already imported in this file):

```python
@managed_audit_router.get("/v1/whoami")
async def whoami(request: Request):
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")

    raw_token = auth_header.removeprefix("Bearer ")
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    installation = await get_installation_by_token_hash(request.app.state.db_pool, token_hash)
    if installation is None:
        raise HTTPException(status_code=401, detail="invalid or revoked token")
    return {"account_login": installation["account_login"], "plan": installation["plan"]}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_managed_audit_api.py -v`
Expected: PASS (all tests, including every pre-existing test in this file)

- [ ] **Step 5: Run the full backend suite**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest -q`
Expected: PASS, no regressions

- [ ] **Step 6: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/app_server/managed_audit_api.py github-app/tests/test_managed_audit_api.py
git commit -m "feat(github-app): add GET /v1/whoami for CLI status command"
```

---

## Task 2: `aletheore status` command

**Files:**
- Modify: `prototype/aletheore/cli.py`
- Test: `prototype/tests/test_cli.py`

**Interfaces:**
- Consumes: `GET /v1/whoami` (Task 1) as a plain JSON HTTP contract; existing `has_api_key`, `get_api_key` from `aletheore/credentials.py`.
- Produces: `_check_for_update(installed_version: str, http_client: httpx.Client | None = None) -> str`, `_fetch_whoami(token: str, api_base_url: str = "https://app.aletheore.com", http_client: httpx.Client | None = None) -> dict | None`, `aletheore status` command.

- [ ] **Step 1: Write the failing tests**

Add `import httpx` to the top of `prototype/tests/test_cli.py` (`Path` and `json` are already imported there; `httpx` is not). Then append to the same file (the tests patch `aletheore.cli._check_for_update`/`_fetch_whoami` directly, matching the existing `patch("aletheore.cli._audit", ...)` style already used in this file):

```python
def test_check_for_update_reports_up_to_date():
    from aletheore.cli import _check_for_update

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"info": {"version": "0.3.0"}})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://pypi.org")
    assert _check_for_update("0.3.0", http_client=client) == "up to date"


def test_check_for_update_reports_available_update():
    from aletheore.cli import _check_for_update

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"info": {"version": "0.4.0"}})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://pypi.org")
    assert _check_for_update("0.3.0", http_client=client) == "update available: 0.4.0"


def test_check_for_update_degrades_gracefully_on_network_error():
    from aletheore.cli import _check_for_update

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://pypi.org")
    assert _check_for_update("0.3.0", http_client=client) == "couldn't check for updates"


def test_fetch_whoami_returns_account_info():
    from aletheore.cli import _fetch_whoami

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer real-token"
        return httpx.Response(200, json={"account_login": "acme", "plan": "pro"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://app.aletheore.com")
    assert _fetch_whoami("real-token", http_client=client) == {"account_login": "acme", "plan": "pro"}


def test_fetch_whoami_returns_none_on_invalid_token():
    from aletheore.cli import _fetch_whoami

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "invalid or revoked token"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://app.aletheore.com")
    assert _fetch_whoami("bad-token", http_client=client) is None


def test_status_reports_not_logged_in(monkeypatch):
    monkeypatch.delenv("ALETHEORE_API_TOKEN", raising=False)
    monkeypatch.setattr("aletheore.credentials.DEFAULT_CREDENTIALS_PATH", Path("/nonexistent/creds.json"))

    with patch("aletheore.cli._check_for_update", return_value="up to date"), \
         patch("aletheore.cli._fetch_whoami") as mock_whoami:
        result = runner.invoke(app, ["status"])

    mock_whoami.assert_not_called()

    assert result.exit_code == 0
    assert "Not logged in" in result.output
    assert "aletheore login" in result.output


def test_status_reports_logged_in_org_when_token_saved(tmp_path, monkeypatch):
    creds_path = tmp_path / "creds.json"
    creds_path.write_text(json.dumps({"aletheore-managed-audit": "real-token"}))
    monkeypatch.setattr("aletheore.credentials.DEFAULT_CREDENTIALS_PATH", creds_path)
    monkeypatch.delenv("ALETHEORE_API_TOKEN", raising=False)

    with patch("aletheore.cli._check_for_update", return_value="up to date"), \
         patch("aletheore.cli._fetch_whoami", return_value={"account_login": "acme", "plan": "pro"}) as mock_whoami:
        result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "acme" in result.output
    assert "pro" in result.output
    mock_whoami.assert_called_once_with("real-token")


def test_status_reports_unverifiable_token(tmp_path, monkeypatch):
    creds_path = tmp_path / "creds.json"
    creds_path.write_text(json.dumps({"aletheore-managed-audit": "stale-token"}))
    monkeypatch.setattr("aletheore.credentials.DEFAULT_CREDENTIALS_PATH", creds_path)
    monkeypatch.delenv("ALETHEORE_API_TOKEN", raising=False)

    with patch("aletheore.cli._check_for_update", return_value="up to date"), \
         patch("aletheore.cli._fetch_whoami", return_value=None):
        result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "couldn't be verified" in result.output
```

- [ ] **Step 2: Run to verify failure**

Run: `cd prototype && python -m pytest tests/test_cli.py -k "check_for_update or fetch_whoami or test_status" -v`
Expected: FAIL - `ImportError: cannot import name '_check_for_update'` (and similarly for `_fetch_whoami`), and `Error: No such command 'status'`

- [ ] **Step 3: Implement**

Add `import httpx` to the top of `prototype/aletheore/cli.py`, alongside the existing `import typer` / `import uvicorn` block.

Add these two private functions near the other private command helpers (e.g. after `_dashboard`, before the `healthcheck` command):

```python
def _check_for_update(installed_version: str, http_client: httpx.Client | None = None) -> str:
    client = http_client or httpx.Client(base_url="https://pypi.org")
    try:
        response = client.get("/pypi/aletheore/json", timeout=5.0)
        response.raise_for_status()
        latest_version = response.json()["info"]["version"]
    except httpx.HTTPError:
        return "couldn't check for updates"
    if latest_version == installed_version:
        return "up to date"
    return f"update available: {latest_version}"


def _fetch_whoami(
    token: str,
    api_base_url: str = "https://app.aletheore.com",
    http_client: httpx.Client | None = None,
) -> dict | None:
    client = http_client or httpx.Client(base_url=api_base_url)
    try:
        response = client.get(
            "/v1/whoami", headers={"Authorization": f"Bearer {token}"}, timeout=5.0
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    return response.json()
```

Add the command itself near the other `@app.command` definitions (e.g. after `login`):

```python
@app.command(help="show installed version, update availability, and login state")
def status() -> None:
    import importlib.metadata

    from aletheore.credentials import get_api_key, has_api_key

    installed_version = importlib.metadata.version("aletheore")
    version_note = _check_for_update(installed_version)
    console.print(f"Aletheore v{installed_version} ({version_note})")

    if not has_api_key("ALETHEORE_API_TOKEN", "aletheore-managed-audit"):
        console.print("Not logged in - run [bold]aletheore login[/bold]")
        return

    token = get_api_key("ALETHEORE_API_TOKEN", "aletheore-managed-audit", prompt_fn=lambda _msg: "")
    who = _fetch_whoami(token)
    if who is None:
        console.print("A token is saved locally, but it couldn't be verified right now.")
    else:
        console.print(f"Logged in as: [bold]{who['account_login']}[/bold] ({who['plan']} plan)")
```

Also add `("status", "installed version, update availability, and login state")` to the `_COMMAND_SUMMARIES` list near the top of the file, matching the existing entry style for every other command (e.g. the `("login", ...)` entry added in the previous change).

- [ ] **Step 4: Run to verify pass**

Run: `cd prototype && python -m pytest tests/test_cli.py -v`
Expected: PASS (all tests, including every pre-existing test in this file)

- [ ] **Step 5: Run the full prototype suite**

Run: `cd prototype && python -m pytest -q`
Expected: PASS, no regressions

- [ ] **Step 6: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add prototype/aletheore/cli.py prototype/tests/test_cli.py
git commit -m "feat(cli): add \`aletheore status\` command"
```
