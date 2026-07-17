import pytest

from app_server.db import get_installation, upsert_installation
from app_server.webhooks.installation import handle_installation_event


@pytest.mark.asyncio
async def test_installation_created_upserts_row(pool):
    payload = {
        "action": "created",
        "installation": {"id": 555, "account": {"login": "octocat"}},
    }
    await handle_installation_event("installation", payload, pool)
    row = await get_installation(pool, 555)
    assert row["account_login"] == "octocat"


@pytest.mark.asyncio
async def test_installation_deleted_removes_row(pool):
    await upsert_installation(pool, 555, "octocat")
    payload = {
        "action": "deleted",
        "installation": {"id": 555, "account": {"login": "octocat"}},
    }
    await handle_installation_event("installation", payload, pool)
    assert await get_installation(pool, 555) is None


@pytest.mark.asyncio
async def test_installation_repositories_added_upserts_row(pool):
    payload = {
        "action": "added",
        "installation": {"id": 556, "account": {"login": "someorg"}},
    }
    await handle_installation_event("installation_repositories", payload, pool)
    row = await get_installation(pool, 556)
    assert row["account_login"] == "someorg"
