import importlib
import math

import httpx
import pytest

from app_server import hosted_embedder as he


def _response(status, vectors=None, order=None):
    data = [{"index": i, "embedding": v} for i, v in enumerate(vectors or [])]
    if order:
        data = [data[i] for i in order]
    return httpx.Response(status, json={"data": data}, request=httpx.Request("POST", he.DEEPINFRA_EMBEDDINGS_URL))


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

    vectors = he.embed_batch(["a", "b"])

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

    vectors = he.embed_batch(texts)

    assert max(calls) <= he.MAX_TEXTS_PER_CALL and len(calls) == 3
    assert [int(round(v[0] * int(t))) if int(t) else 0 for v, t in zip(vectors, texts)] == [int(t) for t in texts]


def test_retryable_status_is_retried_then_succeeds(monkeypatch):
    results = iter([_response(429), _response(503), _response(200, [[1.0]])])
    monkeypatch.setattr(he.httpx, "post", lambda *a, **k: next(results))

    assert he.embed_batch(["x"]) == [[1.0]]


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
