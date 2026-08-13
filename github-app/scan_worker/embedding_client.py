"""TEI (self-hosted nomic-embed-text-v1.5) embedding client for
evidence-packet similarity caching. Was Ollama; replaced for real
concurrent-request throughput (see Dockerfile.tei)."""

import logging
import os

import httpx

EMBEDDING_MODEL = "nomic-embed-text-v1.5"

# The model's own real, hard-trained context is 2048 tokens (confirmed
# directly against the running Ollama server this replaced:
# `nomic-bert.context_length = 2048`; requesting more there got silently
# clamped with a warning). TEI's own self-reported max_input_length is NOT
# trustworthy for this number - it comes from a legacy config field
# (n_positions=8192) that Dockerfile.tei's build-time patch removes
# specifically because it doesn't reflect the model's real trained limit.
# The char-based truncation below is the real safety margin, independent
# of whatever TEI reports.
#
# Empirically calibrated against the real running model with text shaped
# like actual evidence packets (file paths, symbol names, short identifiers
# - not plain prose, and not a pathological single repeated character
# either): 6600 chars succeeded, 6990 failed. 5000 chars keeps a real
# margin below that boundary for token-density variance in different
# packets and the container being busier under real concurrent load than
# this manual test. A truncated embedding is still useful for similarity
# matching; the exact text match isn't needed, and a cache hit is always
# re-verified against current evidence regardless of how it was found.
MAX_EMBEDDING_CHARS = 5000

logger = logging.getLogger(__name__)


def _client(base_url: str | None = None) -> httpx.Client:
    return httpx.Client(base_url=base_url or os.environ.get("TEI_BASE_URL", "http://tei:80"))


def embed_text(text: str, base_url: str | None = None, timeout_seconds: float = 10.0) -> list[float] | None:
    # A 7000-char prompt (above the truncation budget below) measured 3.6s
    # against the real Ollama server this replaced - 5.0 left too little
    # margin under real concurrent load; this is a degrade-to-miss timeout,
    # not a build-blocking one, so erring toward more patience costs
    # nothing but a slightly slower cache lookup.
    if len(text) > MAX_EMBEDDING_CHARS:
        text = text[:MAX_EMBEDDING_CHARS]
    try:
        with _client(base_url) as client:
            response = client.post("/embed", json={"inputs": text}, timeout=timeout_seconds)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("embedding call failed (%s); treating cache as unavailable", type(exc).__name__)
        return None

    # TEI's /embed returns a list of embeddings, one per input text - a
    # single-item batch here since embed_text sends exactly one string.
    if not isinstance(data, list) or not data or not isinstance(data[0], list) or not data[0]:
        logger.warning("embedding response missing embedding array; treating cache as unavailable")
        return None
    embedding = data[0]
    if not all(isinstance(value, int | float) for value in embedding):
        logger.warning("embedding response contains non-numeric values; treating cache as unavailable")
        return None
    return [float(value) for value in embedding]
