import hashlib
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app_server.db import create_api_token, set_installation_plan, upsert_installation
from app_server.embeddings_api import EMBEDDING_MODEL, MAX_CHARS_PER_TEXT
from app_server.llm_cost import cost_for_usage
from app_server.main import app


def _fake_openai(prompt_tokens: int = 100, dimensions: int = 1536, count: int = 1):
    client = MagicMock()
    response = MagicMock()
    response.data = [MagicMock(embedding=[0.1] * dimensions) for _ in range(count)]
    response.usage = MagicMock(prompt_tokens=prompt_tokens)
    client.embeddings.create.return_value = response
    return client


async def _installation_with_token(pool, installation_id: int, plan: str, token: str) -> None:
    await upsert_installation(pool, installation_id, f"org{installation_id}")
    await set_installation_plan(pool, installation_id, plan)
    await create_api_token(
        pool, installation_id, hashlib.sha256(token.encode()).hexdigest(), "test", "tester"
    )


async def _post(pool, token: str | None, texts: list[str]):
    app.state.db_pool = pool
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/v1/embeddings", json={"texts": texts}, headers=headers)


@pytest.mark.asyncio
async def test_rejects_a_request_with_no_token(pool):
    assert (await _post(pool, None, ["x"])).status_code == 401


@pytest.mark.asyncio
async def test_rejects_an_unknown_token(pool):
    assert (await _post(pool, "not-a-real-token", ["x"])).status_code == 401


@pytest.mark.asyncio
async def test_free_plan_is_refused_with_402_and_told_what_to_do_instead(pool):
    """The gate is the server returning 402, not a check inside the CLI - a
    client-side check in an open-source binary is a suggestion.

    The message names the free alternative, because the free path produces an
    identical index. What a paid plan removes here is a setup step, not a
    capability."""
    await _installation_with_token(pool, 9001, "free", "free-token")

    response = await _post(pool, "free-token", ["x"])

    assert response.status_code == 402
    detail = response.json()["detail"]
    assert "Ollama" in detail and "OPENAI_API_KEY" in detail


@pytest.mark.asyncio
async def test_paid_plan_gets_vectors_and_is_charged(pool):
    """H-4 in the 2026-08-10 audit was LLM spend neither capped nor recorded,
    so the figure shown to the customer understated usage and the cap was
    enforced against an undercount. Billed on the provider's own token count
    so recorded spend matches the invoice."""
    await _installation_with_token(pool, 9002, "air", "paid-token")

    with patch("app_server.embeddings_api.OpenAI", return_value=_fake_openai(prompt_tokens=5000)), \
         patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
        response = await _post(pool, "paid-token", ["x"])

    assert response.status_code == 200
    assert response.json()["model"] == EMBEDDING_MODEL
    assert len(response.json()["vectors"][0]) == 1536

    spent = await pool.fetchval("SELECT total_cost_usd FROM llm_spend WHERE installation_id = 9002")
    assert float(spent) == pytest.approx(cost_for_usage(EMBEDDING_MODEL, 5000, 0))


@pytest.mark.asyncio
async def test_spend_cap_pauses_hosted_embeddings(pool):
    await _installation_with_token(pool, 9003, "air", "capped-token")
    await pool.execute(
        "INSERT INTO llm_spend (installation_id, month, total_cost_usd) "
        "VALUES (9003, date_trunc('month', now())::date, 9999)"
    )

    with patch("app_server.embeddings_api.OpenAI", return_value=_fake_openai()), \
         patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
        response = await _post(pool, "capped-token", ["x"])

    assert response.status_code == 402
    assert "cap" in response.json()["detail"]


@pytest.mark.asyncio
async def test_oversized_text_is_refused(pool):
    await _installation_with_token(pool, 9004, "air", "big-token")

    response = await _post(pool, "big-token", ["x" * (MAX_CHARS_PER_TEXT + 1)])

    assert response.status_code == 413


@pytest.mark.asyncio
async def test_too_many_texts_in_one_request_is_refused(pool):
    await _installation_with_token(pool, 9005, "air", "many-token")

    assert (await _post(pool, "many-token", ["x"] * 1000)).status_code == 422


@pytest.mark.asyncio
async def test_provider_failure_does_not_leak_the_callers_source(pool):
    """The upstream error message can quote the input back, and the input
    here is the caller's own source code."""
    await _installation_with_token(pool, 9006, "air", "fail-token")
    failing = MagicMock()
    failing.embeddings.create.side_effect = RuntimeError(
        "invalid input: def my_secret_function(): api_key = 'sk-real-value'"
    )

    with patch("app_server.embeddings_api.OpenAI", return_value=failing), \
         patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
        response = await _post(pool, "fail-token", ["def my_secret_function(): ..."])

    assert response.status_code == 502
    assert "my_secret_function" not in response.text
    assert "sk-real-value" not in response.text


@pytest.mark.asyncio
async def test_missing_provider_key_is_503_rather_than_a_crash(pool, monkeypatch):
    await _installation_with_token(pool, 9007, "air", "nokey-token")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert (await _post(pool, "nokey-token", ["x"])).status_code == 503


@pytest.mark.asyncio
async def test_the_only_row_written_is_the_spend_row(pool):
    """Chunk text arrives, vectors go back, and the index lives on the
    caller's disk. That is a deliberately smaller promise than "we index your
    code": no retention policy to write, no deletion path to honour, and
    nothing here for a subpoena to reach."""
    await _installation_with_token(pool, 9008, "air", "store-token")
    tables = [
        row["tablename"]
        for row in await pool.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
    ]
    before = {t: await pool.fetchval(f"SELECT count(*) FROM {t}") for t in tables}  # noqa: S608

    with patch("app_server.embeddings_api.OpenAI", return_value=_fake_openai()), \
         patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
        response = await _post(pool, "store-token", ["def totally_unique_marker_9008(): pass"])

    assert response.status_code == 200
    after = {t: await pool.fetchval(f"SELECT count(*) FROM {t}") for t in tables}  # noqa: S608
    grew = {t for t in tables if after[t] != before[t]}
    assert grew == {"llm_spend"}, f"unexpected writes to {grew - {'llm_spend'}}"
