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
import os

import torch
from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# torch sizes its thread pool from the number of cores it can *see*, which is
# the host's, not the cgroup's. Under `cpus: "1.0"` that meant ~N threads
# fighting over one core's worth of quota - the scheduler spends its time
# context-switching instead of embedding. Measured against production before
# this was set: a steady ~340 characters/second regardless of batch size,
# which made a 60s request budget (embeddings_api's httpx timeout to this
# service) top out around 20k characters and put a real index build out of
# reach entirely.
#
# Set explicitly from the same number the compose file allocates, so the two
# cannot drift: raising `cpus` without raising this buys nothing, and raising
# this without raising `cpus` makes the thrashing worse.
_THREADS = int(os.environ.get("JINA_EMBED_THREADS", "1"))
torch.set_num_threads(_THREADS)
logger.info("torch threads=%d", _THREADS)

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
