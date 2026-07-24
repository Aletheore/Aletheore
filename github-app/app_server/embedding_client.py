"""Ollama embedding client for hosted MCP semantic search."""

import logging
import os

import httpx

EMBEDDING_MODEL = "nomic-embed-text"

logger = logging.getLogger(__name__)


def _client(base_url: str | None = None) -> httpx.Client:
    return httpx.Client(base_url=base_url or os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434"))


def embed_text(text: str, base_url: str | None = None, timeout_seconds: float = 5.0) -> list[float] | None:
    try:
        with _client(base_url) as client:
            response = client.post(
                "/api/embeddings",
                json={"model": EMBEDDING_MODEL, "prompt": text},
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("embedding call failed (%s); treating hosted MCP index as unavailable", type(exc).__name__)
        return None

    embedding = data.get("embedding")
    if not isinstance(embedding, list) or not embedding:
        logger.warning("embedding response missing embedding array; treating hosted MCP index as unavailable")
        return None
    if not all(isinstance(value, int | float) for value in embedding):
        logger.warning("embedding response contains non-numeric values; treating hosted MCP index as unavailable")
        return None
    return [float(value) for value in embedding]
