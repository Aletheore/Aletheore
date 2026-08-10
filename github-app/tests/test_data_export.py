import hashlib

import pytest

from app_server.db import list_admin_actions
from test_admin import _logged_in_client


@pytest.mark.asyncio
async def test_export_requires_login(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch)
    async with client:
        client.cookies.clear()
        response = await client.get("/admin/octocat/hello-world/export-data")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_export_rejects_non_administrator(pool, monkeypatch):
    from app_server.db import upsert_installation

    client = await _logged_in_client(pool, monkeypatch, installation_id=100)
    await upsert_installation(pool, 101, "globex")
    async with client:
        response = await client.get("/admin/globex/web/export-data")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_export_works_on_the_free_plan(pool, monkeypatch):
    # Same reasoning as delete-all-data and deletion-preview: exporting
    # your own data isn't a paid feature to unlock.
    client = await _logged_in_client(pool, monkeypatch, plan="free")
    async with client:
        response = await client.get("/admin/octocat/hello-world/export-data")

    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_export_contains_expected_shape_and_findings(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, plan="air")
    async with client:
        response = await client.get("/admin/octocat/hello-world/export-data")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["account_login"] == "octocat"
    assert body["plan"] == "air"
    assert "octocat/hello-world" in body["connected_repos"]
    assert body["latest_findings_by_repo"]["octocat/hello-world"] == {"scanned_at": "x"}
    assert "exported_at" in body
    for key in ("members", "api_tokens", "health_check_targets"):
        assert key in body


@pytest.mark.asyncio
async def test_export_download_headers_name_the_file(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, plan="air")
    async with client:
        response = await client.get("/admin/octocat/hello-world/export-data")

    disposition = response.headers["content-disposition"]
    assert "attachment" in disposition
    assert "octocat" in disposition
    assert disposition.endswith('.json"')


@pytest.mark.asyncio
async def test_export_never_leaks_a_working_api_token_or_its_hash(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, plan="air")
    async with client:
        create_response = await client.post(
            "/admin/octocat/hello-world/tokens", json={"label": "export test token"}
        )
        raw_token = create_response.json()["token"]
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

        export_response = await client.get("/admin/octocat/hello-world/export-data")

    assert export_response.status_code == 200, export_response.text
    body = export_response.json()
    assert len(body["api_tokens"]) == 1
    assert body["api_tokens"][0]["label"] == "export test token"
    raw_text = export_response.text
    assert raw_token not in raw_text
    assert token_hash not in raw_text
    assert "hash" not in body["api_tokens"][0]


@pytest.mark.asyncio
async def test_export_never_leaks_the_webhook_url(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, plan="air")
    secret_url = "https://hooks.slack.com/services/T000/B000/supersecrettoken"
    async with client:
        await client.put("/admin/octocat/hello-world/webhook-url", json={"webhook_url": secret_url})
        export_response = await client.get("/admin/octocat/hello-world/export-data")

    assert secret_url not in export_response.text


@pytest.mark.asyncio
async def test_export_records_an_admin_action(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, plan="air")
    async with client:
        await client.get("/admin/octocat/hello-world/export-data")

    actions = await list_admin_actions(pool, 100)
    matching = [a for a in actions if a["action"] == "data_exported"]
    assert len(matching) == 1
    assert matching[0]["actor_login"] == "octocat"


@pytest.mark.asyncio
async def test_export_health_targets_span_every_connected_repo(pool, monkeypatch):
    from app_server.db import insert_repo_history
    from datetime import datetime, timezone

    client = await _logged_in_client(pool, monkeypatch, plan="air")
    await insert_repo_history(
        pool, 100, "octocat/second-repo", datetime.now(timezone.utc), {"scanned_at": "y"}
    )
    async with client:
        await client.post(
            "/admin/octocat/hello-world/health-targets",
            json={"label": "repo1-target", "base_url": "https://one.example.com"},
        )
        await client.post(
            "/admin/octocat/second-repo/health-targets",
            json={"label": "repo2-target", "base_url": "https://two.example.com"},
        )
        response = await client.get("/admin/octocat/hello-world/export-data")

    body = response.json()
    labels = {t["label"] for t in body["health_check_targets"]}
    assert labels == {"repo1-target", "repo2-target"}
