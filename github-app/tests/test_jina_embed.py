import importlib
import sys
import types


def test_embed_batch_returns_one_vector_per_text(monkeypatch):
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
    sys.modules.pop("jina_embed.server", None)

    server = importlib.import_module("jina_embed.server")
    response = server.embed_batch(server.EmbedBatchRequest(texts=["a", "b"]))

    assert len(response.embeddings) == 2
    assert all(len(vector) == 3 for vector in response.embeddings)
