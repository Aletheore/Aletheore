import json

import httpx

from scan_worker.embedding_client import MAX_EMBEDDING_CHARS, _client, embed_text


def test_embed_text_returns_vector_on_success(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/embed"
        return httpx.Response(200, json={"embedding": [0.1, 0.2, 0.3]})

    monkeypatch.setattr(
        "scan_worker.embedding_client._client",
        lambda base_url=None: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://jina-embed:80"),
    )

    result = embed_text("some evidence text")

    assert result == [0.1, 0.2, 0.3]


def test_embed_text_sends_the_text_field(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"embedding": [0.1]})

    monkeypatch.setattr(
        "scan_worker.embedding_client._client",
        lambda base_url=None: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://jina-embed:80"),
    )

    embed_text("some evidence text")

    assert seen["body"] == {"text": "some evidence text"}


def test_embed_text_truncates_oversized_input(monkeypatch):
    # A real production packet hit 52k+ tokens - nowhere close to fitting
    # the model's real context. Truncating keeps the call from failing
    # outright; the exact text match isn't needed for similarity matching,
    # and any cache hit found this way is still re-verified against
    # current evidence before being served.
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["text"] = json.loads(request.content)["text"]
        return httpx.Response(200, json={"embedding": [0.1]})

    monkeypatch.setattr(
        "scan_worker.embedding_client._client",
        lambda base_url=None: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://jina-embed:80"),
    )

    embed_text("x" * (MAX_EMBEDDING_CHARS + 5000))

    assert len(seen["text"]) == MAX_EMBEDDING_CHARS


def test_embed_text_uses_explicit_base_url(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"embedding": [0.1]})

    monkeypatch.setattr(
        "scan_worker.embedding_client._client",
        lambda base_url=None: httpx.Client(transport=httpx.MockTransport(handler), base_url=base_url),
    )

    assert embed_text("some evidence text", base_url="http://custom-jina-embed:9999") == [0.1]
    assert seen["url"].startswith("http://custom-jina-embed:9999/")


def test_embed_text_returns_none_on_connection_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(
        "scan_worker.embedding_client._client",
        lambda base_url=None: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://jina-embed:80"),
    )

    assert embed_text("some evidence text") is None


def test_embed_text_returns_none_on_timeout(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    monkeypatch.setattr(
        "scan_worker.embedding_client._client",
        lambda base_url=None: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://jina-embed:80"),
    )

    assert embed_text("some evidence text") is None


def test_embed_text_returns_none_on_malformed_response(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    monkeypatch.setattr(
        "scan_worker.embedding_client._client",
        lambda base_url=None: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://jina-embed:80"),
    )

    assert embed_text("some evidence text") is None


def test_client_is_pooled_not_reconstructed_per_call():
    # Real bug this guards: _client used to build a brand-new httpx.Client
    # (and pay a fresh TCP handshake to the jina-embed sidecar) on every
    # single embed_text call - the exact anti-pattern #183 fixed for the
    # GitHub API and Redis clients but never got swept into this file.
    # embed_text runs once per cache-eligible packet, potentially dozens
    # of times in one AIRview build.
    _client.cache_clear()
    try:
        first = _client("http://jina-embed:80")
        second = _client("http://jina-embed:80")
        assert first is second
        # A distinct base_url still gets its own pooled client, not a
        # shared one that would send requests to the wrong host.
        other = _client("http://custom-jina-embed:9999")
        assert other is not first
    finally:
        _client.cache_clear()
