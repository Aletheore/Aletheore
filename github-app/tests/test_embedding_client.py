import json

import httpx

from scan_worker.embedding_client import MAX_EMBEDDING_CHARS, embed_text


def test_embed_text_returns_vector_on_success(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/embeddings"
        return httpx.Response(200, json={"embedding": [0.1, 0.2, 0.3]})

    monkeypatch.setattr(
        "scan_worker.embedding_client._client",
        lambda base_url=None: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://ollama:11434"),
    )

    result = embed_text("some evidence text")

    assert result == [0.1, 0.2, 0.3]


def test_embed_text_requests_the_models_real_context_window(monkeypatch):
    # Confirmed in production: Ollama's own default (2048) is far smaller
    # than nomic-embed-text's real 8192-token context, and every real
    # embedding call failed against that default - the cache never recorded
    # a single hit despite running for 38 hours.
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"embedding": [0.1]})

    monkeypatch.setattr(
        "scan_worker.embedding_client._client",
        lambda base_url=None: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://ollama:11434"),
    )

    embed_text("some evidence text")

    assert seen["body"]["options"] == {"num_ctx": 8192}


def test_embed_text_truncates_oversized_input(monkeypatch):
    # A real production packet hit 52k+ tokens - even with num_ctx raised to
    # the model's real max, that's still too large for a single embedding
    # call. Truncating keeps the call from failing outright; the exact text
    # match isn't needed for similarity matching, and any cache hit found
    # this way is still re-verified against current evidence before being
    # served.
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["prompt"] = json.loads(request.content)["prompt"]
        return httpx.Response(200, json={"embedding": [0.1]})

    monkeypatch.setattr(
        "scan_worker.embedding_client._client",
        lambda base_url=None: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://ollama:11434"),
    )

    embed_text("x" * (MAX_EMBEDDING_CHARS + 5000))

    assert len(seen["prompt"]) == MAX_EMBEDDING_CHARS


def test_embed_text_uses_explicit_base_url(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"embedding": [0.1]})

    monkeypatch.setattr(
        "scan_worker.embedding_client._client",
        lambda base_url=None: httpx.Client(transport=httpx.MockTransport(handler), base_url=base_url),
    )

    assert embed_text("some evidence text", base_url="http://custom-ollama:11434") == [0.1]
    assert seen["url"].startswith("http://custom-ollama:11434/")


def test_embed_text_returns_none_on_connection_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(
        "scan_worker.embedding_client._client",
        lambda base_url=None: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://ollama:11434"),
    )

    assert embed_text("some evidence text") is None


def test_embed_text_returns_none_on_timeout(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    monkeypatch.setattr(
        "scan_worker.embedding_client._client",
        lambda base_url=None: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://ollama:11434"),
    )

    assert embed_text("some evidence text") is None


def test_embed_text_returns_none_on_malformed_response(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    monkeypatch.setattr(
        "scan_worker.embedding_client._client",
        lambda base_url=None: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://ollama:11434"),
    )

    assert embed_text("some evidence text") is None
