"""Internal-only embedding service backed by jinaai/jina-embeddings-v2-base-code.

Served via llama.cpp against a Q8_0 GGUF quantization of the model, not raw
PyTorch/HuggingFace `transformers`. That switch is the point of this file:
measured on this exact production host against real source (a 133k-char,
38-chunk flask sample), the previous sentence-transformers/PyTorch backend
hadn't finished the first fifth of the batch after 164s and went on to
OOM-kill the container; llama.cpp finished the same batch in 24.55s (~5,400
chars/s) with no memory blowup. That gap isn't Apple Silicon vs. this host's
CPU (both were checked directly - Accelerate/AMX locally, MKL with full
AVX-512/VNNI here, neither crippled) and it isn't core count (2 threads
performed the same as unconstrained locally). It's llama.cpp's quantized,
hand-tuned CPU kernels against eager-mode PyTorch running an unquantized,
custom `trust_remote_code` model - the same reason nomic-embed-text (served
by Ollama, also llama.cpp underneath) never had this problem. Quantization
cost was checked too: Q8_0 output measured at 0.9997 cosine similarity
against the full-precision PyTorch embedding on the same input.

CPU-only in production (no GPU on these hosts - n_gpu_layers=0 is explicit
below so a build that happens to have GPU support compiled in, e.g. Metal on
a dev machine, doesn't silently take a different code path than prod runs).
The single-text contract remains for scan-worker caches; the additive batch
endpoint is used by hosted index builds so one request can encode many
chunks in one call.

_MODEL_LOCK below is not an optimization - it is required for correctness.
A single `Llama` instance is not safe for concurrent use from multiple
threads (abetlen/llama-cpp-python#1241: concurrent calls into one context
corrupt its internal GGML tensor state), and both routes below are sync
`def` handlers, which FastAPI/Starlette dispatches onto its worker
threadpool rather than the event loop - so two requests arriving close
together genuinely can call into `_model` from two different threads at
once. Reproduced directly: profiling a real hosted index build crashed the
container with `GGML_ASSERT(offset + size <= ggml_nbytes(tensor) &&
"tensor read out of bounds")` after dozens of successful requests, on two
different corpora (gson after ~33 requests, apache/thrift after ~38
minutes) - not tied to any one input's size or content, consistent with a
race rather than a bad chunk. This process serves exactly one shared model
to every caller (two scan-worker replicas, demo-scan-worker, hosted `aletheore
index` traffic), so concurrent callers are the normal case, not an edge
case - serializing access to the model is the correct fix, not a
workaround.
"""
import logging
import math
import os
import threading

from fastapi import FastAPI
from llama_cpp import Llama
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_THREADS = int(os.environ.get("JINA_EMBED_THREADS", "1"))
_MODEL_PATH = os.environ.get("JINA_EMBED_MODEL_PATH", "/app/model.gguf")
# 2048 comfortably covers real chunk sizes (chunking upstream keeps
# individual texts well under this); jina-embeddings-v2 supports up to 8192
# but reserving that much KV-cache space for every request buys nothing here
# and costs memory.
_CTX = int(os.environ.get("JINA_EMBED_CTX", "2048"))

app = FastAPI()

logger.info("Loading %s with %d threads...", _MODEL_PATH, _THREADS)
_model = Llama(
    model_path=_MODEL_PATH,
    embedding=True,
    n_threads=_THREADS,
    n_ctx=_CTX,
    n_gpu_layers=0,
    verbose=False,
)
logger.info("Model loaded")

_model_lock = threading.Lock()


class EmbedRequest(BaseModel):
    text: str


class EmbedResponse(BaseModel):
    embedding: list[float]


class EmbedBatchRequest(BaseModel):
    texts: list[str]


class EmbedBatchResponse(BaseModel):
    embeddings: list[list[float]]


def _normalize(vector: list[float]) -> list[float]:
    # llama.cpp's pooled output is not unit-length (measured L2 norm ~14.5
    # on real text) - callers downstream (LanceDB cosine search) expect the
    # same normalized vectors the previous sentence-transformers backend
    # produced via normalize_embeddings=True.
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        return vector
    return [v / norm for v in vector]


@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest) -> EmbedResponse:
    with _model_lock:
        result = _model.create_embedding(req.text)
    vector = _normalize(result["data"][0]["embedding"])
    return EmbedResponse(embedding=vector)


@app.post("/embed_batch", response_model=EmbedBatchResponse)
def embed_batch(req: EmbedBatchRequest) -> EmbedBatchResponse:
    with _model_lock:
        result = _model.create_embedding(req.texts)
    # Sorted explicitly rather than trusting list order: app-server checks
    # the returned count matches the request but has no way to check order,
    # so a reordered response would silently attach the wrong vector to the
    # wrong chunk in the index.
    ordered = sorted(result["data"], key=lambda item: item["index"])
    vectors = [_normalize(item["embedding"]) for item in ordered]
    return EmbedBatchResponse(embeddings=vectors)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
