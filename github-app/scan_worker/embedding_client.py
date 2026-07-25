"""Ollama embedding client for evidence-packet similarity caching."""

import logging
import os

import httpx

EMBEDDING_MODEL = "nomic-embed-text"

# nomic-embed-text supports an 8192-token context, but Ollama's own default
# (2048) is much smaller and silently truncates/errors past it - confirmed
# in production: every real embedding call failed with "input (N tokens) is
# too large to process" against the 2048 default, so the cache never
# recorded a single hit despite running for 38 hours. num_ctx below asks
# Ollama to actually use the model's real capacity.
EMBEDDING_NUM_CTX = 8192

# A conservative ~3 chars/token estimate for TOON-encoded evidence (denser
# than prose - lots of short identifiers and punctuation) keeps even the
# largest real packets seen in production (52k+ tokens) under the model's
# context window, rather than sending something guaranteed to fail. A
# truncated embedding is still useful for similarity matching; the exact
# text match isn't needed, and a cache hit is always re-verified against
# current evidence regardless of how it was found.
MAX_EMBEDDING_CHARS = EMBEDDING_NUM_CTX * 3

logger = logging.getLogger(__name__)


def _client(base_url: str | None = None) -> httpx.Client:
    return httpx.Client(base_url=base_url or os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434"))


def embed_text(text: str, base_url: str | None = None, timeout_seconds: float = 5.0) -> list[float] | None:
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
