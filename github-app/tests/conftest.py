import os

import asyncpg
import pytest
import pytest_asyncio

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:test@localhost:55433/aletheore_test",
)

os.environ.setdefault("DATABASE_URL", TEST_DATABASE_URL)
os.environ.setdefault("GITHUB_APP_ID", "12345")
os.environ.setdefault("GITHUB_APP_PRIVATE_KEY", "test-private-key")
os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-webhook-secret")
os.environ.setdefault("GITHUB_CLIENT_ID", "test-client-id")
os.environ.setdefault("GITHUB_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://test")


@pytest_asyncio.fixture
async def pool():
    try:
        p = await asyncpg.create_pool(TEST_DATABASE_URL)
    except OSError as exc:
        pytest.skip(f"test Postgres unavailable: {exc}")
    async with p.acquire() as conn:
        await conn.execute("TRUNCATE installations, sessions CASCADE")
    yield p
    await p.close()
