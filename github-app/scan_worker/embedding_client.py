"""jina-embed client for evidence-packet and flash-review similarity caching.

Replaced Ollama+nomic-embed-text after a real, corrected 6-language
retrieval benchmark showed jina-embeddings-v2-base-code beating nomic on
every metric (75.0/93.1/95.8% vs 73.6/87.5/91.7% top-1/3/5 pooled), while
bge-m3 actually underperformed nomic. See jina_embed/server.py, which
serves this model behind a plain /embed endpoint.
"""

import functools
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

# Identifies which embedder produced a vector, recorded alongside every
# similarity-cache row (packet_cache.py, flash_review_cache.py) so a
# future embedder switch can't silently compare vectors from two
# different embedding spaces - see before_launch_fixes.md Batch 5 finding
# 8. Update this whenever the model or serving backend actually changes,
# in the same change that points JINA_EMBED_BASE_URL (or its replacement)
# at the new service - it's what makes an old row's cached vector
# correctly stop matching new queries the moment the embedder does.
CURRENT_EMBEDDER = "jina-embed:jina-embeddings-v2-base-code"

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=None)
def _client(base_url: str | None = None) -> httpx.Client:
    # Process-wide pooled client, one per distinct base_url, reused across
    # every embed_text call - matches app_server/http_client.py's identical
    # @lru_cache pooling convention (see get_github_api_client's docstring
    # there for the reasoning). Real gap this closes: this was a fresh
    # httpx.Client() - and a fresh TCP handshake to the jina-embed sidecar
    # - constructed and torn down on EVERY embed_text call, the exact
    # anti-pattern #183 fixed for the GitHub API and Redis clients but
    # never got swept into this file. embed_text is called once per cache-
    # eligible packet from packet_cache.py/flash_review_cache.py -
    # potentially dozens of times in a single AIRview build.
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
        # Not a `with` block: _client is now a pooled, cached-for-the-
        # process-lifetime client (see its own docstring) - closing it
        # after one call would break every subsequent call.
        client = _client(base_url)
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
