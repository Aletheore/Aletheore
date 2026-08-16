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

Per-instance locking below is not an optimization - it is required for
correctness. A single `Llama` instance is not safe for concurrent use from
multiple threads (abetlen/llama-cpp-python#1241: concurrent calls into one
context corrupt its internal GGML tensor state), and both routes below are
sync `def` handlers, which FastAPI/Starlette dispatches onto its worker
threadpool rather than the event loop - so two requests arriving close
together genuinely can call into the same `Llama` instance from two
different threads at once. Reproduced directly: profiling a real hosted
index build crashed the container with `GGML_ASSERT(offset + size <=
ggml_nbytes(tensor) && "tensor read out of bounds")` after dozens of
successful requests, on two different corpora (gson after ~33 requests,
apache/thrift after ~38 minutes) - not tied to any one input's size or
content, consistent with a race rather than a bad chunk. This process
serves exactly one shared model to every caller (two scan-worker replicas,
demo-scan-worker, hosted `aletheore index` traffic), so concurrent callers
are the normal case, not an edge case - serializing access to any single
instance is the correct fix, not a workaround.

JINA_EMBED_INSTANCES (default 1, preserving prior single-instance
behavior) loads that many independent Llama instances instead of one,
each with its own lock and 1/N of JINA_EMBED_THREADS. This is not about
using more CPU - the container's total thread budget is unchanged either
way - it's about how that budget is spent. A single instance parallelizing
one embedding call across N threads pays real synchronization/
memory-bandwidth overhead inside llama.cpp's matrix-multiply kernels
(measured well under linear scaling at 2 threads). N single-threaded
instances processing N different requests at once pay none of that: pure
task parallelism, no cross-thread coordination inside one call. It also
directly fixes the serialization side effect of the lock above - today
every caller queues behind one instance even when two scan-worker
replicas or a background /embed cache call and an /embed_batch index
build land at the same moment; multiple instances let genuinely
independent requests actually run at once instead of just avoiding
corruption while still queueing.
"""
import itertools
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
_NUM_INSTANCES = int(os.environ.get("JINA_EMBED_INSTANCES", "1"))
# Divided, not duplicated: JINA_EMBED_THREADS is sized to the container's
# `cpus` limit (see docker-compose.yml), so N instances each get a fair
# share of that same budget rather than N times it. max(1, ...) so an
# instance count larger than the thread budget still gets one real thread
# each instead of zero.
_THREADS_PER_INSTANCE = max(1, _THREADS // _NUM_INSTANCES)

app = FastAPI()


class _Instance:
    __slots__ = ("model", "lock")

    def __init__(self, model: Llama) -> None:
        self.model = model
        self.lock = threading.Lock()


_instances: list[_Instance] = []
for _i in range(_NUM_INSTANCES):
    logger.info(
        "Loading instance %d/%d (%s, %d threads)...",
        _i + 1,
        _NUM_INSTANCES,
        _MODEL_PATH,
        _THREADS_PER_INSTANCE,
    )
    _instances.append(
        _Instance(
            Llama(
                model_path=_MODEL_PATH,
                embedding=True,
                n_threads=_THREADS_PER_INSTANCE,
                n_ctx=_CTX,
                n_gpu_layers=0,
                verbose=False,
            )
        )
    )
logger.info("%d instance(s) loaded", _NUM_INSTANCES)

# Round-robin rather than least-recently-used: request duration varies with
# batch size, so "next in rotation" spreads load evenly on average without
# needing per-instance timing to decide, and the counter itself is cheap
# enough that contention on it is never the bottleneck (real work happens
# inside the per-instance lock above, held for orders of magnitude longer).
_next_instance = itertools.count()
_rotation_lock = threading.Lock()


def _pick_instance() -> _Instance:
    with _rotation_lock:
        index = next(_next_instance) % len(_instances)
    return _instances[index]


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
    instance = _pick_instance()
    with instance.lock:
        result = instance.model.create_embedding(req.text)
    vector = _normalize(result["data"][0]["embedding"])
    return EmbedResponse(embedding=vector)


@app.post("/embed_batch", response_model=EmbedBatchResponse)
def embed_batch(req: EmbedBatchRequest) -> EmbedBatchResponse:
    instance = _pick_instance()
    with instance.lock:
        result = instance.model.create_embedding(req.texts)
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
