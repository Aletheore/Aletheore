"""Internal-only embedding service backed by jinaai/jina-embeddings-v2-base-code.

Replaces Ollama+nomic-embed-text as the embedder behind scan_worker's
evidence-packet and flash-review similarity caches - see
docs/superpowers/specs (jina vs nomic vs bge-m3 retrieval benchmark) for
why: real, corrected benchmark numbers across 6 languages showed jina
beating nomic on every metric (75.0/93.1/95.8% vs 73.6/87.5/91.7% top-1/3/5),
with bge-m3 actually worse than nomic.

CPU-only in production (no MPS/CUDA on these hosts) - fine here because
callers send one text per request, not the large batches a full index
build sends; a single ~5000-char embed is fast enough on CPU that the
batching/leak concerns from local benchmarking don't apply.
"""
import logging

from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

logger.info("Loading jinaai/jina-embeddings-v2-base-code...")
_model = SentenceTransformer("jinaai/jina-embeddings-v2-base-code", trust_remote_code=True, device="cpu")
logger.info("Model loaded, dim=%d", _model.get_sentence_embedding_dimension())


class EmbedRequest(BaseModel):
    text: str


class EmbedResponse(BaseModel):
    embedding: list[float]


@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest) -> EmbedResponse:
    vector = _model.encode(req.text, normalize_embeddings=True).tolist()
    return EmbedResponse(embedding=vector)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
