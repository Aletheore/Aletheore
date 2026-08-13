"""Ollama embedding client for evidence-packet similarity caching."""

import logging
import os

import httpx

EMBEDDING_MODEL = "nomic-embed-text"

# The pulled nomic-embed-text GGUF's own metadata caps it at a real,
# hard 2048-token training context (confirmed directly against the running
# server: `nomic-bert.context_length = 2048`, and requesting num_ctx=8192
# just produced "requested context size too large for model" and got
# silently clamped back to 2048 - the model was never going to accept more,
# regardless of server or per-request settings). Every real embedding call
# failed until input was kept under this real limit; the cache had a 0%
# hit rate for the 38 hours it ran before this was caught.
EMBEDDING_NUM_CTX = 2048

# Empirically calibrated against the real running model with text shaped
# like actual evidence packets (file paths, symbol names, short identifiers
# - not plain prose, and not a pathological single repeated character
# either): 6600 chars succeeded, 6990 failed. 5000 chars keeps a real
# margin below that boundary for token-density variance in different
# packets and the single-CPU container being busier under real concurrent
# load than this manual test. A truncated embedding is still useful for
# similarity matching; the exact text match isn't needed, and a cache hit
# is always re-verified against current evidence regardless of how it was
# found.
MAX_EMBEDDING_CHARS = 5000

logger = logging.getLogger(__name__)


def _client(base_url: str | None = None) -> httpx.Client:
    return httpx.Client(base_url=base_url or os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434"))


def embed_text(text: str, base_url: str | None = None, timeout_seconds: float = 10.0) -> list[float] | None:
    # A 7000-char prompt (above the truncation budget below) measured 3.6s
    # against the real server - 5.0 left too little margin under real
    # concurrent load on a single-CPU container; this is a degrade-to-miss
    # timeout, not a build-blocking one, so erring toward more patience
    # costs nothing but a slightly slower cache lookup.
    if len(text) > MAX_EMBEDDING_CHARS:
        text = text[:MAX_EMBEDDING_CHARS]
    try:
        with _client(base_url) as client:
            response = client.post(
                "/api/embeddings",
                json={
                    "model": EMBEDDING_MODEL,
                    "prompt": text,
                    "options": {"num_ctx": EMBEDDING_NUM_CTX},
                },
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("embedding call failed (%s); treating cache as unavailable", type(exc).__name__)
        return None

    embedding = data.get("embedding")
    if not isinstance(embedding, list) or not embedding:
        logger.warning("embedding response missing embedding array; treating cache as unavailable")
        return None
    if not all(isinstance(value, int | float) for value in embedding):
        logger.warning("embedding response contains non-numeric values; treating cache as unavailable")
        return None
    return [float(value) for value in embedding]
