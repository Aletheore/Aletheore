"""Internal-only embedding service backed by jinaai/jina-embeddings-v2-base-code.

Replaces Ollama+nomic-embed-text as the embedder behind scan_worker's
evidence-packet and flash-review similarity caches - see
docs/superpowers/specs (jina vs nomic vs bge-m3 retrieval benchmark) for
why: real, corrected benchmark numbers across 6 languages showed jina
beating nomic on every metric (75.0/93.1/95.8% vs 73.6/87.5/91.7% top-1/3/5),
with bge-m3 actually worse than nomic.

CPU-only in production (no MPS/CUDA on these hosts). The single-text
contract remains for scan-worker caches; the additive batch endpoint is used
by hosted index builds so one request can encode many chunks efficiently.
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


class EmbedBatchRequest(BaseModel):
    texts: list[str]


class EmbedBatchResponse(BaseModel):
    embeddings: list[list[float]]


@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest) -> EmbedResponse:
    vector = _model.encode(req.text, normalize_embeddings=True).tolist()
    return EmbedResponse(embedding=vector)


@app.post("/embed_batch", response_model=EmbedBatchResponse)
def embed_batch(req: EmbedBatchRequest) -> EmbedBatchResponse:
    vectors = _model.encode(req.texts, normalize_embeddings=True).tolist()
    return EmbedBatchResponse(embeddings=vectors)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
