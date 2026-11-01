"""Protect the sign-in and repository picker wiring independently of a live GitHub account."""

import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app_server import auth, dashboard, frontend


@pytest.mark.skipif(shutil.which("node") is None, reason="node is unavailable")
def test_picker_browser_logic_handles_api_and_interaction_states():
    html = frontend._picker_html()
    scripts = re.findall(r"<script>(.*?)</script>", html, re.DOTALL | re.IGNORECASE)
    assert len(scripts) == 1
    harness = Path(__file__).parent / "fixtures" / "picker-dom-harness.cjs"
    result = subprocess.run(
        ["node", str(harness)], input=scripts[0], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
async def test_signin_picker_and_repo_api_keep_session_and_access_boundaries(monkeypatch):
    app = FastAPI()
    app.include_router(frontend.frontend_router)
    app.include_router(dashboard.dashboard_router)
    app.state.db_pool = object()
    secret = "test-session-secret-at-least-32-characters-long"
    monkeypatch.setenv("SESSION_SECRET", secret)
    auth.get_settings.cache_clear()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        signin = await client.get("/", follow_redirects=False)
        assert signin.status_code == 200
        assert signin.headers["cache-control"] == "no-store"
        assert 'href="/auth/login"' in signin.text
        assert "Nothing is scanned until you add a repo" in signin.text
        assert (await client.get("/dashboard", follow_redirects=False)).headers["location"] == "/"
        assert (await client.get("/app/repos")).status_code == 401

    row = {
        "github_login": "alice",
        "github_access_token": auth.encrypt_access_token("test-token", secret),
        "github_refresh_token": None,
    }
    monkeypatch.setattr(auth, "get_session", AsyncMock(side_effect=lambda *_: row.copy()))
    administered = AsyncMock(return_value=[11, 22])
    verify = AsyncMock(return_value=[11])
    list_repos = AsyncMock(return_value=[{"installation_id": 11, "repo_full_name": "alice/ready", "plan": "air"}])
    monkeypatch.setattr(dashboard, "_administered_installation_ids_for_session_or_401", administered)
    monkeypatch.setattr(dashboard, "_verify_installation_ids", verify)
    monkeypatch.setattr(dashboard, "list_repos_for_installations", list_repos)
    monkeypatch.setattr(dashboard, "get_installation", AsyncMock(return_value={"plan": "air"}))
    monkeypatch.setattr(dashboard, "count_monthly_scanned_repos", AsyncMock(return_value=0))
    monkeypatch.setattr(dashboard, "_uninitialized_repos_for_installation", AsyncMock(return_value=[{
        "org": "alice", "repo": "waiting", "repo_full_name": "alice/waiting",
        "plan": "air", "initialized": False, "scan_limit_reached": False,
    }]))
    signed = auth.sign_session_id("test-session", secret)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={auth.SESSION_COOKIE_NAME: signed}) as client:
        assert (await client.get("/", follow_redirects=False)).headers["location"] == "/dashboard"
        picker = await client.get("/dashboard", follow_redirects=False)
        assert picker.status_code == 200
        assert picker.headers["cache-control"] == "no-store"
        assert 'id="picker-search"' in picker.text
        response = await client.get("/app/repos")
        assert response.status_code == 200
        assert [(r["repo"], r["initialized"]) for r in response.json()["repos"]] == [("ready", True), ("waiting", False)]
    verify.assert_awaited_once_with(app.state.db_pool, [11, 22], "alice")
    list_repos.assert_awaited_once_with(app.state.db_pool, [11])
