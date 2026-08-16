import importlib
import math
import sys
import types


def _import_server(monkeypatch):
    """Import jina_embed.server with llama_cpp stubbed out.

    llama-cpp-python is not installed in the test job - it lives only in
    the jina-embed image, compiled from source against build tools that
    aren't part of this image - so the module is imported against a fake.
    Returns the module plus the n_threads values the fake Llama() was
    constructed with, since that happens at import time and can't be
    observed after.
    """

    class FakeLlama:
        def __init__(self, *args, **kwargs):
            requested_threads.append(kwargs.get("n_threads"))

        def create_embedding(self, input):
            texts = [input] if isinstance(input, str) else input
            # Deliberately not unit-length, matching the real model's raw
            # pooled output - exercises server.py's own normalization
            # rather than a fake that does it for the code under test.
            return {
                "data": [
                    {"index": index, "embedding": [float(index) + 1.0, 0.0, 3.0]}
                    for index, _ in enumerate(texts)
                ]
            }

    requested_threads: list[int | None] = []
    fake_llama_cpp = types.ModuleType("llama_cpp")
    fake_llama_cpp.Llama = FakeLlama
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama_cpp)

    sys.modules.pop("jina_embed.server", None)
    server = importlib.import_module("jina_embed.server")
    return server, requested_threads


def test_embed_batch_returns_one_normalized_vector_per_text(monkeypatch):
    server, _ = _import_server(monkeypatch)
    response = server.embed_batch(server.EmbedBatchRequest(texts=["a", "b"]))

    assert len(response.embeddings) == 2
    for vector in response.embeddings:
        norm = math.sqrt(sum(v * v for v in vector))
        assert math.isclose(norm, 1.0, rel_tol=1e-9)


def test_embed_batch_preserves_request_order_even_if_the_backend_does_not(monkeypatch):
    server, _ = _import_server(monkeypatch)

    class ShufflingLlama:
        def __init__(self, *args, **kwargs):
            pass

        def create_embedding(self, input):
            # Backend returns items out of order, and on orthogonal axes so
            # normalization (which erases relative magnitude on the same
            # axis) can't mask a mix-up - server.py must sort by index
            # rather than trust list position, or a chunk gets the wrong
            # vector attached silently in the index.
            return {
                "data": [
                    {"index": 2, "embedding": [0.0, 0.0, 5.0]},
                    {"index": 0, "embedding": [5.0, 0.0, 0.0]},
                    {"index": 1, "embedding": [0.0, 5.0, 0.0]},
                ]
            }

    server._model = ShufflingLlama()
    response = server.embed_batch(server.EmbedBatchRequest(texts=["a", "b", "c"]))

    assert [[round(x) for x in v] for v in response.embeddings] == [
        [1, 0, 0],
        [0, 1, 0],
        [0, 0, 1],
    ]


def test_embed_returns_a_normalized_vector_for_a_single_text(monkeypatch):
    server, _ = _import_server(monkeypatch)
    response = server.embed(server.EmbedRequest(text="a"))

    norm = math.sqrt(sum(v * v for v in response.embedding))
    assert math.isclose(norm, 1.0, rel_tol=1e-9)


def test_thread_count_follows_the_environment(monkeypatch):
    # The compose file sets JINA_EMBED_THREADS to match the `cpus` it
    # grants - the whole reason this backend is fast: the 24.55s/2-thread
    # real-batch measurement server.py's docstring cites was made at this
    # exact setting, not a default.
    monkeypatch.setenv("JINA_EMBED_THREADS", "2")
    _, requested_threads = _import_server(monkeypatch)

    assert requested_threads == [2]


def test_thread_count_defaults_to_one(monkeypatch):
    monkeypatch.delenv("JINA_EMBED_THREADS", raising=False)
    _, requested_threads = _import_server(monkeypatch)

    assert requested_threads == [1]
