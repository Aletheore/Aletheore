from datetime import datetime, timezone

import pytest

from app_server.db import (
    add_installation_member,
    insert_repo_history,
    list_admin_actions,
    record_admin_action,
    upsert_installation,
)
from test_admin import _logged_in_client


async def _seed_installation(pool, installation_id, account_login, repo_full_name):
    await upsert_installation(pool, installation_id, account_login)
    await insert_repo_history(
        pool, installation_id, repo_full_name, datetime.now(timezone.utc), {"scanned_at": "x"}
    )


@pytest.mark.asyncio
async def test_record_admin_action_stores_detail_as_jsonb(pool):
    await _seed_installation(pool, 950, "acme", "acme/api")

    await record_admin_action(pool, 950, "octocat", "member_added", {"github_login": "friend"})

    actions = await list_admin_actions(pool, 950)
    assert len(actions) == 1
    assert actions[0]["actor_login"] == "octocat"
    assert actions[0]["action"] == "member_added"
    assert actions[0]["detail"] == {"github_login": "friend"}


@pytest.mark.asyncio
async def test_record_admin_action_allows_no_detail(pool):
    await _seed_installation(pool, 951, "acme", "acme/api")

    await record_admin_action(pool, 951, "octocat", "data_exported")

    actions = await list_admin_actions(pool, 951)
    assert actions[0]["detail"] is None


@pytest.mark.asyncio
async def test_list_admin_actions_orders_newest_first(pool):
    await _seed_installation(pool, 952, "acme", "acme/api")

    await record_admin_action(pool, 952, "octocat", "first_action")
    await record_admin_action(pool, 952, "octocat", "second_action")

    actions = await list_admin_actions(pool, 952)
    assert [a["action"] for a in actions] == ["second_action", "first_action"]


@pytest.mark.asyncio
async def test_admin_action_log_cascades_with_installation(pool):
    await _seed_installation(pool, 953, "acme", "acme/api")
    await record_admin_action(pool, 953, "octocat", "member_added")

    await pool.execute("DELETE FROM installations WHERE installation_id = 953")

    remaining = await pool.fetchval(
        "SELECT count(*) FROM admin_action_log WHERE installation_id = 953"
    )
    assert remaining == 0


@pytest.mark.asyncio
async def test_add_member_route_records_admin_action(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, plan="air")
    async with client:
        response = await client.post(
            "/admin/octocat/hello-world/members", json={"github_login": "newteammate"}
        )
    assert response.status_code == 200, response.text

    actions = await list_admin_actions(pool, 100)
    matching = [a for a in actions if a["action"] == "member_added"]
    assert len(matching) == 1
    assert matching[0]["detail"] == {"github_login": "newteammate"}
    assert matching[0]["actor_login"] == "octocat"


@pytest.mark.asyncio
async def test_remove_member_route_records_admin_action(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, plan="air")
    # octocat must be seated first, or _require_seat_if_paid's
    # first-admin-auto-seat never triggers for them (someone else already
    # has a seat), and their own request 403s before reaching the route.
    await add_installation_member(pool, 100, "octocat", "octocat")
    await add_installation_member(pool, 100, "leaving-member", "octocat")
    async with client:
        response = await client.delete("/admin/octocat/hello-world/members/leaving-member")
    assert response.status_code == 200, response.text

    actions = await list_admin_actions(pool, 100)
    matching = [a for a in actions if a["action"] == "member_removed"]
    assert len(matching) == 1
    assert matching[0]["detail"] == {"github_login": "leaving-member"}


@pytest.mark.asyncio
async def test_generate_token_route_records_admin_action_without_the_raw_token(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, plan="air")
    async with client:
        response = await client.post(
            "/admin/octocat/hello-world/tokens", json={"label": "CI token"}
        )
    assert response.status_code == 200, response.text
    raw_token = response.json()["token"]

    actions = await list_admin_actions(pool, 100)
    matching = [a for a in actions if a["action"] == "api_token_created"]
    assert len(matching) == 1
    assert matching[0]["detail"]["label"] == "CI token"
    # The whole point: the audit trail must never carry a working credential.
    assert raw_token not in str(matching[0]["detail"])


@pytest.mark.asyncio
async def test_revoke_token_route_records_admin_action(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, plan="air")
    async with client:
        create_response = await client.post(
            "/admin/octocat/hello-world/tokens", json={"label": "to revoke"}
        )
        token_id = create_response.json()["id"]
        response = await client.delete(f"/admin/octocat/hello-world/tokens/{token_id}")
    assert response.status_code == 200, response.text

    actions = await list_admin_actions(pool, 100)
    matching = [a for a in actions if a["action"] == "api_token_revoked"]
    assert len(matching) == 1
    assert matching[0]["detail"] == {"token_id": token_id}


@pytest.mark.asyncio
async def test_set_webhook_url_route_records_action_without_the_url_itself(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, plan="air")
    secret_url = "https://hooks.slack.com/services/T000/B000/superrsecrettoken"
    async with client:
        response = await client.put(
            "/admin/octocat/hello-world/webhook-url", json={"webhook_url": secret_url}
        )
    assert response.status_code == 200, response.text

    actions = await list_admin_actions(pool, 100)
    matching = [a for a in actions if a["action"] == "webhook_url_changed"]
    assert len(matching) == 1
    # A Slack-style webhook URL embeds a secret in its path - it must never
    # land in an audit log entry.
    assert secret_url not in str(matching[0]["detail"])
    assert matching[0]["detail"] == {"cleared": False}


@pytest.mark.asyncio
async def test_set_llm_suggestions_route_records_admin_action(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, plan="air")
    async with client:
        response = await client.put(
            "/admin/octocat/hello-world/llm-suggestions", json={"enabled": False}
        )
    assert response.status_code == 200, response.text

    actions = await list_admin_actions(pool, 100)
    matching = [a for a in actions if a["action"] == "llm_suggestions_setting_changed"]
    assert len(matching) == 1
    assert matching[0]["detail"] == {"enabled": False}


@pytest.mark.asyncio
async def test_add_and_remove_health_target_routes_record_admin_actions(pool, monkeypatch):
    client = await _logged_in_client(pool, monkeypatch, plan="air")
    async with client:
        create_response = await client.post(
            "/admin/octocat/hello-world/health-targets",
            json={"label": "prod", "base_url": "https://app.example.com"},
        )
        target_id = create_response.json()["id"]
        remove_response = await client.delete(
            f"/admin/octocat/hello-world/health-targets/{target_id}"
        )
    assert create_response.status_code == 200, create_response.text
    assert remove_response.status_code == 200, remove_response.text

    actions = await list_admin_actions(pool, 100)
    added = [a for a in actions if a["action"] == "health_check_target_added"]
    removed = [a for a in actions if a["action"] == "health_check_target_removed"]
    assert len(added) == 1
    assert added[0]["detail"]["label"] == "prod"
    assert len(removed) == 1
    assert removed[0]["detail"]["target_id"] == target_id
