import json

import httpx

from scan_worker.embedding_client import MAX_EMBEDDING_CHARS, embed_text


def test_embed_text_returns_vector_on_success(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/embed"
        return httpx.Response(200, json=[[0.1, 0.2, 0.3]])

    monkeypatch.setattr(
        "scan_worker.embedding_client._client",
        lambda base_url=None: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://tei:80"),
    )

    result = embed_text("some evidence text")

    assert result == [0.1, 0.2, 0.3]


def test_embed_text_sends_inputs_as_a_single_string(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=[[0.1]])

    monkeypatch.setattr(
        "scan_worker.embedding_client._client",
        lambda base_url=None: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://tei:80"),
    )

    embed_text("some evidence text")

    assert seen["body"] == {"inputs": "some evidence text"}


def test_embed_text_truncates_oversized_input(monkeypatch):
    # A real production packet hit 52k+ tokens - nowhere close to fitting
    # the model's real 2048-token context. Truncating keeps the call from
    # failing outright; the exact text match isn't needed for similarity
    # matching, and any cache hit found this way is still re-verified
    # against current evidence before being served.
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["inputs"] = json.loads(request.content)["inputs"]
        return httpx.Response(200, json=[[0.1]])

    monkeypatch.setattr(
        "scan_worker.embedding_client._client",
        lambda base_url=None: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://tei:80"),
    )

    embed_text("x" * (MAX_EMBEDDING_CHARS + 5000))

    assert len(seen["inputs"]) == MAX_EMBEDDING_CHARS


def test_embed_text_uses_explicit_base_url(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=[[0.1]])

    monkeypatch.setattr(
        "scan_worker.embedding_client._client",
        lambda base_url=None: httpx.Client(transport=httpx.MockTransport(handler), base_url=base_url),
    )

    assert embed_text("some evidence text", base_url="http://custom-tei:80") == [0.1]
    assert seen["url"].startswith("http://custom-tei/")


def test_embed_text_returns_none_on_connection_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(
        "scan_worker.embedding_client._client",
        lambda base_url=None: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://tei:80"),
    )

    assert embed_text("some evidence text") is None


def test_embed_text_returns_none_on_timeout(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    monkeypatch.setattr(
        "scan_worker.embedding_client._client",
        lambda base_url=None: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://tei:80"),
    )

    assert embed_text("some evidence text") is None


def test_embed_text_returns_none_on_malformed_response(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    monkeypatch.setattr(
        "scan_worker.embedding_client._client",
        lambda base_url=None: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://tei:80"),
    )

    assert embed_text("some evidence text") is None


def test_embed_text_returns_none_on_empty_batch(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    monkeypatch.setattr(
        "scan_worker.embedding_client._client",
        lambda base_url=None: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://tei:80"),
    )

    assert embed_text("some evidence text") is None
