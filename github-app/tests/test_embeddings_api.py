import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app_server.db import create_api_token, set_installation_plan, upsert_installation
from app_server.embeddings_api import EMBEDDING_MODEL, MAX_CHARS_PER_TEXT, get_jina_client
from app_server.main import app


def _fake_jina(dimensions: int = 768, count: int = 1):
    client = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"embeddings": [[0.1] * dimensions for _ in range(count)]}
    client.post.return_value = response
    return client


@pytest.fixture(autouse=True)
def _clear_jina_client_cache():
    get_jina_client.cache_clear()
    yield
    get_jina_client.cache_clear()


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
async def test_paid_plan_gets_jina_vectors_without_external_spend(pool):
    await _installation_with_token(pool, 9002, "air", "paid-token")

    with patch("app_server.embeddings_api.get_jina_client", return_value=_fake_jina(count=1)):
        response = await _post(pool, "paid-token", ["x"])

    assert response.status_code == 200
    assert response.json()["model"] == EMBEDDING_MODEL
    assert len(response.json()["vectors"][0]) == 768

    spent = await pool.fetchval("SELECT count(*) FROM llm_spend WHERE installation_id = 9002")
    assert spent == 0


@pytest.mark.asyncio
async def test_embedding_route_uses_cached_jina_client_factory(pool):
    await _installation_with_token(pool, 9013, "air", "cached-token")
    client = _fake_jina()

    with patch("app_server.embeddings_api.httpx.Client", return_value=client) as jina_factory:
        first = await _post(pool, "cached-token", ["a"])
        second = await _post(pool, "cached-token", ["b"])

    assert first.status_code == 200
    assert second.status_code == 200
    jina_factory.assert_called_once_with(base_url="http://jina-embed:80", timeout=120.0)
    assert client.post.call_count == 2


@pytest.mark.asyncio
async def test_embedding_provider_call_is_offloaded_to_thread(pool):
    await _installation_with_token(pool, 9012, "air", "thread-token")
    client = _fake_jina(count=2)
    offloaded = AsyncMock(return_value=client.post.return_value)

    with patch("app_server.embeddings_api.get_jina_client", return_value=client), \
         patch("app_server.embeddings_api.asyncio.to_thread", offloaded), \
         patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
        response = await _post(pool, "thread-token", ["a", "b"])

    assert response.status_code == 200
    offloaded.assert_awaited_once()
    call = offloaded.await_args
    assert call.args == (client.post, "/embed_batch")
    assert call.kwargs == {"json": {"texts": ["a", "b"]}}


@pytest.mark.asyncio
async def test_hosted_embeddings_do_not_depend_on_external_spend_cap(pool):
    await _installation_with_token(pool, 9003, "air", "capped-token")
    await pool.execute(
        "INSERT INTO llm_spend (installation_id, month, total_cost_usd) "
        "VALUES (9003, date_trunc('month', now())::date, 9999)"
    )

    with patch("app_server.embeddings_api.get_jina_client", return_value=_fake_jina()):
        response = await _post(pool, "capped-token", ["x"])

    assert response.status_code == 200


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
    failing.post.side_effect = RuntimeError(
        "invalid input: def my_secret_function(): api_key = 'sk-real-value'"
    )

    with patch("app_server.embeddings_api.get_jina_client", return_value=failing):
        response = await _post(pool, "fail-token", ["def my_secret_function(): ..."])

    assert response.status_code == 502
    assert "my_secret_function" not in response.text
    assert "sk-real-value" not in response.text


@pytest.mark.asyncio
async def test_jina_provider_failure_is_502_rather_than_a_crash(pool):
    await _installation_with_token(pool, 9007, "air", "nokey-token")
    failing = MagicMock()
    failing.post.side_effect = RuntimeError("source text must not leak")

    with patch("app_server.embeddings_api.get_jina_client", return_value=failing):
        response = await _post(pool, "nokey-token", ["x"])
    assert response.status_code == 502


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

    with patch("app_server.embeddings_api.get_jina_client", return_value=_fake_jina()):
        response = await _post(pool, "store-token", ["def totally_unique_marker_9008(): pass"])

    assert response.status_code == 200
    after = {t: await pool.fetchval(f"SELECT count(*) FROM {t}") for t in tables}  # noqa: S608
    grew = {t for t in tables if after[t] != before[t]}
    assert grew == set(), f"unexpected writes to {grew}"


@pytest.mark.asyncio
async def test_rate_limit_key_includes_repo_id_when_given(pool):
    """`aletheore watch` running against several repos on one token would
    otherwise share a single request budget - the key has to actually carry
    the repo, not just accept the field and ignore it."""
    await _installation_with_token(pool, 9009, "air", "repo-token")

    with patch("app_server.embeddings_api.get_jina_client", return_value=_fake_jina()), \
         patch("app_server.embeddings_api.is_rate_limited", return_value=False) as rl:
        await _post(pool, "repo-token", ["x"], repo_id="repo-abc")

    assert rl.call_args.args[1] == "ratelimit:embeddings:9009:repo-abc"


@pytest.mark.asyncio
async def test_rate_limit_key_falls_back_to_installation_only_without_repo_id(pool):
    """An older CLI that predates repo_id must keep working exactly as
    before - the coarser, shared-per-installation bucket every caller used
    to get."""
    await _installation_with_token(pool, 9010, "air", "norepo-token")

    with patch("app_server.embeddings_api.get_jina_client", return_value=_fake_jina()), \
         patch("app_server.embeddings_api.is_rate_limited", return_value=False) as rl:
        await _post(pool, "norepo-token", ["x"])

    assert rl.call_args.args[1] == "ratelimit:embeddings:9010"


@pytest.mark.asyncio
async def test_hosted_embed_concurrency_cap_returns_429_with_a_short_retry_after(pool):
    """Distinct from the hourly rate limit's 429: jina-embed frees a slot in
    low tens of seconds for a typical batch, not an hour, and the CLI's
    retry loop (embed_texts_hosted) needs a short Retry-After to know it's
    worth retrying rather than giving up."""
    await _installation_with_token(pool, 9014, "air", "saturated-token")

    with patch("app_server.embeddings_api.acquire_concurrency_slot", return_value=False):
        response = await _post(pool, "saturated-token", ["x"])

    assert response.status_code == 429
    assert "capacity" in response.json()["detail"]
    assert int(response.headers["retry-after"]) < 60


@pytest.mark.asyncio
async def test_hosted_embed_releases_its_concurrency_slot_after_a_successful_call(pool):
    await _installation_with_token(pool, 9015, "air", "release-token")

    with patch("app_server.embeddings_api.get_jina_client", return_value=_fake_jina()), \
         patch("app_server.embeddings_api.acquire_concurrency_slot", return_value=True), \
         patch("app_server.embeddings_api.release_concurrency_slot") as release:
        response = await _post(pool, "release-token", ["x"])

    assert response.status_code == 200
    release.assert_called_once()


@pytest.mark.asyncio
async def test_hosted_embed_releases_its_concurrency_slot_even_when_jina_fails(pool):
    """The slot must not leak just because the upstream call failed - the
    finally block is what makes this safe, not the happy path."""
    await _installation_with_token(pool, 9016, "air", "release-on-fail-token")
    failing = MagicMock()
    failing.post.side_effect = RuntimeError("boom")

    with patch("app_server.embeddings_api.get_jina_client", return_value=failing), \
         patch("app_server.embeddings_api.acquire_concurrency_slot", return_value=True), \
         patch("app_server.embeddings_api.release_concurrency_slot") as release:
        response = await _post(pool, "release-on-fail-token", ["x"])

    assert response.status_code == 502
    release.assert_called_once()


@pytest.mark.asyncio
async def test_hosted_embed_concurrency_check_fails_open_on_redis_error(pool):
    """Matches the rate limiter's own fail-open policy: jina-embed's
    per-instance locks are the hard backstop against real overload, this is
    only the soft admission control - a Redis outage should cost that
    smoothing, not availability."""
    await _installation_with_token(pool, 9017, "air", "redis-down-token")

    with patch("app_server.embeddings_api.get_jina_client", return_value=_fake_jina()), \
         patch(
             "app_server.embeddings_api.acquire_concurrency_slot",
             side_effect=RuntimeError("redis unreachable"),
         ):
        response = await _post(pool, "redis-down-token", ["x"])

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_one_repo_being_rate_limited_does_not_block_another_repo_on_the_same_token(pool):
    """The actual point of per-repo keying: one repo's rebase-heavy burst
    hitting its own limit must not starve a second repo watched on the same
    installation token."""
    await _installation_with_token(pool, 9011, "air", "multi-repo-token")

    def fake_rate_limited(redis_conn, key, limit, window):  # noqa: ANN001
        return "repo-a" in key

    with patch("app_server.embeddings_api.get_jina_client", return_value=_fake_jina()), \
         patch("app_server.embeddings_api.is_rate_limited", side_effect=fake_rate_limited):
        blocked = await _post(pool, "multi-repo-token", ["x"], repo_id="repo-a")
        allowed = await _post(pool, "multi-repo-token", ["x"], repo_id="repo-b")

    assert blocked.status_code == 429
    assert allowed.status_code == 200
