from datetime import datetime, timezone
import os

import pytest

from app_server.db import (
    get_latest_evidence_for_mcp,
    get_mcp_git_mirror,
    list_mcp_code_embeddings,
)
from scan_worker.db import (
    insert_repo_history,
    upsert_mcp_code_embedding,
    upsert_mcp_git_mirror,
)

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:test@localhost:55433/aletheore_test",
)


async def _insert_installation(pool, installation_id: int, account_login: str, **values) -> None:
    columns = ["installation_id", "account_login", *values.keys()]
    params = [installation_id, account_login, *values.values()]
    placeholders = ", ".join(f"${i}" for i in range(1, len(params) + 1))
    await pool.execute(
        f"INSERT INTO installations ({', '.join(columns)}) VALUES ({placeholders})",
        *params,
    )


@pytest.mark.asyncio
async def test_app_server_get_mcp_git_mirror_evidence_and_embeddings(pool):
    await _insert_installation(pool, 501, "acme", plan="team")
    upsert_mcp_git_mirror(
        TEST_DATABASE_URL,
        501,
        "acme/widgets",
        "/var/aletheore/mirrors/501/acme__widgets",
        "abc",
        100,
    )
    insert_repo_history(
        TEST_DATABASE_URL,
        501,
        "acme/widgets",
        datetime.now(timezone.utc),
        {"repository": {"modules": []}},
    )
    upsert_mcp_code_embedding(
        TEST_DATABASE_URL,
        501,
        "acme/widgets",
        "a.py",
        0,
        "hash-a",
        "def a(): pass",
        [1.0],
    )

    row = get_mcp_git_mirror(TEST_DATABASE_URL, 501, "acme/widgets")
    assert row["local_path"] == "/var/aletheore/mirrors/501/acme__widgets"

    evidence = get_latest_evidence_for_mcp(TEST_DATABASE_URL, 501, "acme/widgets")
    assert evidence == {"repository": {"modules": []}}

    rows = list_mcp_code_embeddings(TEST_DATABASE_URL, 501, "acme/widgets")
    assert rows[0]["file_path"] == "a.py"
