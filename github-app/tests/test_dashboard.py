from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app_server.auth import encrypt_access_token, sign_session_id
from app_server.db import (
    create_session,
    hide_repo,
    insert_repo_history,
    set_installation_plan,
    set_public_status_enabled,
    upsert_installation,
)
from app_server.dashboard import _fetch_uninitialized_repos_sync
from app_server.main import app


async def _seed_wiki_overview(pool, installation_id, repo_full_name, description="System overview."):
    await pool.execute(
        """
        INSERT INTO wiki_overview (installation_id, repo_full_name, description, diagram_mermaid, source_commit)
        VALUES ($1, $2, $3, 'graph TD; A-->B;', 'abc123')
        ON CONFLICT (installation_id, repo_full_name) DO UPDATE
        SET description = EXCLUDED.description
        """,
        installation_id,
        repo_full_name,
        description,
    )


async def _seed_wiki_subsystem(pool, installation_id, repo_full_name, subsystem_id, name="Auth"):
    await pool.execute(
        """
        INSERT INTO wiki_subsystems
            (installation_id, repo_full_name, subsystem_id, name, description, files, diagram_mermaid, source_commit)
        VALUES ($1, $2, $3, $4, 'Handles authentication.', $5::jsonb, 'graph TD; A-->B;', 'abc123')
        ON CONFLICT (installation_id, repo_full_name, subsystem_id) DO UPDATE
        SET name = EXCLUDED.name
        """,
        installation_id,
        repo_full_name,
        subsystem_id,
        name,
        '["server/auth.py"]',
    )


async def _seed_wiki_build_status(pool, installation_id, repo_full_name, status, error_message=None):
    await pool.execute(
        """
        INSERT INTO wiki_build_status (installation_id, repo_full_name, status, error_message)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (installation_id, repo_full_name) DO UPDATE
        SET status = EXCLUDED.status, error_message = EXCLUDED.error_message
        """,
        installation_id,
        repo_full_name,
        status,
        error_message,
    )


def test_fetch_uninitialized_repos_collects_paginated_results(monkeypatch):
    repos = [{"full_name": f"some-user/repo-{i}"} for i in range(101)]
    seen_params = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.append(dict(request.url.params))
        page = int(request.url.params["page"])
        assert request.url.params["per_page"] == "100"
        start = (page - 1) * 100
        return httpx.Response(
            200,
            json={"total_count": len(repos), "repositories": repos[start:start + 100]},
            request=request,
        )

    monkeypatch.setattr("app_server.dashboard.get_installation_token", lambda *a, **k: "tok")
    monkeypatch.setattr(
        "app_server.dashboard._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )

    assert _fetch_uninitialized_repos_sync(801, "jwt") == repos
    assert seen_params == [{"per_page": "100", "page": "1"}, {"per_page": "100", "page": "2"}]


async def _seed_docs_symbol(
    pool, installation_id, repo_full_name, module_path, symbol_name, description, mode="generated"
):
    await pool.execute(
        """
        INSERT INTO docs_symbols
            (installation_id, repo_full_name, module_path, symbol_name, description, mode, source_commit)
        VALUES ($1, $2, $3, $4, $5, $6, 'abc123')
        ON CONFLICT (installation_id, repo_full_name, module_path, symbol_name) DO UPDATE
        SET description = EXCLUDED.description, mode = EXCLUDED.mode
        """,
        installation_id,
        repo_full_name,
        module_path,
        symbol_name,
        description,
        mode,
    )


async def _seed_docs_build_status(pool, installation_id, repo_full_name, status, error_message=None):
    await pool.execute(
        """
        INSERT INTO docs_build_status (installation_id, repo_full_name, status, error_message)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (installation_id, repo_full_name) DO UPDATE
        SET status = EXCLUDED.status, error_message = EXCLUDED.error_message
        """,
        installation_id,
        repo_full_name,
        status,
        error_message,
    )


def _evidence_with_module(module_path: str, function_name: str, docstring: str | None) -> dict:
    return {
        "repository": {
            "modules": [
                {
                    "path": module_path,
                    "language": "python",
                    "imports": [],
                    "imported_by": [],
                    "symbols": {
                        "functions": [{
                            "name": function_name, "start_line": 1, "end_line": 2,
                            "params": "()", "docstring": docstring, "return_type": None,
                            "is_public": True,
                        }],
                        "classes": [],
                    },
                }
            ]
        }
    }


async def _async_true(*args, **kwargs) -> bool:
    return True


async def _logged_in_client(pool, monkeypatch, administered_ids):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    await create_session(
        pool,
        "sess-1",
        42,
        "octocat",
        encrypt_access_token("gho_faketoken", "test-session-secret"),
        datetime.now(timezone.utc) + timedelta(hours=1),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "total_count": len(administered_ids),
                "installations": [{"id": installation_id} for installation_id in administered_ids],
            },
        )

    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )
    # This fixture's "logged in" user represents a real GitHub admin on the
    # repo - _has_real_admin_permission (app_server/admin.py) would otherwise
    # attempt a live GitHub API call and fail closed. See test_admin.py's
    # identical mock on its own _logged_in_client for the same reasoning.
    monkeypatch.setattr("app_server.admin._has_real_admin_permission", _async_true)

    app.state.db_pool = pool
    signed = sign_session_id("sess-1", "test-session-secret")
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test", cookies={"session": signed})


@pytest.mark.asyncio
async def test_list_my_repos_requires_login(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/app/repos")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_my_repos_returns_repos_across_administered_installations(pool, monkeypatch):
    await upsert_installation(pool, 701, "octocat")
    await set_installation_plan(pool, 701, "air")
    await upsert_installation(pool, 702, "another-org")
    await set_installation_plan(pool, 702, "air")
    await insert_repo_history(
        pool, 701, "octocat/hello-world", datetime.now(timezone.utc), {"repository": {"modules": []}}
    )
    await insert_repo_history(
        pool, 702, "another-org/service-b", datetime.now(timezone.utc), {"repository": {"modules": []}}
    )
    # A third installation the caller does NOT administer - must not leak in.
    await upsert_installation(pool, 703, "someone-else")
    await insert_repo_history(
        pool, 703, "someone-else/private-repo", datetime.now(timezone.utc), {"repository": {"modules": []}}
    )

    client = await _logged_in_client(pool, monkeypatch, administered_ids=[701, 702])
    async with client:
        response = await client.get("/app/repos")

    assert response.status_code == 200
    repos = response.json()["repos"]
    full_names = {r["repo_full_name"] for r in repos}
    assert full_names == {"octocat/hello-world", "another-org/service-b"}
    by_name = {r["repo_full_name"]: r for r in repos}
    assert by_name["octocat/hello-world"]["org"] == "octocat"
    assert by_name["octocat/hello-world"]["repo"] == "hello-world"
    assert by_name["octocat/hello-world"]["plan"] == "air"
    assert by_name["another-org/service-b"]["plan"] == "air"


@pytest.mark.asyncio
async def test_list_my_repos_excludes_free_plan_installations(pool, monkeypatch):
    # The hosted dashboard is an AIR (paid) feature - Community is free,
    # self-service, and unmanaged by design. Listing a free installation's
    # repos here would let every GitHub admin on that org click into a full
    # managed dashboard for free, without anyone paying for it.
    await upsert_installation(pool, 704, "octocat")  # defaults to plan='free'
    await insert_repo_history(
        pool, 704, "octocat/free-repo", datetime.now(timezone.utc), {"repository": {"modules": []}}
    )

    client = await _logged_in_client(pool, monkeypatch, administered_ids=[704])
    async with client:
        response = await client.get("/app/repos")

    assert response.status_code == 200
    assert response.json()["repos"] == []


@pytest.mark.asyncio
async def test_list_my_repos_excludes_a_hidden_repo(pool, monkeypatch):
    # A repo the customer deselected from the installation
    # (installation_repositories/removed - see webhooks/installation.py's
    # hide_repo) must disappear from the dashboard immediately, without
    # its scan history being deleted.
    await upsert_installation(pool, 705, "octocat")
    await set_installation_plan(pool, 705, "air")
    await insert_repo_history(
        pool, 705, "octocat/kept", datetime.now(timezone.utc), {"repository": {"modules": []}}
    )
    await insert_repo_history(
        pool, 705, "octocat/hidden", datetime.now(timezone.utc), {"repository": {"modules": []}}
    )
    await hide_repo(pool, 705, "octocat/hidden")

    client = await _logged_in_client(pool, monkeypatch, administered_ids=[705])
    async with client:
        response = await client.get("/app/repos")

    assert response.status_code == 200
    full_names = {r["repo_full_name"] for r in response.json()["repos"]}
    assert full_names == {"octocat/kept"}


@pytest.mark.asyncio
async def test_list_my_repos_includes_uninitialized_repos_with_no_scan_yet(pool, monkeypatch):
    # A freshly installed (or freshly paid) installation has no repo_history
    # rows at all until its first scan completes - it must still show up,
    # not vanish entirely, per the real gap found dogfooding this: a user
    # paid for their personal-account installation and the dashboard kept
    # showing nothing for it.
    await upsert_installation(pool, 801, "some-user")
    await set_installation_plan(pool, 801, "air")

    monkeypatch.setattr("app_server.dashboard.generate_app_jwt", lambda *a, **k: "fake-jwt")

    def fake_get_installation_token(installation_id, app_jwt, http_client=None):
        return "fake-installation-token"

    monkeypatch.setattr("app_server.dashboard.get_installation_token", fake_get_installation_token)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/installations":
            return httpx.Response(200, json={"total_count": 1, "installations": [{"id": 801}]})
        if request.url.path == "/installation/repositories":
            return httpx.Response(200, json={
                "repositories": [{"full_name": "some-user/proctor-browser"}],
            })
        raise AssertionError(f"unexpected request: {request.url.path}")

    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    await create_session(
        pool, "sess-1", 42, "octocat",
        encrypt_access_token("gho_faketoken", "test-session-secret"),
        datetime.now(timezone.utc) + timedelta(hours=1),
    )
    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )
    monkeypatch.setattr(
        "app_server.dashboard._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )

    app.state.db_pool = pool
    signed = sign_session_id("sess-1", "test-session-secret")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={"session": signed}) as client:
        response = await client.get("/app/repos")

    assert response.status_code == 200
    repos = response.json()["repos"]
    assert repos == [{
        "org": "some-user",
        "repo": "proctor-browser",
        "repo_full_name": "some-user/proctor-browser",
        "plan": "air",
        "initialized": False,
        "scan_limit_reached": False,
    }]


@pytest.mark.asyncio
async def test_list_my_repos_flags_uninitialized_repos_when_monthly_scan_cap_reached(pool, monkeypatch):
    await upsert_installation(pool, 802, "some-user")
    await set_installation_plan(pool, 802, "air")
    for i in range(10):
        await pool.execute(
            """
            INSERT INTO monthly_scanned_repos (installation_id, repo_full_name, month)
            VALUES (802, $1, date_trunc('month', now())::date)
            """,
            f"some-user/already-scanned-{i}",
        )

    monkeypatch.setattr("app_server.dashboard.generate_app_jwt", lambda *a, **k: "fake-jwt")

    def fake_get_installation_token(installation_id, app_jwt, http_client=None):
        return "fake-installation-token"

    monkeypatch.setattr("app_server.dashboard.get_installation_token", fake_get_installation_token)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/installations":
            return httpx.Response(200, json={"total_count": 1, "installations": [{"id": 802}]})
        if request.url.path == "/installation/repositories":
            return httpx.Response(200, json={
                "repositories": [{"full_name": "some-user/proctor-browser"}],
            })
        raise AssertionError(f"unexpected request: {request.url.path}")

    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    await create_session(
        pool, "sess-2", 42, "octocat",
        encrypt_access_token("gho_faketoken", "test-session-secret"),
        datetime.now(timezone.utc) + timedelta(hours=1),
    )
    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )
    monkeypatch.setattr(
        "app_server.dashboard._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )

    app.state.db_pool = pool
    signed = sign_session_id("sess-2", "test-session-secret")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={"session": signed}) as client:
        response = await client.get("/app/repos")

    assert response.status_code == 200
    repos = response.json()["repos"]
    assert repos == [{
        "org": "some-user",
        "repo": "proctor-browser",
        "repo_full_name": "some-user/proctor-browser",
        "plan": "air",
        "initialized": False,
        "scan_limit_reached": True,
    }]


@pytest.mark.asyncio
async def test_list_my_repos_does_not_duplicate_already_scanned_repos(pool, monkeypatch):
    # A repo that already has a completed scan must not ALSO show up as an
    # "uninitialized" duplicate just because it's still covered by the
    # installation's real GitHub repo list.
    await upsert_installation(pool, 802, "some-user")
    await set_installation_plan(pool, 802, "air")
    await insert_repo_history(
        pool, 802, "some-user/already-scanned", datetime.now(timezone.utc), {"repository": {"modules": []}}
    )

    monkeypatch.setattr("app_server.dashboard.generate_app_jwt", lambda *a, **k: "fake-jwt")

    def fake_get_installation_token(installation_id, app_jwt, http_client=None):
        return "fake-installation-token"

    monkeypatch.setattr("app_server.dashboard.get_installation_token", fake_get_installation_token)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/installations":
            return httpx.Response(200, json={"total_count": 1, "installations": [{"id": 802}]})
        if request.url.path == "/installation/repositories":
            return httpx.Response(200, json={
                "repositories": [{"full_name": "some-user/already-scanned"}],
            })
        raise AssertionError(f"unexpected request: {request.url.path}")

    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    await create_session(
        pool, "sess-1", 42, "octocat",
        encrypt_access_token("gho_faketoken", "test-session-secret"),
        datetime.now(timezone.utc) + timedelta(hours=1),
    )
    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )
    monkeypatch.setattr(
        "app_server.dashboard._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )

    app.state.db_pool = pool
    signed = sign_session_id("sess-1", "test-session-secret")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={"session": signed}) as client:
        response = await client.get("/app/repos")

    assert response.status_code == 200
    repos = response.json()["repos"]
    assert repos == [{
        "org": "some-user",
        "repo": "already-scanned",
        "repo_full_name": "some-user/already-scanned",
        "plan": "air",
        "initialized": True,
    }]


@pytest.mark.asyncio
async def test_list_my_repos_ignores_github_failure_when_fetching_uninitialized_repos(pool, monkeypatch):
    # If the installation-token exchange or repo listing call fails (rate
    # limit, revoked installation, transient GitHub outage), a user must
    # still see their already-scanned repos rather than getting a 502 for
    # the whole page over a "nice to have" enrichment.
    await upsert_installation(pool, 803, "some-user")
    await set_installation_plan(pool, 803, "team")

    monkeypatch.setattr("app_server.dashboard.generate_app_jwt", lambda *a, **k: "fake-jwt")

    def fake_get_installation_token(installation_id, app_jwt, http_client=None):
        raise httpx.HTTPStatusError(
            "boom",
            request=httpx.Request("POST", "https://api.github.com/x"),
            response=httpx.Response(403),
        )

    monkeypatch.setattr("app_server.dashboard.get_installation_token", fake_get_installation_token)

    client = await _logged_in_client(pool, monkeypatch, administered_ids=[803])
    async with client:
        response = await client.get("/app/repos")

    assert response.status_code == 200
    assert response.json()["repos"] == []


@pytest.mark.asyncio
async def test_app_repos_response_is_not_cacheable(pool, monkeypatch):
    # /app/... carries per-installation data (which repos someone
    # administers, at minimum) - a cached copy must never be replayable
    # after the session that fetched it ends.
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[701])
    async with client:
        response = await client.get("/app/repos")

    assert response.headers["cache-control"] == "no-store"


async def _logged_in_client_with_github_failure(pool, monkeypatch, status_code: int):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    await create_session(
        pool,
        "sess-1",
        42,
        "octocat",
        encrypt_access_token("gho_faketoken", "test-session-secret"),
        datetime.now(timezone.utc) + timedelta(hours=1),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="not json")

    monkeypatch.setattr(
        "app_server.admin._github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )

    app.state.db_pool = pool
    signed = sign_session_id("sess-1", "test-session-secret")
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test", cookies={"session": signed})


@pytest.mark.asyncio
async def test_list_my_repos_returns_401_when_github_token_expired(pool, monkeypatch):
    # A stored GitHub token can be revoked or expire after the local
    # session was created - this must surface as a clean 401 (which every
    # frontend page's apiGet() already redirects to sign-in on), not an
    # unhandled 500 whose non-JSON body then crashes the page's res.json().
    client = await _logged_in_client_with_github_failure(pool, monkeypatch, status_code=401)
    async with client:
        response = await client.get("/app/repos")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_my_repos_returns_502_on_github_outage(pool, monkeypatch):
    client = await _logged_in_client_with_github_failure(pool, monkeypatch, status_code=503)
    async with client:
        response = await client.get("/app/repos")
    assert response.status_code == 502


@pytest.mark.asyncio
async def test_list_my_repos_empty_for_no_administered_installations(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[])
    async with client:
        response = await client.get("/app/repos")
    assert response.status_code == 200
    assert response.json()["repos"] == []


@pytest.mark.asyncio
async def test_dashboard_requires_login(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/app/octocat/hello-world")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_dashboard_returns_404_for_unknown_repo(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[1])
    async with client:
        response = await client.get("/app/octocat/nonexistent")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_dashboard_rejects_unadministered_installation_with_the_same_404_as_a_nonexistent_repo(
    pool, monkeypatch
):
    await upsert_installation(pool, 1, "octocat")
    await insert_repo_history(
        pool,
        1,
        "octocat/hello-world",
        datetime.now(timezone.utc),
        {"repository": {"modules": []}},
    )
    # The caller is logged in, but their GitHub account administers a
    # different installation (999), not the one that owns this repo (1) -
    # this is the exact cross-tenant case the fix closes. 404, not 403: a
    # real repo the caller doesn't administer must be indistinguishable
    # from a repo that was never connected at all (see
    # test_dashboard_returns_404_for_unknown_repo just above) - otherwise
    # the status code alone is a repo-existence oracle for any
    # authenticated user (docs/audits/Claude_Audit.md finding 34).
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[999])
    async with client:
        response = await client.get("/app/octocat/hello-world")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_dashboard_requires_paid_plan(pool, monkeypatch):
    # Community is free, self-service, and unmanaged by design - the hosted
    # dashboard (scan history, health monitoring, AIRview) is an AIR-only
    # feature. Without this, any GitHub admin on a free-plan org could click
    # into a full managed dashboard for free.
    await upsert_installation(pool, 511, "octocat")  # defaults to plan='free'
    await insert_repo_history(
        pool, 511, "octocat/hello-world", datetime.now(timezone.utc), {"repository": {"modules": []}}
    )
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[511])
    async with client:
        response = await client.get("/app/octocat/hello-world")
    assert response.status_code == 402


@pytest.mark.asyncio
async def test_dashboard_health_requires_paid_plan(pool, monkeypatch):
    await upsert_installation(pool, 512, "octocat")  # defaults to plan='free'
    await insert_repo_history(
        pool, 512, "octocat/hello-world", datetime.now(timezone.utc), {"repository": {"modules": []}}
    )
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[512])
    async with client:
        response = await client.get("/app/octocat/hello-world/health")
    assert response.status_code == 402


@pytest.mark.asyncio
async def test_dashboard_returns_data_for_known_repo(pool, monkeypatch):
    await upsert_installation(pool, 1, "octocat")
    await set_installation_plan(pool, 1, "air")
    await insert_repo_history(
        pool,
        1,
        "octocat/hello-world",
        datetime.now(timezone.utc),
        {"repository": {"modules": []}},
    )
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[1])
    async with client:
        response = await client.get("/app/octocat/hello-world")
    assert response.status_code == 200
    body = response.json()
    assert body["repo_full_name"] == "octocat/hello-world"
    assert len(body["history"]) == 1


@pytest.mark.asyncio
async def test_dashboard_returns_empty_dismissed_finding_keys_by_default(pool, monkeypatch):
    await upsert_installation(pool, 1, "octocat")
    await set_installation_plan(pool, 1, "air")
    await insert_repo_history(
        pool, 1, "octocat/hello-world", datetime.now(timezone.utc), {"repository": {"modules": []}}
    )
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[1])
    async with client:
        response = await client.get("/app/octocat/hello-world")
    body = response.json()
    assert body["dismissed_finding_keys"] == {"secret": [], "vulnerability": []}


@pytest.mark.asyncio
async def test_dismiss_finding_route_requires_login(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/app/octocat/hello-world/findings/dismiss",
            json={"finding_type": "secret", "finding": {"path": "a.py", "pattern": "x", "match_preview": "y"}},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_dismiss_finding_route_rejects_unknown_finding_type(pool, monkeypatch):
    await upsert_installation(pool, 1, "octocat")
    await set_installation_plan(pool, 1, "air")
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[1])
    async with client:
        response = await client.post(
            "/app/octocat/hello-world/findings/dismiss",
            json={"finding_type": "layer_violation", "finding": {}},
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_dismiss_finding_route_rejects_finding_missing_required_field(pool, monkeypatch):
    await upsert_installation(pool, 1, "octocat")
    await set_installation_plan(pool, 1, "air")
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[1])
    async with client:
        response = await client.post(
            "/app/octocat/hello-world/findings/dismiss",
            json={"finding_type": "secret", "finding": {"path": "a.py"}},
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_dismiss_finding_route_then_get_dashboard_reflects_it(pool, monkeypatch):
    await upsert_installation(pool, 1, "octocat")
    await set_installation_plan(pool, 1, "air")
    await insert_repo_history(
        pool, 1, "octocat/hello-world", datetime.now(timezone.utc), {"repository": {"modules": []}}
    )
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[1])
    finding = {"path": "config.py", "pattern": "aws_access_key_id", "match_preview": "AKIA****...MNOP"}
    async with client:
        dismiss_response = await client.post(
            "/app/octocat/hello-world/findings/dismiss",
            json={"finding_type": "secret", "finding": finding, "reason": "false positive"},
        )
        get_response = await client.get("/app/octocat/hello-world")

    assert dismiss_response.status_code == 200
    body = get_response.json()
    assert len(body["dismissed_finding_keys"]["secret"]) == 1

    row = await pool.fetchrow("SELECT reason, dismissed_by FROM dismissed_findings WHERE installation_id = 1")
    assert row["reason"] == "false positive"
    assert row["dismissed_by"] == "octocat"


@pytest.mark.asyncio
async def test_undismiss_finding_route_removes_it(pool, monkeypatch):
    await upsert_installation(pool, 1, "octocat")
    await set_installation_plan(pool, 1, "air")
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[1])
    finding = {"ecosystem": "PyPI", "package": "requests", "advisory_id": "GHSA-1"}
    async with client:
        await client.post(
            "/app/octocat/hello-world/findings/dismiss",
            json={"finding_type": "vulnerability", "finding": finding},
        )
        undismiss_response = await client.post(
            "/app/octocat/hello-world/findings/undismiss",
            json={"finding_type": "vulnerability", "finding": finding},
        )
        get_response = await client.get("/app/octocat/hello-world")

    assert undismiss_response.status_code == 200
    body = get_response.json()
    assert body["dismissed_finding_keys"]["vulnerability"] == []


@pytest.mark.asyncio
async def test_public_health_returns_latest_per_endpoint(pool):
    await upsert_installation(pool, 500, "octocat")
    await set_public_status_enabled(pool, 500, "octocat/hello-world", True)
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
    endpoints = {(endpoint["method"], endpoint["path"]): endpoint for endpoint in body["endpoints"]}
    assert len(endpoints) == 2
    assert endpoints[("GET", "/api/users")]["latency_ms"] == 88.0
    assert endpoints[("GET", "/api/orders")]["reachable"] is False
    assert endpoints[("GET", "/api/orders")]["status_code"] is None
    # /api/users: 2 of 2 checks reachable; /api/orders: 0 of 1.
    assert endpoints[("GET", "/api/users")]["uptime_pct_7d"] == 1.0
    assert endpoints[("GET", "/api/orders")]["uptime_pct_7d"] == 0.0
    for endpoint in endpoints.values():
        assert set(endpoint.keys()) == {
            "method",
            "path",
            "reachable",
            "status_code",
            "latency_ms",
            "checked_at",
            "uptime_pct_7d",
        }


@pytest.mark.asyncio
async def test_public_health_uptime_pct_excludes_checks_older_than_7_days(pool):
    await upsert_installation(pool, 507, "octocat")
    await set_public_status_enabled(pool, 507, "octocat/hello-world", True)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO endpoint_health
                (installation_id, repo_full_name, endpoint_method, endpoint_path,
                 reachable, status_code, latency_ms, checked_at)
            VALUES
                (507, 'octocat/hello-world', 'GET', '/api/users', false, 500, 90.5, now() - interval '10 days'),
                (507, 'octocat/hello-world', 'GET', '/api/users', true, 200, 88.0, now())
            """
        )

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/health/octocat/hello-world")

    assert response.status_code == 200
    endpoints = {(e["method"], e["path"]): e for e in response.json()["endpoints"]}
    # Only the recent, reachable check falls inside the 7-day window.
    assert endpoints[("GET", "/api/users")]["uptime_pct_7d"] == 1.0


@pytest.mark.asyncio
async def test_public_health_excludes_endpoints_not_checked_recently(pool):
    # An endpoint whose most recent check is older than the staleness
    # window has either been removed from the route set or was never a
    # real endpoint (e.g. a fixed scanner false positive) - either way the
    # sweep has stopped checking it, and this public API shouldn't keep
    # reporting it as "up" forever off one ancient row.
    await upsert_installation(pool, 508, "octocat")
    await set_public_status_enabled(pool, 508, "octocat/hello-world", True)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO endpoint_health
                (installation_id, repo_full_name, endpoint_method, endpoint_path,
                 reachable, status_code, latency_ms, checked_at)
            VALUES
                (508, 'octocat/hello-world', 'GET', '/api/users', true, 200, 88.0, now()),
                (508, 'octocat/hello-world', 'GET', '/removed-route', true, 404, 5.0, now() - interval '1 day')
            """
        )

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/health/octocat/hello-world")

    assert response.status_code == 200
    endpoints = {(e["method"], e["path"]) for e in response.json()["endpoints"]}
    assert endpoints == {("GET", "/api/users")}


@pytest.mark.asyncio
async def test_public_health_rate_limits_after_threshold(pool, monkeypatch, redis_conn):
    from app_server import dashboard

    await upsert_installation(pool, 508, "octocat")
    await set_public_status_enabled(pool, 508, "octocat/hello-world", True)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO endpoint_health
                (installation_id, repo_full_name, endpoint_method, endpoint_path, reachable)
            VALUES (508, 'octocat/hello-world', 'GET', '/api/users', true)
            """
        )

    monkeypatch.setattr(dashboard, "PUBLIC_HEALTH_RATE_LIMIT", 2)
    monkeypatch.setattr("app_server.redis_client.get_redis_client", lambda: redis_conn)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    headers = {"x-forwarded-for": "203.0.113.50"}
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/v1/health/octocat/hello-world", headers=headers)
        second = await client.get("/v1/health/octocat/hello-world", headers=headers)
        third = await client.get("/v1/health/octocat/hello-world", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.headers["access-control-allow-origin"] == "*"
    assert "retry-after" in third.headers


@pytest.mark.asyncio
async def test_public_health_rate_limit_is_keyed_per_ip(pool, monkeypatch, redis_conn):
    from app_server import dashboard

    await upsert_installation(pool, 509, "octocat")
    await set_public_status_enabled(pool, 509, "octocat/hello-world", True)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO endpoint_health
                (installation_id, repo_full_name, endpoint_method, endpoint_path, reachable)
            VALUES (509, 'octocat/hello-world', 'GET', '/api/users', true)
            """
        )

    monkeypatch.setattr(dashboard, "PUBLIC_HEALTH_RATE_LIMIT", 1)
    monkeypatch.setattr("app_server.redis_client.get_redis_client", lambda: redis_conn)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get(
            "/v1/health/octocat/hello-world", headers={"x-forwarded-for": "1.1.1.1"}
        )
        second_same_ip = await client.get(
            "/v1/health/octocat/hello-world", headers={"x-forwarded-for": "1.1.1.1"}
        )
        first_other_ip = await client.get(
            "/v1/health/octocat/hello-world", headers={"x-forwarded-for": "2.2.2.2"}
        )

    assert first.status_code == 200
    assert second_same_ip.status_code == 429
    assert first_other_ip.status_code == 200


@pytest.mark.asyncio
async def test_public_health_fails_open_when_redis_is_unreachable(pool, monkeypatch):
    await upsert_installation(pool, 510, "octocat")
    await set_public_status_enabled(pool, 510, "octocat/hello-world", True)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO endpoint_health
                (installation_id, repo_full_name, endpoint_method, endpoint_path, reachable)
            VALUES (510, 'octocat/hello-world', 'GET', '/api/users', true)
            """
        )

    def _boom():
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr("app_server.redis_client.get_redis_client", _boom)

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/health/octocat/hello-world")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_public_health_404s_with_no_data(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/health/octocat/no-such-repo")
    assert response.status_code == 404
    assert response.headers["access-control-allow-origin"] == "*"


@pytest.mark.asyncio
async def test_public_health_404s_when_not_opted_in(pool):
    # Off by default (migration 043) - real health data exists, but the
    # installation never turned public status on.
    await upsert_installation(pool, 511, "octocat")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO endpoint_health
                (installation_id, repo_full_name, endpoint_method, endpoint_path, reachable)
            VALUES (511, 'octocat/hello-world', 'GET', '/api/users', true)
            """
        )

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/health/octocat/hello-world")

    assert response.status_code == 404
    # Same detail text as "no data at all" - not opted in must not be
    # distinguishable from never-scanned by an outside prober.
    assert response.json()["detail"] == "no health data for this repo"


@pytest.mark.asyncio
async def test_public_health_opt_in_does_not_leak_other_repos_in_the_account(pool):
    # F21: public_status_enabled used to be a column on installations, so
    # opting in one repo silently exposed every other repo's endpoint
    # health under the same account, private repos included.
    await upsert_installation(pool, 513, "octocat")
    await set_public_status_enabled(pool, 513, "octocat/public-api", True)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO endpoint_health
                (installation_id, repo_full_name, endpoint_method, endpoint_path, reachable)
            VALUES (513, 'octocat/internal-billing', 'GET', '/api/invoices', true)
            """
        )

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        opted_in_repo = await client.get("/v1/health/octocat/public-api")
        other_repo = await client.get("/v1/health/octocat/internal-billing")

    assert opted_in_repo.status_code == 404  # no endpoint_health rows for it, but opted in
    assert other_repo.status_code == 404
    assert other_repo.json()["detail"] == "no health data for this repo"


@pytest.mark.asyncio
async def test_public_health_opting_in_then_out_toggles_visibility(pool):
    await upsert_installation(pool, 512, "octocat")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO endpoint_health
                (installation_id, repo_full_name, endpoint_method, endpoint_path, reachable)
            VALUES (512, 'octocat/hello-world', 'GET', '/api/users', true)
            """
        )

    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        before = await client.get("/v1/health/octocat/hello-world")
        await set_public_status_enabled(pool, 512, "octocat/hello-world", True)
        during = await client.get("/v1/health/octocat/hello-world")
        await set_public_status_enabled(pool, 512, "octocat/hello-world", False)
        after = await client.get("/v1/health/octocat/hello-world")

    assert before.status_code == 404
    assert during.status_code == 200
    assert after.status_code == 404


@pytest.mark.asyncio
async def test_dashboard_health_requires_login(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/app/octocat/hello-world/health")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_dashboard_health_keeps_results_separate_per_target(pool, monkeypatch):
    # Regression test: DISTINCT ON must key on target_id too, or two
    # targets checking the exact same method+path collapse into one row
    # and one target's result silently vanishes.
    await upsert_installation(pool, 503, "octocat")
    await set_installation_plan(pool, 503, "air")
    await insert_repo_history(
        pool, 503, "octocat/hello-world", datetime.now(timezone.utc), {"repository": {"modules": []}}
    )
    async with pool.acquire() as conn:
        staging_id = await conn.fetchval(
            """
            INSERT INTO health_check_targets (installation_id, repo_full_name, label, base_url)
            VALUES (503, 'octocat/hello-world', 'Staging', 'https://staging.example.com') RETURNING id
            """
        )
        prod_id = await conn.fetchval(
            """
            INSERT INTO health_check_targets (installation_id, repo_full_name, label, base_url)
            VALUES (503, 'octocat/hello-world', 'Production', 'https://prod.example.com') RETURNING id
            """
        )
        await conn.execute(
            """
            INSERT INTO endpoint_health
                (installation_id, repo_full_name, endpoint_method, endpoint_path, reachable, target_id)
            VALUES
                (503, 'octocat/hello-world', 'GET', '/api/users', true, $1),
                (503, 'octocat/hello-world', 'GET', '/api/users', false, $2)
            """,
            staging_id,
            prod_id,
        )

    client = await _logged_in_client(pool, monkeypatch, administered_ids=[503])
    async with client:
        response = await client.get("/app/octocat/hello-world/health")

    assert response.status_code == 200
    endpoints = response.json()["endpoints"]
    assert len(endpoints) == 2
    by_label = {e["target_label"]: e for e in endpoints}
    assert by_label["Staging"]["reachable"] is True
    assert by_label["Production"]["reachable"] is False


@pytest.mark.asyncio
async def test_dashboard_health_history_requires_login(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/app/octocat/hello-world/health/history", params={"method": "GET", "path": "/api/users"}
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_dashboard_health_history_returns_checks_newest_first(pool, monkeypatch):
    await upsert_installation(pool, 504, "octocat")
    await set_installation_plan(pool, 504, "air")
    await insert_repo_history(
        pool, 504, "octocat/hello-world", datetime.now(timezone.utc), {"repository": {"modules": []}}
    )
    async with pool.acquire() as conn:
        target_id = await conn.fetchval(
            """
            INSERT INTO health_check_targets (installation_id, repo_full_name, label, base_url)
            VALUES (504, 'octocat/hello-world', 'Production', 'https://prod.example.com') RETURNING id
            """
        )
        await conn.execute(
            """
            INSERT INTO endpoint_health
                (installation_id, repo_full_name, endpoint_method, endpoint_path,
                 reachable, status_code, latency_ms, checked_at, target_id)
            VALUES
                (504, 'octocat/hello-world', 'GET', '/api/users', true, 200, 80.0, now() - interval '2 minutes', $1),
                (504, 'octocat/hello-world', 'GET', '/api/users', false, 500, NULL, now() - interval '1 minute', $1),
                (504, 'octocat/hello-world', 'GET', '/api/users', true, 200, 95.0, now(), $1)
            """,
            target_id,
        )

    client = await _logged_in_client(pool, monkeypatch, administered_ids=[504])
    async with client:
        response = await client.get(
            "/app/octocat/hello-world/health/history",
            params={"method": "GET", "path": "/api/users", "target_id": target_id},
        )

    assert response.status_code == 200
    body = response.json()
    checks = body["checks"]
    assert len(checks) == 3
    # Newest first.
    assert checks[0]["reachable"] is True
    assert checks[0]["latency_ms"] == 95.0
    assert checks[1]["reachable"] is False
    assert checks[1]["status_code"] == 500
    assert checks[2]["latency_ms"] == 80.0


@pytest.mark.asyncio
async def test_dashboard_health_history_respects_limit(pool, monkeypatch):
    await upsert_installation(pool, 505, "octocat")
    await set_installation_plan(pool, 505, "air")
    await insert_repo_history(
        pool, 505, "octocat/hello-world", datetime.now(timezone.utc), {"repository": {"modules": []}}
    )
    async with pool.acquire() as conn:
        for i in range(5):
            await conn.execute(
                """
                INSERT INTO endpoint_health
                    (installation_id, repo_full_name, endpoint_method, endpoint_path, reachable, checked_at)
                VALUES (505, 'octocat/hello-world', 'GET', '/api/users', true, now() - ($1 || ' minutes')::interval)
                """,
                str(i),
            )

    client = await _logged_in_client(pool, monkeypatch, administered_ids=[505])
    async with client:
        response = await client.get(
            "/app/octocat/hello-world/health/history",
            params={"method": "GET", "path": "/api/users", "limit": 2},
        )

    assert response.status_code == 200
    assert len(response.json()["checks"]) == 2


@pytest.mark.asyncio
async def test_dashboard_health_history_rejects_unadministered_installation(pool, monkeypatch):
    # 404, not 403 - see test_dashboard_rejects_unadministered_installation_
    # with_the_same_404_as_a_nonexistent_repo for why (finding 34).
    await upsert_installation(pool, 506, "octocat")
    await insert_repo_history(
        pool, 506, "octocat/hello-world", datetime.now(timezone.utc), {"repository": {"modules": []}}
    )
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[])
    async with client:
        response = await client.get(
            "/app/octocat/hello-world/health/history", params={"method": "GET", "path": "/api/users"}
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_dashboard_health_rejects_unadministered_installation(pool, monkeypatch):
    # 404, not 403 - see test_dashboard_rejects_unadministered_installation_
    # with_the_same_404_as_a_nonexistent_repo for why (finding 34).
    await upsert_installation(pool, 501, "octocat")
    await insert_repo_history(
        pool,
        501,
        "octocat/hello-world",
        datetime.now(timezone.utc),
        {"repository": {"modules": []}},
    )
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO endpoint_health
                (installation_id, repo_full_name, endpoint_method, endpoint_path, reachable)
            VALUES (501, 'octocat/hello-world', 'GET', '/api/users', true)
            """
        )
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[999])
    async with client:
        response = await client.get("/app/octocat/hello-world/health")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_dashboard_health_includes_evidence_resolution(pool, monkeypatch):
    await upsert_installation(pool, 502, "octocat")
    await set_installation_plan(pool, 502, "air")
    await insert_repo_history(
        pool,
        502,
        "octocat/hello-world",
        datetime.now(timezone.utc),
        {
            "repository": {
                "api_endpoints": {
                    "endpoints": [
                        {
                            "method": "GET",
                            "path": "/api/users",
                            "file": "server/routes/users.py",
                            "line": 42,
                            "handler": "get_users",
                        }
                    ]
                }
            }
        },
    )
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO endpoint_health
                (installation_id, repo_full_name, endpoint_method, endpoint_path, reachable)
            VALUES (502, 'octocat/hello-world', 'GET', '/api/users', false)
            """
        )
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[502])
    async with client:
        response = await client.get("/app/octocat/hello-world/health")

    assert response.status_code == 200
    body = response.json()
    endpoints = {(e["method"], e["path"]): e for e in body["endpoints"]}
    resolution = endpoints[("GET", "/api/users")]["evidence_resolution"]
    assert resolution["file"] == "server/routes/users.py"
    assert resolution["line"] == 42
    assert resolution["symbol"] == "get_users"
    assert resolution["confidence"] == "exact"


@pytest.mark.asyncio
async def test_dashboard_health_includes_stale_endpoints(pool, monkeypatch):
    await upsert_installation(pool, 504, "octocat")
    await set_installation_plan(pool, 504, "air")
    await insert_repo_history(
        pool,
        504,
        "octocat/hello-world",
        datetime.now(timezone.utc),
        {
            "repository": {
                "api_endpoints": {
                    "endpoints": [
                        {
                            "method": "GET",
                            "path": "/api/legacy",
                            "file": "routes.py",
                            "line": 5,
                            "handler": "legacy",
                        }
                    ]
                }
            }
        },
    )
    async with pool.acquire() as conn:
        for _ in range(5):
            await conn.execute(
                """
                INSERT INTO endpoint_health
                    (installation_id, repo_full_name, endpoint_method, endpoint_path, reachable)
                VALUES (504, 'octocat/hello-world', 'GET', '/api/legacy', false)
                """
            )
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[504])
    async with client:
        response = await client.get("/app/octocat/hello-world/health")

    assert response.status_code == 200
    stale = response.json()["stale_endpoints"]
    assert stale == [
        {
            "method": "GET",
            "path": "/api/legacy",
            "file": "routes.py",
            "line": 5,
            "check_count": 5,
        }
    ]


@pytest.mark.asyncio
async def test_dashboard_health_omits_stale_endpoints_with_recent_success(pool, monkeypatch):
    await upsert_installation(pool, 505, "octocat")
    await set_installation_plan(pool, 505, "air")
    await insert_repo_history(
        pool,
        505,
        "octocat/hello-world",
        datetime.now(timezone.utc),
        {
            "repository": {
                "api_endpoints": {
                    "endpoints": [
                        {
                            "method": "GET",
                            "path": "/api/active",
                            "file": "routes.py",
                            "line": 5,
                            "handler": "active",
                        }
                    ]
                }
            }
        },
    )
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO endpoint_health
                (installation_id, repo_full_name, endpoint_method, endpoint_path, reachable)
            VALUES
                (505, 'octocat/hello-world', 'GET', '/api/active', true),
                (505, 'octocat/hello-world', 'GET', '/api/active', false),
                (505, 'octocat/hello-world', 'GET', '/api/active', false),
                (505, 'octocat/hello-world', 'GET', '/api/active', false),
                (505, 'octocat/hello-world', 'GET', '/api/active', false)
            """
        )
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[505])
    async with client:
        response = await client.get("/app/octocat/hello-world/health")

    assert response.status_code == 200
    assert response.json()["stale_endpoints"] == []


@pytest.mark.asyncio
async def test_dashboard_wiki_requires_login(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/app/octocat/hello-world/wiki")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_dashboard_wiki_requires_paid_plan(pool, monkeypatch):
    await upsert_installation(pool, 601, "octocat")  # defaults to plan='free'
    await insert_repo_history(
        pool,
        601,
        "octocat/hello-world",
        datetime.now(timezone.utc),
        {"repository": {"modules": []}},
    )
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[601])
    async with client:
        response = await client.get("/app/octocat/hello-world/wiki")
    assert response.status_code == 402


@pytest.mark.asyncio
async def test_dashboard_wiki_returns_overview_and_subsystems(pool, monkeypatch):
    await upsert_installation(pool, 602, "octocat")
    await set_installation_plan(pool, 602, "air")
    await insert_repo_history(
        pool,
        602,
        "octocat/hello-world",
        datetime.now(timezone.utc),
        {"repository": {"modules": []}},
    )
    await _seed_wiki_overview(pool, 602, "octocat/hello-world")
    await _seed_wiki_subsystem(pool, 602, "octocat/hello-world", "auth")

    client = await _logged_in_client(pool, monkeypatch, administered_ids=[602])
    async with client:
        response = await client.get("/app/octocat/hello-world/wiki")

    assert response.status_code == 200
    body = response.json()
    assert body["repo_full_name"] == "octocat/hello-world"
    assert body["overview"]["description"] == "System overview."
    assert body["overview"]["diagram_mermaid"] == "graph TD; A-->B;"
    assert len(body["subsystems"]) == 1
    assert body["subsystems"][0]["subsystem_id"] == "auth"
    assert body["subsystems"][0]["name"] == "Auth"


@pytest.mark.asyncio
async def test_dashboard_wiki_returns_null_overview_when_not_yet_generated(pool, monkeypatch):
    await upsert_installation(pool, 603, "octocat")
    await set_installation_plan(pool, 603, "air")
    await insert_repo_history(
        pool,
        603,
        "octocat/hello-world",
        datetime.now(timezone.utc),
        {"repository": {"modules": []}},
    )
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[603])
    async with client:
        response = await client.get("/app/octocat/hello-world/wiki")

    assert response.status_code == 200
    body = response.json()
    assert body["overview"] is None
    assert body["subsystems"] == []
    assert body["build_status"] is None
    assert body["build_error"] is None


@pytest.mark.asyncio
async def test_dashboard_wiki_surfaces_failed_build_status(pool, monkeypatch):
    # Before this fix, a failed build left the dashboard indistinguishable
    # from "hasn't been built yet" - the customer had no way to tell a
    # transient in-progress state apart from a build that's never coming.
    await upsert_installation(pool, 605, "octocat")
    await set_installation_plan(pool, 605, "air")
    await insert_repo_history(
        pool,
        605,
        "octocat/hello-world",
        datetime.now(timezone.utc),
        {"repository": {"modules": []}},
    )
    await _seed_wiki_build_status(pool, 605, "octocat/hello-world", "failed", "model provider unavailable")

    client = await _logged_in_client(pool, monkeypatch, administered_ids=[605])
    async with client:
        response = await client.get("/app/octocat/hello-world/wiki")

    assert response.status_code == 200
    body = response.json()
    assert body["overview"] is None
    assert body["build_status"] == "failed"
    assert body["build_error"] == "model provider unavailable"


@pytest.mark.asyncio
async def test_dashboard_wiki_surfaces_failed_status_alongside_existing_overview(pool, monkeypatch):
    # A later incremental update can fail after the first full build already
    # succeeded - the API must keep reporting that failure (not silently
    # drop it just because an overview now exists), so the dashboard can
    # tell the customer their AIRview content may be stale.
    await upsert_installation(pool, 606, "octocat")
    await set_installation_plan(pool, 606, "air")
    await insert_repo_history(
        pool,
        606,
        "octocat/hello-world",
        datetime.now(timezone.utc),
        {"repository": {"modules": []}},
    )
    await _seed_wiki_overview(pool, 606, "octocat/hello-world")
    await _seed_wiki_build_status(pool, 606, "octocat/hello-world", "failed", "LLM API unavailable")

    client = await _logged_in_client(pool, monkeypatch, administered_ids=[606])
    async with client:
        response = await client.get("/app/octocat/hello-world/wiki")

    assert response.status_code == 200
    body = response.json()
    assert body["overview"] is not None
    assert body["build_status"] == "failed"
    assert body["build_error"] == "LLM API unavailable"


@pytest.mark.asyncio
async def test_dashboard_wiki_subsystem_requires_login(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/app/octocat/hello-world/wiki/auth")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_dashboard_wiki_subsystem_returns_detail(pool, monkeypatch):
    await upsert_installation(pool, 604, "octocat")
    await set_installation_plan(pool, 604, "air")
    await insert_repo_history(
        pool,
        604,
        "octocat/hello-world",
        datetime.now(timezone.utc),
        {"repository": {"modules": []}},
    )
    await _seed_wiki_subsystem(pool, 604, "octocat/hello-world", "auth")

    client = await _logged_in_client(pool, monkeypatch, administered_ids=[604])
    async with client:
        response = await client.get("/app/octocat/hello-world/wiki/auth")

    assert response.status_code == 200
    body = response.json()
    subsystem = body["subsystem"]
    assert subsystem["subsystem_id"] == "auth"
    assert subsystem["name"] == "Auth"
    assert subsystem["files"] == ["server/auth.py"]
    assert subsystem["description"] == "Handles authentication."


@pytest.mark.asyncio
async def test_dashboard_wiki_file_returns_structural_fallback_for_unpaged_module(pool, monkeypatch):
    await upsert_installation(pool, 607, "octocat")
    await set_installation_plan(pool, 607, "air")
    await insert_repo_history(
        pool,
        607,
        "octocat/hello-world",
        datetime.now(timezone.utc),
        {
            "repository": {
                "modules": [
                    {
                        "path": "src/auth.py",
                        "language": "python",
                        "imports": ["src/tokens.py"],
                        "imported_by": ["src/app.py"],
                        "symbols": {
                            "functions": [{"name": "login", "start_line": 12}],
                            "classes": [],
                        },
                    }
                ]
            }
        },
    )
    await pool.execute(
        """
        INSERT INTO wiki_subsystems
            (installation_id, repo_full_name, subsystem_id, name, description, files, diagram_mermaid, source_commit)
        VALUES (607, 'octocat/hello-world', 'auth', 'Auth', 'Handles authentication.', $1::jsonb, 'graph TD; A-->B;', 'abc123')
        """,
        '[{"path":"src/auth.py","role":"Owns request authentication.","key_symbols":[]}]',
    )

    client = await _logged_in_client(pool, monkeypatch, administered_ids=[607])
    async with client:
        response = await client.get("/app/octocat/hello-world/wiki/file/src/auth.py")

    assert response.status_code == 200
    body = response.json()
    assert body["file"]["detail_source"] == "fallback"
    assert "Owns request authentication." in body["file"]["detail"]
    assert "`src/tokens.py`" in body["file"]["detail"]
    assert "`login` (line 12)" in body["file"]["detail"]


@pytest.mark.asyncio
async def test_dashboard_wiki_file_fetches_unindexed_file_without_llm(pool, monkeypatch):
    await upsert_installation(pool, 608, "octocat")
    await set_installation_plan(pool, 608, "air")
    await insert_repo_history(
        pool,
        608,
        "octocat/hello-world",
        datetime.now(timezone.utc),
        {"repository": {"modules": []}},
    )
    monkeypatch.setattr(
        "app_server.dashboard._fetch_wiki_file_content_sync",
        lambda *args: "Config\n=====\n\nThe application configuration.\n",
    )

    client = await _logged_in_client(pool, monkeypatch, administered_ids=[608])
    async with client:
        response = await client.get("/app/octocat/hello-world/wiki/file/config.toml")

    assert response.status_code == 200
    body = response.json()
    assert body["file"]["detail_source"] == "fallback"
    assert "The application configuration." in body["file"]["detail"]


@pytest.mark.asyncio
async def test_dashboard_wiki_subsystem_404s_for_unknown_id(pool, monkeypatch):
    await upsert_installation(pool, 605, "octocat")
    await set_installation_plan(pool, 605, "air")
    await insert_repo_history(
        pool,
        605,
        "octocat/hello-world",
        datetime.now(timezone.utc),
        {"repository": {"modules": []}},
    )
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[605])
    async with client:
        response = await client.get("/app/octocat/hello-world/wiki/nonexistent")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_dashboard_docs_requires_login(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/app/octocat/hello-world/docs")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_dashboard_docs_requires_paid_plan(pool, monkeypatch):
    await upsert_installation(pool, 701, "octocat")  # defaults to plan='free'
    await insert_repo_history(
        pool, 701, "octocat/hello-world", datetime.now(timezone.utc),
        _evidence_with_module("a.py", "add", None),
    )
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[701])
    async with client:
        response = await client.get("/app/octocat/hello-world/docs")
    assert response.status_code == 402


@pytest.mark.asyncio
async def test_dashboard_docs_renders_verbatim_docstring_with_no_ai_marker(pool, monkeypatch):
    await upsert_installation(pool, 702, "octocat")
    await set_installation_plan(pool, 702, "air")
    await insert_repo_history(
        pool, 702, "octocat/hello-world", datetime.now(timezone.utc),
        _evidence_with_module("a.py", "add", "Adds two numbers."),
    )
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[702])
    async with client:
        response = await client.get("/app/octocat/hello-world/docs")

    assert response.status_code == 200
    body = response.json()
    assert body["repo_full_name"] == "octocat/hello-world"
    assert "Adds two numbers." in body["modules"]["a.py"]
    assert "AI-generated" not in body["modules"]["a.py"]
    assert "AI-polished" not in body["modules"]["a.py"]


@pytest.mark.asyncio
async def test_dashboard_docs_merges_ai_generated_description_for_undocumented_symbol(pool, monkeypatch):
    await upsert_installation(pool, 703, "octocat")
    await set_installation_plan(pool, 703, "air")
    await insert_repo_history(
        pool, 703, "octocat/hello-world", datetime.now(timezone.utc),
        _evidence_with_module("a.py", "add", None),
    )
    await _seed_docs_symbol(pool, 703, "octocat/hello-world", "a.py", "add", "Adds two numbers and returns the sum.", "generated")

    client = await _logged_in_client(pool, monkeypatch, administered_ids=[703])
    async with client:
        response = await client.get("/app/octocat/hello-world/docs")

    assert response.status_code == 200
    body = response.json()
    assert "Adds two numbers and returns the sum." in body["modules"]["a.py"]
    assert "AI-generated" in body["modules"]["a.py"]


@pytest.mark.asyncio
async def test_dashboard_docs_returns_empty_modules_when_nothing_scanned_yet(pool, monkeypatch):
    await upsert_installation(pool, 704, "octocat")
    await set_installation_plan(pool, 704, "air")
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[704])
    async with client:
        response = await client.get("/app/octocat/hello-world/docs")

    assert response.status_code == 200
    body = response.json()
    assert body["modules"] == {}
    assert body["build_status"] is None


@pytest.mark.asyncio
async def test_dashboard_docs_surfaces_failed_build_status(pool, monkeypatch):
    await upsert_installation(pool, 705, "octocat")
    await set_installation_plan(pool, 705, "air")
    await insert_repo_history(
        pool, 705, "octocat/hello-world", datetime.now(timezone.utc),
        _evidence_with_module("a.py", "add", None),
    )
    await _seed_docs_build_status(pool, 705, "octocat/hello-world", "failed", "model provider unavailable")

    client = await _logged_in_client(pool, monkeypatch, administered_ids=[705])
    async with client:
        response = await client.get("/app/octocat/hello-world/docs")

    assert response.status_code == 200
    body = response.json()
    assert body["build_status"] == "failed"
    assert body["build_error"] == "model provider unavailable"


@pytest.mark.asyncio
async def test_dashboard_docs_export_requires_login(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/app/octocat/hello-world/docs/export")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_dashboard_docs_export_requires_paid_plan(pool, monkeypatch):
    await upsert_installation(pool, 706, "octocat")  # defaults to plan='free'
    await insert_repo_history(
        pool, 706, "octocat/hello-world", datetime.now(timezone.utc),
        _evidence_with_module("a.py", "add", None),
    )
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[706])
    async with client:
        response = await client.get("/app/octocat/hello-world/docs/export")
    assert response.status_code == 402


@pytest.mark.asyncio
async def test_dashboard_docs_export_returns_combined_markdown_with_toc(pool, monkeypatch):
    await upsert_installation(pool, 707, "octocat")
    await set_installation_plan(pool, 707, "air")
    await insert_repo_history(
        pool, 707, "octocat/hello-world", datetime.now(timezone.utc),
        _evidence_with_module("a.py", "add", "Adds two numbers."),
    )
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[707])
    async with client:
        response = await client.get("/app/octocat/hello-world/docs/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["content-disposition"] == 'attachment; filename="hello-world-api-reference.md"'
    body = response.text
    assert "# API Reference — octocat/hello-world" in body
    assert "## Contents" in body
    assert "[a.py](#apy)" in body
    assert "Adds two numbers." in body


@pytest.mark.asyncio
async def test_dashboard_docs_export_handles_no_modules_yet(pool, monkeypatch):
    await upsert_installation(pool, 708, "octocat")
    await set_installation_plan(pool, 708, "air")
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[708])
    async with client:
        response = await client.get("/app/octocat/hello-world/docs/export")

    assert response.status_code == 200
    assert "No public functions or classes found yet." in response.text


@pytest.mark.asyncio
async def test_dashboard_docs_export_sanitizes_unsafe_characters_in_filename(pool, monkeypatch):
    # _repo_installation_id only ever matches `repo` against the org's
    # account_login - it never validates that `repo` is a real, existing
    # repository name - so this route can't assume `repo` is limited to
    # GitHub's own repo-naming charset the way every other route implicitly
    # can. A raw '"' here would otherwise break the Content-Disposition
    # header's quoting.
    await upsert_installation(pool, 709, "octocat")
    await set_installation_plan(pool, 709, "air")
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[709])
    async with client:
        response = await client.get('/app/octocat/weird%22repo/docs/export')

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="weird_repo-api-reference.md"'


def test_fetch_wiki_file_content_sync_calls_a_function_that_actually_exists(monkeypatch):
    # Regression test for docs/audits/Claude_Audit.md finding #23: the real
    # function body called get_github_api_client(), which is never imported
    # in dashboard.py (only _github_http_client is) - a pure NameError on
    # every call. test_dashboard_wiki_file_fetches_unindexed_file_without_llm
    # monkeypatches this whole function out, so it never exercised the real
    # body and the bug shipped invisibly. This test calls the real function.
    import httpx

    from app_server import dashboard

    monkeypatch.setattr(dashboard, "generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr(
        dashboard, "get_installation_token", lambda installation_id, app_jwt, http_client=None: "fake-token"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/octocat/hello-world/contents/config.toml"
        return httpx.Response(
            200,
            json={
                "encoding": "base64",
                "content": "Q29uZmln",  # base64("Config")
            },
        )

    monkeypatch.setattr(
        dashboard,
        "_github_http_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com"),
    )

    result = dashboard._fetch_wiki_file_content_sync(608, "octocat/hello-world", "config.toml")

    assert result == "Config"
