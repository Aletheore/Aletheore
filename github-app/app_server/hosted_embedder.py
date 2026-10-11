"""Optional DeepInfra embedder, behind EMBED_PROVIDER.

Default is "local": the self-hosted jina-embed sidecar, exactly as before. Set
EMBED_PROVIDER=deepinfra (plus DEEPINFRA_API_KEY) to send embedding text to
DeepInfra instead. That is a privacy-relevant change: the text is customer code
(PR diffs, evidence packets, and AIR index chunks), so flipping the flag makes
DeepInfra a subprocessor for those three paths.

There is deliberately no per-request fallback to the local model. A vector is
only comparable to vectors from the same model, so a request that quietly fell
back would store jina vectors under a Qwen label (or the reverse). A failed call
degrades the way a cache miss always has: embed_text returns None and the hosted
endpoint answers 502 so the CLI retries. Rolling back is flipping the flag, and
the jina-embed container stays deployed for that.
"""

import logging
import math
import os
from concurrent.futures import ThreadPoolExecutor

import httpx

logger = logging.getLogger(__name__)

PROVIDER_LOCAL = "local"
PROVIDER_DEEPINFRA = "deepinfra"

DEEPINFRA_EMBEDDINGS_URL = "https://api.deepinfra.com/v1/openai/embeddings"
# 1024-dim, not 768 on purpose: an older CLI labels whatever the hosted endpoint
# returns as the jina model, so a 768-dim replacement would be accepted into an
# existing jina index and silently mix two vector spaces. A different dimension
# is caught by the CLI's dimension guard and forces a clean rebuild.
DEFAULT_DEEPINFRA_MODEL = "Qwen/Qwen3-Embedding-0.6B"

# Per-request bounds, taken from the retrieval benchmark harness: large enough to
# keep the call count low, small enough to stay clear of request-size limits.
MAX_TEXTS_PER_CALL = 48
MAX_CHARS_PER_CALL = 120_000
MAX_PARALLEL_CALLS = 4
RETRIES = 3
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class EmbedderUnavailable(Exception):
    """The configured remote embedder could not produce vectors."""


def provider() -> str:
    value = os.environ.get("EMBED_PROVIDER", PROVIDER_LOCAL).strip().lower()
    if value in (PROVIDER_LOCAL, PROVIDER_DEEPINFRA):
        return value
    logger.error("unknown EMBED_PROVIDER %r; using the local embedder", value)
    return PROVIDER_LOCAL


def deepinfra_model() -> str:
    return os.environ.get("DEEPINFRA_EMBED_MODEL", "").strip() or DEFAULT_DEEPINFRA_MODEL


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    return vector if norm == 0 else [v / norm for v in vector]


def _batches(texts: list[str]) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    current_chars = 0
    for text in texts:
        if current and (len(current) >= MAX_TEXTS_PER_CALL or current_chars + len(text) > MAX_CHARS_PER_CALL):
            batches.append(current)
            current, current_chars = [], 0
        current.append(text)
        current_chars += len(text)
    if current:
        batches.append(current)
    return batches


def _embed_one_call(batch: list[str], timeout_seconds: float, retries: int) -> list[list[float]]:
    api_key = os.environ.get("DEEPINFRA_API_KEY", "").strip()
    if not api_key:
        raise EmbedderUnavailable("DEEPINFRA_API_KEY is not set")
    last_status: int | str = "no response"
    for attempt in range(retries):
        try:
            response = httpx.post(
                DEEPINFRA_EMBEDDINGS_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": deepinfra_model(), "input": batch, "encoding_format": "float"},
                timeout=timeout_seconds,
            )
        except httpx.HTTPError as exc:
            last_status = type(exc).__name__
            continue
        if response.status_code == 200:
            try:
                rows = sorted(response.json()["data"], key=lambda item: item["index"])
                vectors = [_normalize([float(v) for v in row["embedding"]]) for row in rows]
            except (KeyError, TypeError, ValueError) as exc:
                raise EmbedderUnavailable("malformed response from the embedding provider") from exc
            if len(vectors) != len(batch):
                raise EmbedderUnavailable("embedding provider returned the wrong number of vectors")
            return vectors
        last_status = response.status_code
        if response.status_code not in RETRYABLE_STATUS:
            break
    # Never include the response body: a provider error can quote the input text.
    raise EmbedderUnavailable(f"embedding provider call failed ({last_status})")


def embed_batch(texts: list[str], timeout_seconds: float = 60.0, retries: int = RETRIES) -> list[list[float]]:
    """Embed `texts` in input order, or raise EmbedderUnavailable. Blocking."""
    if not texts:
        return []
    batches = _batches(texts)
    if len(batches) == 1:
        return _embed_one_call(batches[0], timeout_seconds, retries)
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_CALLS) as pool:
        results = list(pool.map(lambda batch: _embed_one_call(batch, timeout_seconds, retries), batches))
    return [vector for result in results for vector in result]
