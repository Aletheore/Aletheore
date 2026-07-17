from datetime import datetime, timedelta, timezone

import pytest

from app_server.db import (
    count_active_tokens,
    create_api_token,
    create_session,
    delete_installation,
    delete_session,
    get_installation_by_token_hash,
    get_installation,
    get_recent_history,
    get_max_tokens,
    get_session,
    insert_repo_history,
    list_api_tokens,
    revoke_api_token,
    set_installation_plan,
    set_webhook_url,
    touch_api_token,
    upsert_installation,
)


@pytest.mark.asyncio
async def test_upsert_installation_creates_row(pool):
    await upsert_installation(pool, 123, "octocat")
    row = await get_installation(pool, 123)
    assert row["account_login"] == "octocat"
    assert row["plan"] == "free"


@pytest.mark.asyncio
async def test_upsert_installation_is_idempotent(pool):
    await upsert_installation(pool, 123, "octocat")
    await upsert_installation(pool, 123, "octocat")
    row = await get_installation(pool, 123)
    assert row["account_login"] == "octocat"


@pytest.mark.asyncio
async def test_set_installation_plan_updates_plan(pool):
    await upsert_installation(pool, 123, "octocat")
    await set_installation_plan(pool, 123, "pro")
    row = await get_installation(pool, 123)
    assert row["plan"] == "pro"


@pytest.mark.asyncio
async def test_delete_installation_removes_row(pool):
    await upsert_installation(pool, 123, "octocat")
    await delete_installation(pool, 123)
    assert await get_installation(pool, 123) is None


@pytest.mark.asyncio
async def test_delete_installation_cascades_to_history(pool):
    await upsert_installation(pool, 123, "octocat")
    await insert_repo_history(pool, 123, "octocat/repo", datetime.now(timezone.utc), {"x": 1})
    await delete_installation(pool, 123)
    assert await get_recent_history(pool, 123, "octocat/repo") == []


@pytest.mark.asyncio
async def test_repo_history_rotation_keeps_only_20(pool):
    await upsert_installation(pool, 123, "octocat")
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(21):
        await insert_repo_history(pool, 123, "octocat/repo", start + timedelta(minutes=i), {"n": i})

    history = await get_recent_history(pool, 123, "octocat/repo", limit=100)
    assert len(history) == 20
    assert history[0]["evidence"]["n"] == 20
    assert history[-1]["evidence"]["n"] == 1


@pytest.mark.asyncio
async def test_session_lifecycle(pool):
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    await create_session(pool, "sess-1", 42, "octocat", "encrypted", expires)
    row = await get_session(pool, "sess-1")
    assert row["github_login"] == "octocat"
    await delete_session(pool, "sess-1")
    assert await get_session(pool, "sess-1") is None


@pytest.mark.asyncio
async def test_webhook_url_and_token_lifecycle(pool):
    await upsert_installation(pool, 100, "octocat")
    await set_webhook_url(pool, 100, "https://hooks.slack.com/services/x")
    installation = await get_installation(pool, 100)
    assert installation["webhook_url"] == "https://hooks.slack.com/services/x"
    assert await get_max_tokens(pool, 100) == 3

    await create_api_token(pool, 100, "hash1", "laptop", "octocat")
    assert await count_active_tokens(pool, 100) == 1
    assert (await get_installation_by_token_hash(pool, "hash1"))["installation_id"] == 100
    await touch_api_token(pool, "hash1")
    tokens = await list_api_tokens(pool, 100)
    assert tokens[0]["last_used_at"] is not None
    assert "token_hash" not in tokens[0]
    await revoke_api_token(pool, 100, tokens[0]["id"])
    assert await count_active_tokens(pool, 100) == 0
    assert await get_installation_by_token_hash(pool, "hash1") is None
