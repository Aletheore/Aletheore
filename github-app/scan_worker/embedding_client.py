"""jina-embed client for evidence-packet and flash-review similarity caching.

Replaced Ollama+nomic-embed-text after a real, corrected 6-language
retrieval benchmark showed jina-embeddings-v2-base-code beating nomic on
every metric (75.0/93.1/95.8% vs 73.6/87.5/91.7% top-1/3/5 pooled), while
bge-m3 actually underperformed nomic. See jina_embed/server.py, which
serves this model behind a plain /embed endpoint.
"""

import logging
import os

import httpx

# jina-embeddings-v2-base-code supports an 8192-token context (vs nomic's
# hard 2048), but this cap is kept conservative rather than re-tuned
# against the new model's real limit: it was already comfortably safe for
# real evidence-packet sizes under nomic, and a truncated embedding is
# still useful for similarity matching - the exact text match isn't
# needed, and a cache hit is always re-verified against current evidence
# regardless of how it was found.
MAX_EMBEDDING_CHARS = 5000

logger = logging.getLogger(__name__)


def _client(base_url: str | None = None) -> httpx.Client:
    return httpx.Client(base_url=base_url or os.environ.get("JINA_EMBED_BASE_URL", "http://jina-embed:80"))


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
                "/embed",
                json={"text": text},
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
