import importlib
import itertools
import math
import sys
import threading
import time
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

    server._instances = [server._Instance(ShufflingLlama())]
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


def test_concurrent_requests_do_not_call_the_model_at_the_same_time(monkeypatch):
    """A single Llama instance is not safe for concurrent use from multiple
    threads (abetlen/llama-cpp-python#1241) - both routes are sync `def`
    handlers, which FastAPI dispatches onto its worker threadpool, so two
    requests arriving close together really can reach the model from two
    threads at once without a lock. Reproduced in production as a
    GGML_ASSERT "tensor read out of bounds" crash after dozens of
    successful requests. This fake model raises if it is ever entered while
    another call is still inside it, which only a correctly-held lock in
    server.py prevents."""
    server, _ = _import_server(monkeypatch)

    class ConcurrencyDetectingLlama:
        def __init__(self, *args, **kwargs):
            self._inside = threading.Lock()

        def create_embedding(self, input):
            if not self._inside.acquire(blocking=False):
                raise AssertionError("create_embedding entered concurrently")
            try:
                time.sleep(0.05)  # widen the race window
                texts = [input] if isinstance(input, str) else input
                return {
                    "data": [
                        {"index": index, "embedding": [1.0, 0.0, 0.0]}
                        for index, _ in enumerate(texts)
                    ]
                }
            finally:
                self._inside.release()

    server._instances = [server._Instance(ConcurrencyDetectingLlama())]

    errors: list[Exception] = []

    def call_embed():
        try:
            server.embed(server.EmbedRequest(text="x"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=call_embed) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []


def test_multiple_instances_actually_run_concurrently(monkeypatch):
    """The whole point of JINA_EMBED_INSTANCES > 1: independent requests on
    different instances must be able to overlap in time, not just avoid
    corrupting each other. A round-robin bug that always picked the same
    instance would pass the concurrency-safety test above (nothing would
    ever race) while silently defeating the entire feature - this test
    would catch that, the one above would not."""
    server, _ = _import_server(monkeypatch)

    class SlowLlama:
        def __init__(self, *args, **kwargs):
            pass

        def create_embedding(self, input):
            concurrent_calls.append(1)
            time.sleep(0.1)
            concurrent_calls.append(-1)
            texts = [input] if isinstance(input, str) else input
            return {
                "data": [
                    {"index": index, "embedding": [1.0, 0.0, 0.0]}
                    for index, _ in enumerate(texts)
                ]
            }

    concurrent_calls: list[int] = []
    server._instances = [server._Instance(SlowLlama()), server._Instance(SlowLlama())]
    server._next_instance = itertools.count()

    threads = [
        threading.Thread(target=server.embed, args=(server.EmbedRequest(text="x"),))
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Running total of in-flight calls across both instances at any point in
    # time - if it never exceeds 1, the two instances never actually
    # overlapped despite being separate objects.
    running_total = 0
    peak_concurrency = 0
    for delta in concurrent_calls:
        running_total += delta
        peak_concurrency = max(peak_concurrency, running_total)

    assert peak_concurrency == 2
