"""TEI (self-hosted all-MiniLM-L6-v2) embedding client for evidence-packet
similarity caching. Was Ollama, then nomic-embed-text-v1.5 (which
repeatedly OOM-killed in production - see Dockerfile.tei); replaced with
this much smaller model for real reliability on a resource-constrained
host (see tei/download_and_patch.py)."""

import logging
import os

import httpx

EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# The model's own real, hard-trained context is 256 tokens
# (sentence_bert_config.json's max_seq_length on the HF repo) - much
# shorter than nomic's 2048, the trade this smaller/more-reliable model
# makes. TEI's auto_truncate=true means it silently truncates past this
# rather than erroring, so the char-based truncation below is a courtesy
# (avoid sending obviously-wasted bytes over the wire), not the only line
# of defense. A truncated embedding is still useful for similarity
# matching; the exact text match isn't needed, and a cache hit is always
# re-verified against current evidence regardless of how it was found.
MAX_EMBEDDING_CHARS = 1000

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
