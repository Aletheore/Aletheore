from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app_server.auth import encrypt_access_token, sign_session_id
from app_server.db import create_session, insert_repo_history, set_installation_plan, upsert_installation
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
    await upsert_installation(pool, 702, "another-org")
    await set_installation_plan(pool, 702, "pro")
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
    assert by_name["octocat/hello-world"]["plan"] == "free"
    assert by_name["another-org/service-b"]["plan"] == "pro"


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
async def test_dashboard_rejects_unadministered_installation(pool, monkeypatch):
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
    # this is the exact cross-tenant case the fix closes.
    client = await _logged_in_client(pool, monkeypatch, administered_ids=[999])
    async with client:
        response = await client.get("/app/octocat/hello-world")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_dashboard_returns_data_for_known_repo(pool, monkeypatch):
    await upsert_installation(pool, 1, "octocat")
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
    endpoints = {(endpoint["method"], endpoint["path"]): endpoint for endpoint in body["endpoints"]}
    assert len(endpoints) == 2
    assert endpoints[("GET", "/api/users")]["latency_ms"] == 88.0
    assert endpoints[("GET", "/api/orders")]["reachable"] is False
    assert endpoints[("GET", "/api/orders")]["status_code"] is None
    for endpoint in endpoints.values():
        assert set(endpoint.keys()) == {
            "method",
            "path",
            "reachable",
            "status_code",
            "latency_ms",
            "checked_at",
        }


@pytest.mark.asyncio
async def test_public_health_404s_with_no_data(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/health/octocat/no-such-repo")
    assert response.status_code == 404
    assert response.headers["access-control-allow-origin"] == "*"


@pytest.mark.asyncio
async def test_dashboard_health_requires_login(pool):
    app.state.db_pool = pool
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/app/octocat/hello-world/health")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_dashboard_health_rejects_unadministered_installation(pool, monkeypatch):
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
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_dashboard_health_includes_evidence_resolution(pool, monkeypatch):
    await upsert_installation(pool, 502, "octocat")
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
    await set_installation_plan(pool, 602, "pro")
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
    await set_installation_plan(pool, 603, "pro")
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
    await set_installation_plan(pool, 604, "pro")
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
async def test_dashboard_wiki_subsystem_404s_for_unknown_id(pool, monkeypatch):
    await upsert_installation(pool, 605, "octocat")
    await set_installation_plan(pool, 605, "pro")
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
