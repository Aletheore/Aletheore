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
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

logger = logging.getLogger(__name__)

PROVIDER_LOCAL = "local"
PROVIDER_DEEPINFRA = "deepinfra"

DEEPINFRA_EMBEDDINGS_URL = "https://api.deepinfra.com/v1/openai/embeddings"
# Qwen3-Embedding-8B, asked for 1024 of its 4096 dimensions (it is trained so that a
# leading slice is still a good embedding). Measured on 332 retrieval questions the
# 1024-dim vectors scored the same as the full 4096 (MRR difference +0.001, 95% CI
# -0.011 to +0.013), at a quarter of the storage and cache-lookup cost, and beat the
# 0.6B model (+0.030, CI +0.003 to +0.056) at the same price per token.
#
# The size is not 768 on purpose: an older CLI labels whatever the hosted endpoint
# returns as the jina model, so a 768-dim replacement would be accepted into an
# existing jina index and silently mix two vector spaces. A different dimension
# is caught by the CLI's dimension guard and forces a clean rebuild.
DEFAULT_DEEPINFRA_MODEL = "Qwen/Qwen3-Embedding-8B"
DEFAULT_DEEPINFRA_DIMENSIONS = 1024

# Per-request bounds, taken from the retrieval benchmark harness: large enough to
# keep the call count low, small enough to stay clear of request-size limits.
MAX_TEXTS_PER_CALL = 48
MAX_CHARS_PER_CALL = 120_000
MAX_PARALLEL_CALLS = 4
RETRIES = 3
BACKOFF_SECONDS = 0.5
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
# Process-wide cap on simultaneous calls to the provider, so many requests at once
# queue here instead of fanning out into a burst of 429s that the retries amplify.
MAX_IN_FLIGHT_CALLS = 8
_IN_FLIGHT = threading.BoundedSemaphore(MAX_IN_FLIGHT_CALLS)


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


def deepinfra_dimensions() -> int | None:
    """Vector size to request, or None to take the model's native size.
    DEEPINFRA_EMBED_DIMENSIONS=0 (or "native") opts out, for a model that does not take the option."""
    raw = os.environ.get("DEEPINFRA_EMBED_DIMENSIONS", "").strip().lower()
    if not raw:
        return DEFAULT_DEEPINFRA_DIMENSIONS
    if raw in ("0", "native"):
        return None
    try:
        value = int(raw)
    except ValueError:
        logger.error("invalid DEEPINFRA_EMBED_DIMENSIONS %r; using %d", raw, DEFAULT_DEEPINFRA_DIMENSIONS)
        return DEFAULT_DEEPINFRA_DIMENSIONS
    if value <= 0:
        logger.error("invalid DEEPINFRA_EMBED_DIMENSIONS %r (use 'native' to omit it); using %d", raw, DEFAULT_DEEPINFRA_DIMENSIONS)
        return DEFAULT_DEEPINFRA_DIMENSIONS
    return value


def embedder_label() -> str:
    """Names the model and vector size, e.g. 'Qwen/Qwen3-Embedding-8B@1024'. Used as the cache
    tag and the hosted response's model, so vectors of two different sizes are never compared."""
    dimensions = deepinfra_dimensions()
    return deepinfra_model() if dimensions is None else f"{deepinfra_model()}@{dimensions}"


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


# Used only when a response omits `usage`, which DeepInfra does not do in practice.
# Dense code runs about 2.6 characters per token and prose about 4, so 3.0 sits
# between the two: it never charges a customer much more than the real cost.
FALLBACK_CHARS_PER_TOKEN = 3.0


def _embed_one_call(
    batch: list[str], timeout_seconds: float, retries: int
) -> tuple[list[list[float]], int]:
    api_key = os.environ.get("DEEPINFRA_API_KEY", "").strip()
    if not api_key:
        raise EmbedderUnavailable("DEEPINFRA_API_KEY is not set")
    last_status: int | str = "no response"
    payload = {"model": deepinfra_model(), "input": batch, "encoding_format": "float"}
    dimensions = deepinfra_dimensions()
    if dimensions is not None:
        payload["dimensions"] = dimensions
    for attempt in range(retries):
        if attempt:
            # Backing off matters most for a 429: an immediate retry would fail the same way.
            time.sleep(BACKOFF_SECONDS * 2 ** (attempt - 1))
        try:
            with _IN_FLIGHT:
                response = httpx.post(
                    DEEPINFRA_EMBEDDINGS_URL,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                    timeout=timeout_seconds,
                )
        except httpx.HTTPError as exc:
            last_status = type(exc).__name__
            continue
        if response.status_code == 200:
            try:
                body = response.json()
                rows = sorted(body["data"], key=lambda item: item["index"])
                vectors = [_normalize([float(v) for v in row["embedding"]]) for row in rows]
            except (KeyError, TypeError, ValueError) as exc:
                raise EmbedderUnavailable("malformed response from the embedding provider") from exc
            if len(vectors) != len(batch):
                raise EmbedderUnavailable("embedding provider returned the wrong number of vectors")
            usage = body.get("usage")
            tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
            if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
                tokens = math.ceil(sum(len(text) for text in batch) / FALLBACK_CHARS_PER_TOKEN)
            return vectors, tokens
        last_status = response.status_code
        if response.status_code not in RETRYABLE_STATUS:
            break
    # Never include the response body: a provider error can quote the input text.
    raise EmbedderUnavailable(f"embedding provider call failed ({last_status})")


def embed_batch(
    texts: list[str], timeout_seconds: float = 60.0, retries: int = RETRIES
) -> tuple[list[list[float]], int]:
    """Embed `texts` in input order and report the prompt tokens to charge: the
    provider's own `usage` figure, or a character-based estimate on the rare
    response that omits it. Raises EmbedderUnavailable. Blocking."""
    if not texts:
        return [], 0
    # Callers already cap each text far below this; it only keeps a single oversized
    # text from becoming a request larger than the per-call bound.
    texts = [text[:MAX_CHARS_PER_CALL] for text in texts]
    batches = _batches(texts)
    if len(batches) == 1:
        return _embed_one_call(batches[0], timeout_seconds, retries)
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_CALLS) as pool:
        futures = [pool.submit(_embed_one_call, batch, timeout_seconds, retries) for batch in batches]
        try:
            results = [future.result() for future in futures]
        except BaseException:
            # One batch failing fails the whole request, so do not start (and pay the provider
            # for) the batches that have not begun. Ones already running finish on their own.
            for future in futures:
                future.cancel()
            raise
    return (
        [vector for vectors, _ in results for vector in vectors],
        sum(tokens for _, tokens in results),
    )
