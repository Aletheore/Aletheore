# Aletheore CLI Device Flow (`aletheore login`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `aletheore login` - GitHub OAuth device-flow authentication from the terminal that mints and saves a personal API token, replacing the current copy-paste-from-dashboard workflow.

**Architecture:** Two new bearer-token-authenticated backend routes (`GET /v1/my-installations`, `POST /v1/cli-tokens`) reusing the existing seat-cap/admin-check logic in `github-app/app_server/admin.py`; a new `prototype/aletheore/device_auth.py` module owning the device-flow HTTP mechanics and org/repo resolution as pure, dependency-injected functions; a thin `login` command in `prototype/aletheore/cli.py` that wires them together and saves the result via the existing `credentials.py` storage.

**Tech Stack:** FastAPI + asyncpg (backend, matching `admin.py`'s existing style), httpx + typer + rich (CLI, matching `cli.py`'s existing style), pytest + pytest-asyncio + httpx.MockTransport (tests, matching both existing test suites' style exactly).

## Global Constraints

- No session cookie on the two new backend routes - `Authorization: Bearer <github_token>` only.
- Reuse `create_api_token`, `get_max_tokens`, `count_active_tokens`, `list_api_tokens`, `get_installation` from `app_server/db.py` unchanged - no new DB functions, no schema changes.
- The raw GitHub access token obtained via device flow is never written to disk - only the resulting Aletheore token is saved, via the existing `credentials.py` mechanism, provider name `aletheore-managed-audit`.
- All new CLI-side HTTP/subprocess calls must accept an injectable client/run-function parameter (mirroring `managed_audit_client.py`'s `http_client: httpx.Client | None = None` pattern) so tests never hit a real network or a real git process.
- `GITHUB_CLIENT_ID = "Iv23liGMhaWSkY927jgI"` (the real, already-public Aletheore App client ID - not a secret, safe to hardcode in open-source CLI source).
- Manual prerequisite (not a code task): "Device Flow" must be toggled on for the App at `github.com/settings/apps/aletheore` before this feature works end-to-end against real GitHub. Flag this to the user after the plan is executed - it's outside this repo's control.

---

## Task 1: Backend helper refactor + `GET /v1/my-installations`

**Files:**
- Modify: `github-app/app_server/admin.py`
- Test: `github-app/tests/test_admin.py`

**Interfaces:**
- Produces: `_administered_installation_ids(github_token: str) -> set[int]` (async), `_bearer_github_token(request: Request) -> str`, route `GET /v1/my-installations` on `admin_router` (already mounted in `main.py` - no `main.py` change needed).
- Consumes: existing `_github_http_client()` (unchanged, already in `admin.py`).

- [ ] **Step 1: Write the failing tests**

Append to `github-app/tests/test_admin.py` (add `upsert_installation`, `set_installation_plan` to the existing `from app_server.db import (...)` block if not already there - both already appear in the file's current imports):

```python
async def _mock_github_installations(monkeypatch, installation_ids: list[int]):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "total_count": len(installation_ids),
                "installations": [{"id": i} for i in installation_ids],
            },
        )

    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )


@pytest.mark.asyncio
async def test_my_installations_returns_only_paid_and_administered(pool, monkeypatch):
    await upsert_installation(pool, 100, "acme")
    await set_installation_plan(pool, 100, "pro")
    await upsert_installation(pool, 200, "free-org")
    await set_installation_plan(pool, 200, "free")
    await upsert_installation(pool, 300, "not-mine")
    await set_installation_plan(pool, 300, "pro")

    await _mock_github_installations(monkeypatch, [100, 200])  # note: 300 excluded from GitHub's list too

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/my-installations", headers={"Authorization": "Bearer gho_faketoken"}
        )
    assert response.status_code == 200
    installations = response.json()["installations"]
    assert [i["installation_id"] for i in installations] == [100]
    assert installations[0]["account_login"] == "acme"


@pytest.mark.asyncio
async def test_my_installations_requires_bearer_token(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/my-installations")
    assert response.status_code == 401
```

- [ ] **Step 2: Run to verify failure**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_admin.py -k my_installations -v`
Expected: FAIL with 404 (route doesn't exist) on both tests

- [ ] **Step 3: Implement**

In `github-app/app_server/admin.py`, replace the inline GitHub cross-reference inside `_require_admin_installation` with a shared helper, and add the bearer-token helper and new route. Change:

```python
async def _require_admin_installation(request: Request, org: str, repo: str) -> dict:
    session = await get_current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="login required")

    pool = request.app.state.db_pool
    installation_id = await _repo_installation_id(pool, org, repo)
    response = _github_http_client().get(
        "/user/installations",
        headers={
            "Authorization": f"Bearer {session['github_access_token']}",
            "Accept": "application/vnd.github+json",
        },
    )
    response.raise_for_status()
    administered_ids = {item["id"] for item in response.json().get("installations", [])}
    if installation_id not in administered_ids:
        raise HTTPException(status_code=403, detail="you do not administer this installation")

    installation = await get_installation(pool, installation_id)
    if installation is None:
        raise HTTPException(status_code=404, detail="installation not found")
    if installation["plan"] == "free":
        raise HTTPException(status_code=402, detail="this feature requires a paid plan")
    return installation
```

to:

```python
async def _administered_installation_ids(github_token: str) -> set[int]:
    response = _github_http_client().get(
        "/user/installations",
        headers={
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json",
        },
    )
    response.raise_for_status()
    return {item["id"] for item in response.json().get("installations", [])}


def _bearer_github_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return auth_header.removeprefix("Bearer ")


async def _require_admin_installation(request: Request, org: str, repo: str) -> dict:
    session = await get_current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="login required")

    pool = request.app.state.db_pool
    installation_id = await _repo_installation_id(pool, org, repo)
    administered_ids = await _administered_installation_ids(session["github_access_token"])
    if installation_id not in administered_ids:
        raise HTTPException(status_code=403, detail="you do not administer this installation")

    installation = await get_installation(pool, installation_id)
    if installation is None:
        raise HTTPException(status_code=404, detail="installation not found")
    if installation["plan"] == "free":
        raise HTTPException(status_code=402, detail="this feature requires a paid plan")
    return installation
```

Append the new route at the end of the file:

```python
@admin_router.get("/v1/my-installations")
async def my_installations(request: Request):
    github_token = _bearer_github_token(request)
    administered_ids = await _administered_installation_ids(github_token)
    pool = request.app.state.db_pool
    rows = await pool.fetch(
        """
        SELECT installation_id, account_login
        FROM installations
        WHERE installation_id = ANY($1::bigint[]) AND plan != 'free'
        """,
        list(administered_ids),
    )
    return {"installations": [dict(r) for r in rows]}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_admin.py -v`
Expected: PASS (all tests, including every pre-existing `test_admin.py` test - the refactor of `_require_admin_installation` must not change its behavior)

- [ ] **Step 5: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/app_server/admin.py github-app/tests/test_admin.py
git commit -m "feat(github-app): bearer-token installation listing for CLI device flow"
```

---

## Task 2: `POST /v1/cli-tokens`

**Files:**
- Modify: `github-app/app_server/admin.py`
- Test: `github-app/tests/test_admin.py`

**Interfaces:**
- Consumes: `_bearer_github_token`, `_administered_installation_ids` (Task 1).
- Produces: route `POST /v1/cli-tokens` on `admin_router`.

- [ ] **Step 1: Write the failing tests**

Append to `github-app/tests/test_admin.py`:

```python
@pytest.mark.asyncio
async def test_create_cli_token_mints_token_for_administered_paid_installation(pool, monkeypatch):
    await upsert_installation(pool, 100, "acme")
    await set_installation_plan(pool, 100, "pro")
    await _mock_github_installations(monkeypatch, [100])

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/cli-tokens",
            json={"installation_id": 100, "label": "laptop (device flow)"},
            headers={"Authorization": "Bearer gho_faketoken"},
        )
    assert response.status_code == 200
    assert len(response.json()["token"]) > 20


@pytest.mark.asyncio
async def test_create_cli_token_rejects_unadministered_installation(pool, monkeypatch):
    await upsert_installation(pool, 100, "acme")
    await set_installation_plan(pool, 100, "pro")
    await _mock_github_installations(monkeypatch, [999])  # 100 not in the list

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/cli-tokens",
            json={"installation_id": 100, "label": "x"},
            headers={"Authorization": "Bearer gho_faketoken"},
        )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_create_cli_token_rejects_free_plan(pool, monkeypatch):
    await upsert_installation(pool, 100, "acme")
    await set_installation_plan(pool, 100, "free")
    await _mock_github_installations(monkeypatch, [100])

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/cli-tokens",
            json={"installation_id": 100, "label": "x"},
            headers={"Authorization": "Bearer gho_faketoken"},
        )
    assert response.status_code == 402


@pytest.mark.asyncio
async def test_create_cli_token_enforces_seat_cap(pool, monkeypatch):
    await upsert_installation(pool, 100, "acme")
    await set_installation_plan(pool, 100, "pro")
    await _mock_github_installations(monkeypatch, [100])

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    max_tokens = await get_max_tokens(pool, 100)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for i in range(max_tokens):
            resp = await client.post(
                "/v1/cli-tokens",
                json={"installation_id": 100, "label": f"token-{i}"},
                headers={"Authorization": "Bearer gho_faketoken"},
            )
            assert resp.status_code == 200
        over_limit = await client.post(
            "/v1/cli-tokens",
            json={"installation_id": 100, "label": "one-too-many"},
            headers={"Authorization": "Bearer gho_faketoken"},
        )
    assert over_limit.status_code == 409


@pytest.mark.asyncio
async def test_create_cli_token_requires_bearer_token(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/v1/cli-tokens", json={"installation_id": 100, "label": "x"})
    assert response.status_code == 401
```

Add `get_max_tokens` to the existing `from app_server.db import (...)` block in the test file if not already imported.

- [ ] **Step 2: Run to verify failure**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_admin.py -k create_cli_token -v`
Expected: FAIL with 404 (route doesn't exist) on all five tests

- [ ] **Step 3: Implement**

Append to `github-app/app_server/admin.py` (all imported names - `create_api_token`, `get_max_tokens`, `count_active_tokens`, `list_api_tokens`, `get_installation`, `hashlib`, `secrets` - already exist in this file):

```python
@admin_router.post("/v1/cli-tokens")
async def create_cli_token(request: Request):
    github_token = _bearer_github_token(request)
    body = await request.json()
    installation_id = body["installation_id"]
    label = body["label"]

    administered_ids = await _administered_installation_ids(github_token)
    if installation_id not in administered_ids:
        raise HTTPException(status_code=403, detail="you do not administer this installation")

    pool = request.app.state.db_pool
    installation = await get_installation(pool, installation_id)
    if installation is None:
        raise HTTPException(status_code=404, detail="installation not found")
    if installation["plan"] == "free":
        raise HTTPException(status_code=402, detail="this feature requires a paid plan")

    max_tokens = await get_max_tokens(pool, installation_id)
    if await count_active_tokens(pool, installation_id) >= max_tokens:
        raise HTTPException(status_code=409, detail=f"token limit reached ({max_tokens})")

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    await create_api_token(pool, installation_id, token_hash, label, installation["account_login"])
    token_id = (await list_api_tokens(pool, installation_id))[0]["id"]
    return {"token": raw_token, "id": token_id, "label": label}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_admin.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Run the full backend suite**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest -q`
Expected: PASS, no regressions

- [ ] **Step 6: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add github-app/app_server/admin.py github-app/tests/test_admin.py
git commit -m "feat(github-app): mint personal API tokens via bearer GitHub token for CLI device flow"
```

---

## Task 3: `prototype/aletheore/device_auth.py`

**Files:**
- Create: `prototype/aletheore/device_auth.py`
- Test: `prototype/tests/test_device_auth.py`

**Interfaces:**
- Consumes: `GET /v1/my-installations` and `POST /v1/cli-tokens` (Tasks 1-2) as plain JSON HTTP contracts - no Python import dependency on the backend.
- Produces: `DeviceFlowError` (exception), `DeviceCode` (dataclass: `device_code`, `user_code`, `verification_uri`, `interval`, `expires_in`), `request_device_code`, `poll_for_access_token`, `fetch_my_installations`, `mint_cli_token`, `infer_org_from_cwd_git_remote`, `resolve_installation` - all consumed by Task 4's `login` command.

- [ ] **Step 1: Write the failing tests**

Create `prototype/tests/test_device_auth.py`:

```python
import subprocess

import httpx
import pytest

from aletheore.device_auth import (
    DeviceCode,
    DeviceFlowError,
    fetch_my_installations,
    infer_org_from_cwd_git_remote,
    mint_cli_token,
    poll_for_access_token,
    request_device_code,
    resolve_installation,
)


def test_request_device_code_parses_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "device_code": "dc123",
                "user_code": "ABCD-1234",
                "verification_uri": "https://github.com/login/device",
                "interval": 5,
                "expires_in": 900,
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://github.com")
    code = request_device_code(http_client=client)
    assert code.user_code == "ABCD-1234"
    assert code.interval == 5


def test_poll_for_access_token_succeeds_on_first_try():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "gho_real"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://github.com")
    code = DeviceCode("dc", "ABCD", "https://github.com/login/device", interval=0, expires_in=60)
    token = poll_for_access_token(code, http_client=client, sleep_fn=lambda _s: None)
    assert token == "gho_real"


def test_poll_for_access_token_keeps_polling_on_authorization_pending():
    responses = iter(
        [
            httpx.Response(200, json={"error": "authorization_pending"}),
            httpx.Response(200, json={"access_token": "gho_real"}),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://github.com")
    code = DeviceCode("dc", "ABCD", "https://github.com/login/device", interval=0, expires_in=60)
    token = poll_for_access_token(code, http_client=client, sleep_fn=lambda _s: None)
    assert token == "gho_real"


def test_poll_for_access_token_raises_on_expired_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "expired_token"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://github.com")
    code = DeviceCode("dc", "ABCD", "https://github.com/login/device", interval=0, expires_in=60)
    with pytest.raises(DeviceFlowError, match="expired"):
        poll_for_access_token(code, http_client=client, sleep_fn=lambda _s: None)


def test_poll_for_access_token_raises_on_access_denied():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "access_denied"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://github.com")
    code = DeviceCode("dc", "ABCD", "https://github.com/login/device", interval=0, expires_in=60)
    with pytest.raises(DeviceFlowError, match="denied"):
        poll_for_access_token(code, http_client=client, sleep_fn=lambda _s: None)


def test_poll_for_access_token_times_out():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "authorization_pending"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://github.com")
    code = DeviceCode("dc", "ABCD", "https://github.com/login/device", interval=1, expires_in=1)
    clock_values = iter([0, 0, 2])  # third check exceeds the deadline of 1
    with pytest.raises(DeviceFlowError, match="timed out"):
        poll_for_access_token(
            code,
            http_client=client,
            sleep_fn=lambda _s: None,
            clock=lambda: next(clock_values),
        )


def test_fetch_my_installations_returns_list():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer gho_real"
        return httpx.Response(
            200, json={"installations": [{"installation_id": 100, "account_login": "acme"}]}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://app.aletheore.com")
    result = fetch_my_installations("gho_real", http_client=client)
    assert result == [{"installation_id": 100, "account_login": "acme"}]


def test_mint_cli_token_returns_raw_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"token": "aletheore-tok", "id": 1, "label": "x"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://app.aletheore.com")
    token = mint_cli_token("gho_real", 100, "x", http_client=client)
    assert token == "aletheore-tok"


def test_infer_org_from_cwd_git_remote_ssh_style():
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="git@github.com:acme/widgets.git\n")

    assert infer_org_from_cwd_git_remote(run_fn=fake_run) == "acme"


def test_infer_org_from_cwd_git_remote_https_style():
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="https://github.com/acme/widgets\n")

    assert infer_org_from_cwd_git_remote(run_fn=fake_run) == "acme"


def test_infer_org_from_cwd_git_remote_non_github_remote():
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="https://gitlab.com/acme/widgets\n")

    assert infer_org_from_cwd_git_remote(run_fn=fake_run) is None


def test_infer_org_from_cwd_git_remote_no_git_repo():
    def fake_run(*args, **kwargs):
        raise subprocess.CalledProcessError(128, args)

    assert infer_org_from_cwd_git_remote(run_fn=fake_run) is None


def test_resolve_installation_auto_selects_single_match():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "installations": [
                    {"installation_id": 100, "account_login": "acme"},
                    {"installation_id": 200, "account_login": "other"},
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://app.aletheore.com")

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="git@github.com:acme/widgets.git\n")

    result = resolve_installation("gho_real", http_client=client, run_fn=fake_run)
    assert result == {"installation_id": 100, "account_login": "acme"}


def test_resolve_installation_returns_full_list_when_ambiguous():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "installations": [
                    {"installation_id": 100, "account_login": "acme"},
                    {"installation_id": 200, "account_login": "other"},
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://app.aletheore.com")

    def fake_run(*args, **kwargs):
        raise subprocess.CalledProcessError(128, args)  # not in a git repo

    result = resolve_installation("gho_real", http_client=client, run_fn=fake_run)
    assert result == [
        {"installation_id": 100, "account_login": "acme"},
        {"installation_id": 200, "account_login": "other"},
    ]


def test_resolve_installation_raises_when_no_installations():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"installations": []})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://app.aletheore.com")

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 1)

    with pytest.raises(DeviceFlowError, match="no paid Aletheore installations"):
        resolve_installation("gho_real", http_client=client, run_fn=fake_run)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd prototype && python -m pytest tests/test_device_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aletheore.device_auth'`

- [ ] **Step 3: Implement**

Create `prototype/aletheore/device_auth.py`:

```python
import subprocess
import time
from dataclasses import dataclass
from typing import Callable

import httpx

GITHUB_CLIENT_ID = "Iv23liGMhaWSkY927jgI"


class DeviceFlowError(Exception):
    pass


@dataclass
class DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    interval: int
    expires_in: int


def request_device_code(http_client: httpx.Client | None = None) -> DeviceCode:
    client = http_client or httpx.Client(base_url="https://github.com")
    response = client.post(
        "/login/device/code",
        headers={"Accept": "application/json"},
        data={"client_id": GITHUB_CLIENT_ID},
    )
    response.raise_for_status()
    data = response.json()
    return DeviceCode(
        device_code=data["device_code"],
        user_code=data["user_code"],
        verification_uri=data["verification_uri"],
        interval=data["interval"],
        expires_in=data["expires_in"],
    )


def poll_for_access_token(
    code: DeviceCode,
    http_client: httpx.Client | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    client = http_client or httpx.Client(base_url="https://github.com")
    deadline = clock() + code.expires_in
    interval = code.interval
    while clock() < deadline:
        sleep_fn(interval)
        response = client.post(
            "/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": GITHUB_CLIENT_ID,
                "device_code": code.device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
        response.raise_for_status()
        data = response.json()
        if "access_token" in data:
            return data["access_token"]
        error = data.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval = data.get("interval", interval + 5)
            continue
        if error == "expired_token":
            raise DeviceFlowError(
                "the code expired before you authorized it - run `aletheore login` again"
            )
        if error == "access_denied":
            raise DeviceFlowError("authorization was denied")
        raise DeviceFlowError(f"unexpected device flow error: {error}")
    raise DeviceFlowError("timed out waiting for authorization")


def fetch_my_installations(
    github_token: str,
    api_base_url: str = "https://app.aletheore.com",
    http_client: httpx.Client | None = None,
) -> list[dict]:
    client = http_client or httpx.Client(base_url=api_base_url)
    response = client.get(
        "/v1/my-installations",
        headers={"Authorization": f"Bearer {github_token}"},
    )
    response.raise_for_status()
    return response.json()["installations"]


def mint_cli_token(
    github_token: str,
    installation_id: int,
    label: str,
    api_base_url: str = "https://app.aletheore.com",
    http_client: httpx.Client | None = None,
) -> str:
    client = http_client or httpx.Client(base_url=api_base_url)
    response = client.post(
        "/v1/cli-tokens",
        headers={"Authorization": f"Bearer {github_token}"},
        json={"installation_id": installation_id, "label": label},
    )
    response.raise_for_status()
    return response.json()["token"]


def infer_org_from_cwd_git_remote(
    run_fn: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> str | None:
    try:
        result = run_fn(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None
    url = result.stdout.strip()
    for prefix in ("git@github.com:", "https://github.com/", "http://github.com/"):
        if url.startswith(prefix):
            remainder = url[len(prefix):]
            org = remainder.split("/", 1)[0]
            return org or None
    return None


def resolve_installation(
    github_token: str,
    api_base_url: str = "https://app.aletheore.com",
    http_client: httpx.Client | None = None,
    run_fn: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict | list[dict]:
    installations = fetch_my_installations(github_token, api_base_url, http_client)
    if not installations:
        raise DeviceFlowError(
            "no paid Aletheore installations found for your GitHub account - "
            "install or upgrade the app first at https://github.com/apps/aletheore"
        )

    inferred_org = infer_org_from_cwd_git_remote(run_fn)
    if inferred_org:
        matches = [i for i in installations if i["account_login"] == inferred_org]
        if len(matches) == 1:
            return matches[0]

    return installations
```

- [ ] **Step 4: Run to verify pass**

Run: `cd prototype && python -m pytest tests/test_device_auth.py -v`
Expected: PASS (all 13 tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add prototype/aletheore/device_auth.py prototype/tests/test_device_auth.py
git commit -m "feat(cli): device-flow HTTP mechanics and installation resolution"
```

---

## Task 4: `aletheore login` command

**Files:**
- Modify: `prototype/aletheore/credentials.py`
- Modify: `prototype/aletheore/cli.py`
- Test: `prototype/tests/test_credentials.py`
- Test: `prototype/tests/test_cli.py`

**Interfaces:**
- Consumes: everything from Task 3 (`request_device_code`, `poll_for_access_token`, `resolve_installation`, `mint_cli_token`, `DeviceFlowError`), plus the existing `console` (module-level `Console()` instance already at `cli.py:94`).
- Produces: `save_api_token(provider_name: str, token: str, credentials_path: Path = DEFAULT_CREDENTIALS_PATH) -> None` in `credentials.py`; `aletheore login` CLI command.

- [ ] **Step 1: Write the failing test for the credentials wrapper**

Append to `prototype/tests/test_credentials.py`:

```python
from aletheore.credentials import save_api_token


def test_save_api_token_is_readable_via_get_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("TESTPROVIDER_API_KEY", raising=False)
    creds_path = tmp_path / "creds.json"

    save_api_token("testprovider", "sk-from-device-flow", creds_path)

    def fail_if_called(_msg):
        raise AssertionError("should not prompt when a saved key exists")

    result = get_api_key("TESTPROVIDER_API_KEY", "testprovider", creds_path, fail_if_called)
    assert result == "sk-from-device-flow"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd prototype && python -m pytest tests/test_credentials.py -k save_api_token -v`
Expected: FAIL with `ImportError: cannot import name 'save_api_token'`

- [ ] **Step 3: Add the wrapper**

Append to `prototype/aletheore/credentials.py`:

```python
def save_api_token(
    provider_name: str,
    token: str,
    credentials_path: Path = DEFAULT_CREDENTIALS_PATH,
) -> None:
    _save_key(provider_name, token, credentials_path)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd prototype && python -m pytest tests/test_credentials.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Write the failing CLI tests**

Append to `prototype/tests/test_cli.py` (add `from aletheore.device_auth import DeviceFlowError` to the file's imports):

```python
def test_login_saves_token_when_installation_auto_resolved(tmp_path, monkeypatch):
    creds_path = tmp_path / "creds.json"
    monkeypatch.setattr("aletheore.credentials.DEFAULT_CREDENTIALS_PATH", creds_path)

    with patch("aletheore.device_auth.request_device_code") as mock_request_code, \
         patch("aletheore.device_auth.poll_for_access_token") as mock_poll, \
         patch("aletheore.device_auth.resolve_installation") as mock_resolve, \
         patch("aletheore.device_auth.mint_cli_token") as mock_mint:
        mock_request_code.return_value = MagicMock(
            verification_uri="https://github.com/login/device", user_code="ABCD-1234"
        )
        mock_poll.return_value = "gho_faketoken"
        mock_resolve.return_value = {"installation_id": 100, "account_login": "acme"}
        mock_mint.return_value = "aletheore-tok-xyz"

        result = runner.invoke(app, ["login"])

    assert result.exit_code == 0
    assert "acme" in result.stdout
    saved = json.loads(creds_path.read_text())
    assert saved["aletheore-managed-audit"] == "aletheore-tok-xyz"


def test_login_prompts_when_installation_ambiguous(tmp_path, monkeypatch):
    creds_path = tmp_path / "creds.json"
    monkeypatch.setattr("aletheore.credentials.DEFAULT_CREDENTIALS_PATH", creds_path)

    with patch("aletheore.device_auth.request_device_code") as mock_request_code, \
         patch("aletheore.device_auth.poll_for_access_token") as mock_poll, \
         patch("aletheore.device_auth.resolve_installation") as mock_resolve, \
         patch("aletheore.device_auth.mint_cli_token") as mock_mint:
        mock_request_code.return_value = MagicMock(
            verification_uri="https://github.com/login/device", user_code="ABCD-1234"
        )
        mock_poll.return_value = "gho_faketoken"
        mock_resolve.return_value = [
            {"installation_id": 100, "account_login": "acme"},
            {"installation_id": 200, "account_login": "other"},
        ]
        mock_mint.return_value = "aletheore-tok-xyz"

        result = runner.invoke(app, ["login"], input="2\n")

    assert result.exit_code == 0
    called_installation_id = mock_mint.call_args[0][1]
    assert called_installation_id == 200


def test_login_prints_error_and_exits_nonzero_on_device_flow_error():
    with patch("aletheore.device_auth.request_device_code") as mock_request_code, \
         patch("aletheore.device_auth.poll_for_access_token") as mock_poll:
        mock_request_code.return_value = MagicMock(
            verification_uri="https://github.com/login/device", user_code="ABCD-1234"
        )
        mock_poll.side_effect = DeviceFlowError("authorization was denied")

        result = runner.invoke(app, ["login"])

    assert result.exit_code == 1
    assert "denied" in result.stdout
```

- [ ] **Step 6: Run to verify failure**

Run: `cd prototype && python -m pytest tests/test_cli.py -k test_login -v`
Expected: FAIL with `Error: No such command 'login'`

- [ ] **Step 7: Implement the command**

Add to `prototype/aletheore/cli.py`, near the other `@app.command` definitions (e.g. after the `healthcheck` command, before `def main() -> None:`):

```python
@app.command(help="authenticate with GitHub via device flow and save a personal API token")
def login():
    import socket

    from aletheore.credentials import save_api_token
    from aletheore.device_auth import (
        DeviceFlowError,
        mint_cli_token,
        poll_for_access_token,
        request_device_code,
        resolve_installation,
    )

    try:
        code = request_device_code()
        console.print("First, authenticate with GitHub:")
        console.print(f"  1. Go to: [bold]{code.verification_uri}[/bold]")
        console.print(f"  2. Enter code: [bold cyan]{code.user_code}[/bold cyan]")
        console.print("Waiting for authorization...")
        github_token = poll_for_access_token(code)

        result = resolve_installation(github_token)
        if isinstance(result, dict):
            installation = result
        else:
            console.print("Multiple paid installations found - pick one:")
            for i, inst in enumerate(result, start=1):
                console.print(f"  {i}. {inst['account_login']}")
            choice = int(input("Enter a number: "))
            installation = result[choice - 1]

        label = f"{socket.gethostname()} (device flow)"
        token = mint_cli_token(github_token, installation["installation_id"], label)
        save_api_token("aletheore-managed-audit", token)
        console.print(
            f"[bold green]Logged in.[/bold green] Token saved for "
            f"[bold]{installation['account_login']}[/bold]. "
            f"This replaces any previously saved token."
        )
    except DeviceFlowError as e:
        console.print(f"[bold red]error:[/bold red] {e}")
        raise typer.Exit(code=1)
```

- [ ] **Step 8: Run to verify pass**

Run: `cd prototype && python -m pytest tests/test_cli.py -v`
Expected: PASS (all tests, including every pre-existing `test_cli.py` test)

- [ ] **Step 9: Run the full prototype suite**

Run: `cd prototype && python -m pytest -q`
Expected: PASS, no regressions

- [ ] **Step 10: Commit**

```bash
cd /Users/arihantkaul/Documents/GitHub/Veridion
git add prototype/aletheore/credentials.py prototype/aletheore/cli.py prototype/tests/test_credentials.py prototype/tests/test_cli.py
git commit -m "feat(cli): add \`aletheore login\` device-flow command"
```

---

## After execution

Remind the user: none of this works against real GitHub until "Device Flow" is toggled on for the App at `github.com/settings/apps/aletheore` (manual, one checkbox, same category as the webhook URL/callback URL changes already made by hand).
