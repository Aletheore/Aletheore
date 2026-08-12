import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app_server.db import create_api_token, set_installation_plan, upsert_installation
from app_server.embeddings_api import EMBEDDING_MODEL, MAX_CHARS_PER_TEXT, get_openai_client
from app_server.llm_cost import cost_for_usage
from app_server.main import app


def _fake_openai(prompt_tokens: int = 100, dimensions: int = 1536, count: int = 1):
    client = MagicMock()
    response = MagicMock()
    response.data = [MagicMock(embedding=[0.1] * dimensions) for _ in range(count)]
    response.usage = MagicMock(prompt_tokens=prompt_tokens)
    client.embeddings.create.return_value = response
    return client


@pytest.fixture(autouse=True)
def _clear_openai_client_cache():
    get_openai_client.cache_clear()
    yield
    get_openai_client.cache_clear()


async def _installation_with_token(pool, installation_id: int, plan: str, token: str) -> None:
    await upsert_installation(pool, installation_id, f"org{installation_id}")
    await set_installation_plan(pool, installation_id, plan)
    await create_api_token(
        pool, installation_id, hashlib.sha256(token.encode()).hexdigest(), "test", "tester"
    )


async def _post(pool, token: str | None, texts: list[str], repo_id: str | None = None):
    app.state.db_pool = pool
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    body = {"texts": texts}
    if repo_id is not None:
        body["repo_id"] = repo_id
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/v1/embeddings", json=body, headers=headers)


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
async def test_embedding_route_uses_cached_openai_client_factory(pool):
    await _installation_with_token(pool, 9013, "air", "cached-token")
    client = _fake_openai(prompt_tokens=250)

    with patch("app_server.embeddings_api.OpenAI", return_value=client) as openai_class, \
         patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
        first = await _post(pool, "cached-token", ["a"])
        second = await _post(pool, "cached-token", ["b"])

    assert first.status_code == 200
    assert second.status_code == 200
    openai_class.assert_called_once_with(api_key="sk-test")


@pytest.mark.asyncio
async def test_embedding_provider_call_is_offloaded_to_thread(pool):
    await _installation_with_token(pool, 9012, "air", "thread-token")
    client = _fake_openai(prompt_tokens=250, count=2)
    offloaded = AsyncMock(return_value=client.embeddings.create.return_value)

    with patch("app_server.embeddings_api.OpenAI", return_value=client), \
         patch("app_server.embeddings_api.asyncio.to_thread", offloaded), \
         patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
        response = await _post(pool, "thread-token", ["a", "b"])

    assert response.status_code == 200
    offloaded.assert_awaited_once()
    call = offloaded.await_args
    assert call.args == (client.embeddings.create,)
    assert call.kwargs == {"model": EMBEDDING_MODEL, "input": ["a", "b"]}


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


@pytest.mark.asyncio
async def test_rate_limit_key_includes_repo_id_when_given(pool):
    """`aletheore watch` running against several repos on one token would
    otherwise share a single request budget - the key has to actually carry
    the repo, not just accept the field and ignore it."""
    await _installation_with_token(pool, 9009, "air", "repo-token")

    with patch("app_server.embeddings_api.OpenAI", return_value=_fake_openai()), \
         patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}), \
         patch("app_server.embeddings_api.is_rate_limited", return_value=False) as rl:
        await _post(pool, "repo-token", ["x"], repo_id="repo-abc")

    assert rl.call_args.args[1] == "ratelimit:embeddings:9009:repo-abc"


@pytest.mark.asyncio
async def test_rate_limit_key_falls_back_to_installation_only_without_repo_id(pool):
    """An older CLI that predates repo_id must keep working exactly as
    before - the coarser, shared-per-installation bucket every caller used
    to get."""
    await _installation_with_token(pool, 9010, "air", "norepo-token")

    with patch("app_server.embeddings_api.OpenAI", return_value=_fake_openai()), \
         patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}), \
         patch("app_server.embeddings_api.is_rate_limited", return_value=False) as rl:
        await _post(pool, "norepo-token", ["x"])

    assert rl.call_args.args[1] == "ratelimit:embeddings:9010"


@pytest.mark.asyncio
async def test_one_repo_being_rate_limited_does_not_block_another_repo_on_the_same_token(pool):
    """The actual point of per-repo keying: one repo's rebase-heavy burst
    hitting its own limit must not starve a second repo watched on the same
    installation token."""
    await _installation_with_token(pool, 9011, "air", "multi-repo-token")

    def fake_rate_limited(redis_conn, key, limit, window):  # noqa: ANN001
        return "repo-a" in key

    with patch("app_server.embeddings_api.OpenAI", return_value=_fake_openai()), \
         patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}), \
         patch("app_server.embeddings_api.is_rate_limited", side_effect=fake_rate_limited):
        blocked = await _post(pool, "multi-repo-token", ["x"], repo_id="repo-a")
        allowed = await _post(pool, "multi-repo-token", ["x"], repo_id="repo-b")

    assert blocked.status_code == 429
    assert allowed.status_code == 200
