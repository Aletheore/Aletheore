import importlib
import math

import httpx
import pytest

from app_server import hosted_embedder as he


def _response(status, vectors=None, order=None, usage="default"):
    data = [{"index": i, "embedding": v} for i, v in enumerate(vectors or [])]
    if order:
        data = [data[i] for i in order]
    body = {"data": data}
    if usage == "default":
        body["usage"] = {"prompt_tokens": 7, "total_tokens": 7}
    elif usage is not None:
        body["usage"] = usage
    return httpx.Response(status, json=body, request=httpx.Request("POST", he.DEEPINFRA_EMBEDDINGS_URL))


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("DEEPINFRA_API_KEY", "test-key-not-real")
    monkeypatch.delenv("DEEPINFRA_EMBED_MODEL", raising=False)


def test_provider_defaults_to_local_and_rejects_unknown_values(monkeypatch):
    monkeypatch.delenv("EMBED_PROVIDER", raising=False)
    assert he.provider() == "local"
    monkeypatch.setenv("EMBED_PROVIDER", "DeepInfra")
    assert he.provider() == "deepinfra"
    monkeypatch.setenv("EMBED_PROVIDER", "openai")
    assert he.provider() == "local"


def test_vectors_come_back_in_input_order_and_unit_length(monkeypatch):
    monkeypatch.setattr(he.httpx, "post", lambda *a, **k: _response(200, [[3.0, 4.0], [0.0, 2.0]], order=[1, 0]))

    vectors, tokens = he.embed_batch(["a", "b"])

    assert vectors[0] == pytest.approx([0.6, 0.8])
    assert vectors[1] == pytest.approx([0.0, 1.0])
    assert all(math.isclose(sum(v * v for v in vec), 1.0) for vec in vectors)


def test_request_carries_model_input_and_bearer_key(monkeypatch):
    seen = {}

    def fake_post(url, headers, json, timeout):
        seen.update(url=url, headers=headers, json=json)
        return _response(200, [[1.0]])

    monkeypatch.setattr(he.httpx, "post", fake_post)
    he.embed_batch(["x"])

    assert seen["url"] == he.DEEPINFRA_EMBEDDINGS_URL
    assert seen["headers"] == {"Authorization": "Bearer test-key-not-real"}
    assert seen["json"] == {"model": he.DEFAULT_DEEPINFRA_MODEL, "input": ["x"], "encoding_format": "float"}


def test_large_input_is_split_into_bounded_calls_and_reassembled_in_order(monkeypatch):
    calls = []

    def fake_post(url, headers, json, timeout):
        calls.append(len(json["input"]))
        return _response(200, [[float(int(t))] for t in json["input"]])

    monkeypatch.setattr(he.httpx, "post", fake_post)
    texts = [str(i) for i in range(he.MAX_TEXTS_PER_CALL * 2 + 5)]

    vectors, tokens = he.embed_batch(texts)

    assert max(calls) <= he.MAX_TEXTS_PER_CALL and len(calls) == 3
    assert tokens == 7 * 3  # summed across the three calls
    assert [int(round(v[0] * int(t))) if int(t) else 0 for v, t in zip(vectors, texts)] == [int(t) for t in texts]


def test_retryable_status_is_retried_with_backoff_then_succeeds(monkeypatch):
    results = iter([_response(429), _response(503), _response(200, [[1.0]])])
    monkeypatch.setattr(he.httpx, "post", lambda *a, **k: next(results))
    slept = []
    monkeypatch.setattr(he.time, "sleep", slept.append)

    assert he.embed_batch(["x"]) == ([[1.0]], 7)
    assert slept == [0.5, 1.0]  # waits before each retry, doubling, and not before the first attempt


def test_non_retryable_status_fails_immediately_without_leaking_the_body(monkeypatch):
    attempts = []

    def fake_post(*a, **k):
        attempts.append(1)
        return httpx.Response(400, json={"error": "bad input: SECRET SOURCE LINE"}, request=httpx.Request("POST", "x"))

    monkeypatch.setattr(he.httpx, "post", fake_post)

    with pytest.raises(he.EmbedderUnavailable) as err:
        he.embed_batch(["x"])

    assert len(attempts) == 1
    assert "SECRET" not in str(err.value)


def test_missing_key_is_unavailable_not_a_crash(monkeypatch):
    monkeypatch.delenv("DEEPINFRA_API_KEY")
    with pytest.raises(he.EmbedderUnavailable):
        he.embed_batch(["x"])


def test_wrong_vector_count_is_rejected(monkeypatch):
    monkeypatch.setattr(he.httpx, "post", lambda *a, **k: _response(200, [[1.0]]))
    with pytest.raises(he.EmbedderUnavailable):
        he.embed_batch(["a", "b"])


def test_embed_text_uses_a_distinct_cache_tag_and_degrades_to_none(monkeypatch):
    monkeypatch.setenv("EMBED_PROVIDER", "deepinfra")
    import scan_worker.embedding_client as ec

    try:
        ec = importlib.reload(ec)
        assert ec.CURRENT_EMBEDDER == f"deepinfra:{he.DEFAULT_DEEPINFRA_MODEL}"
        assert ec.CURRENT_EMBEDDER != ec.LOCAL_EMBEDDER

        monkeypatch.setattr(he.httpx, "post", lambda *a, **k: _response(200, [[3.0, 4.0]]))
        assert ec.embed_text("diff") == pytest.approx([0.6, 0.8])

        monkeypatch.setattr(he.httpx, "post", lambda *a, **k: _response(500))
        assert ec.embed_text("diff") is None
    finally:
        monkeypatch.delenv("EMBED_PROVIDER")
        importlib.reload(ec)
        assert ec.CURRENT_EMBEDDER == ec.LOCAL_EMBEDDER


def test_tokens_come_from_the_providers_usage_and_fall_back_to_a_generous_estimate(monkeypatch):
    monkeypatch.setattr(he.httpx, "post", lambda *a, **k: _response(200, [[1.0]], usage={"prompt_tokens": 123}))
    assert he.embed_batch(["x"])[1] == 123

    monkeypatch.setattr(he.httpx, "post", lambda *a, **k: _response(200, [[1.0]], usage=None))
    _, estimated = he.embed_batch(["y" * 250])
    assert estimated == 84  # 250 chars / 3.0, rounded up


def test_embed_text_reports_tokens_only_inside_a_metering_block(monkeypatch):
    monkeypatch.setenv("EMBED_PROVIDER", "deepinfra")
    import scan_worker.embedding_client as ec
    import scan_worker.embedding_spend as spend

    charged = []
    monkeypatch.setattr(spend, "charge_embedding_tokens", lambda *args: charged.append(args))
    try:
        ec = importlib.reload(ec)
        monkeypatch.setattr(he.httpx, "post", lambda *a, **k: _response(200, [[3.0, 4.0]]))

        ec.embed_text("outside any block")
        assert charged == []

        with ec.metered_embedding("dsn", 42, "flash_review_embedding"):
            ec.embed_text("one")
            ec.embed_text("two")
        assert charged == [("dsn", 42, 14, "flash_review_embedding")]

        # A block whose embeddings were all served from the in-memory vector cache charges nothing.
        with ec.metered_embedding("dsn", 42, "flash_review_embedding"):
            pass
        assert len(charged) == 1
    finally:
        monkeypatch.delenv("EMBED_PROVIDER")
        importlib.reload(ec)


def test_charge_draws_credit_ledgers_it_and_never_raises(monkeypatch):
    import scan_worker.embedding_spend as spend

    calls = []
    monkeypatch.setattr(spend, "reserve_llm_spend", lambda dsn, iid, amount: calls.append(("reserve", amount)) or True)
    monkeypatch.setattr(spend, "record_llm_spend", lambda dsn, iid, cost, feature: calls.append(("record", cost, feature)))

    charged = spend.charge_embedding_tokens("dsn", 1, 1_000_000, "hosted_embeddings")

    assert charged == pytest.approx(0.01)  # $0.01 per million tokens
    assert calls == [("reserve", pytest.approx(0.01)), ("record", pytest.approx(0.01), "hosted_embeddings")]
    assert spend.charge_embedding_tokens("dsn", 1, 0, "hosted_embeddings") == 0.0

    def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(spend, "reserve_llm_spend", boom)
    assert spend.charge_embedding_tokens("dsn", 1, 10, "hosted_embeddings") == 0.0


def test_charge_drains_what_is_left_when_the_balance_cannot_cover_the_cost(monkeypatch):
    import scan_worker.embedding_spend as spend

    reserved = []

    def reserve(dsn, iid, amount):
        reserved.append(amount)
        return amount <= 0.002  # the full cost does not fit, the remainder does

    monkeypatch.setattr(spend, "reserve_llm_spend", reserve)
    monkeypatch.setattr(spend, "get_installation", lambda dsn, iid: {"base_credit_remaining_usd": 0.001, "topup_credit_balance_usd": 0.0005})
    monkeypatch.setattr(spend, "record_llm_spend", lambda *a, **k: None)

    spend.charge_embedding_tokens("dsn", 1, 1_000_000, "hosted_embeddings")

    assert reserved == [pytest.approx(0.01), pytest.approx(0.0015)]


def test_a_single_oversized_text_is_cut_to_the_per_call_bound(monkeypatch):
    seen = {}

    def fake_post(url, headers, json, timeout):
        seen["sizes"] = [len(t) for t in json["input"]]
        return _response(200, [[1.0]])

    monkeypatch.setattr(he.httpx, "post", fake_post)
    he.embed_batch(["z" * (he.MAX_CHARS_PER_CALL + 5000)])

    assert seen["sizes"] == [he.MAX_CHARS_PER_CALL]


def test_charge_never_raises_even_if_pricing_itself_fails(monkeypatch):
    import scan_worker.embedding_spend as spend

    def boom(*args, **kwargs):
        raise KeyError("model missing from the rate table")

    monkeypatch.setattr(spend, "cost_for_usage", boom)
    assert spend.charge_embedding_tokens("dsn", 1, 10, "hosted_embeddings") == 0.0
