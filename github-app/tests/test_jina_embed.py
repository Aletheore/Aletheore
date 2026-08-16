import importlib
import sys
import types


def _import_server(monkeypatch):
    """Import jina_embed.server with its two heavy deps stubbed out.

    Neither torch nor sentence_transformers is installed in the test job -
    they live only in the jina-embed image - so the module is imported
    against fakes. Returns the module plus the thread counts torch was asked
    for, since that call happens at import time and cannot be observed after.
    """

    class Encoded(list):
        def tolist(self):
            return list(self)

    class FakeModel:
        def __init__(self, *args, **kwargs):
            pass

        def get_sentence_embedding_dimension(self):
            return 3

        def encode(self, texts, normalize_embeddings):
            if isinstance(texts, str):
                texts = [texts]
            return Encoded([[float(index), 0.0, 1.0] for index, _ in enumerate(texts)])

    fake_sentence_transformers = types.ModuleType("sentence_transformers")
    fake_sentence_transformers.SentenceTransformer = FakeModel
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_sentence_transformers)

    requested_threads: list[int] = []
    fake_torch = types.ModuleType("torch")
    fake_torch.set_num_threads = requested_threads.append
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    sys.modules.pop("jina_embed.server", None)
    server = importlib.import_module("jina_embed.server")
    return server, requested_threads


def test_embed_batch_returns_one_vector_per_text(monkeypatch):
    server, _ = _import_server(monkeypatch)
    response = server.embed_batch(server.EmbedBatchRequest(texts=["a", "b"]))

    assert len(response.embeddings) == 2
    assert all(len(vector) == 3 for vector in response.embeddings)


def test_torch_thread_count_follows_the_environment(monkeypatch):
    # The compose file sets JINA_EMBED_THREADS to match the `cpus` it grants.
    # If this stopped being read, torch would go back to sizing its pool from
    # the host's core count and thrash against the cgroup quota - the exact
    # failure that held hosted embedding to ~340 characters/second.
    monkeypatch.setenv("JINA_EMBED_THREADS", "2")
    _, requested_threads = _import_server(monkeypatch)

    assert requested_threads == [2]


def test_torch_thread_count_defaults_to_one(monkeypatch):
    monkeypatch.delenv("JINA_EMBED_THREADS", raising=False)
    _, requested_threads = _import_server(monkeypatch)

    assert requested_threads == [1]
