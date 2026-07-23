"""Per-installation similarity cache for AIRview writing-stage calls.

Callers must re-validate cached model output against current evidence
before serving it. This module only finds similar packets and stores raw
model output.
"""

import hashlib
import logging
import math

from scan_worker.db import (
    insert_evidence_packet_cache_row,
    list_recent_evidence_packet_cache_rows,
    record_evidence_packet_cache_hit,
)
from scan_worker.embedding_client import embed_text

SIMILARITY_THRESHOLD = 0.92

logger = logging.getLogger(__name__)


def _packet_text(packet: dict) -> str:
    from aletheore.toon_encoding import to_toon

    return to_toon(packet)


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def lookup_cached_result(
    dsn: str, installation_id: int, repo_full_name: str, packet: dict
) -> tuple[dict, str] | None:
    if not packet.get("cache_eligible"):
        return None

    try:
        text = _packet_text(packet)
        vector = embed_text(text)
        if vector is None:
            return None

        rows = list_recent_evidence_packet_cache_rows(dsn, installation_id, repo_full_name)
        if not rows:
            return None

        best_row = None
        best_score = 0.0
        for row in rows:
            score = _cosine_similarity(vector, row["embedding"])
            if score > best_score:
                best_score = score
                best_row = row

        if best_row is None or best_score < SIMILARITY_THRESHOLD:
            return None

        record_evidence_packet_cache_hit(dsn, best_row["id"])
        return best_row["model_output"], best_row["model_used"]
    except Exception as exc:
        logger.warning("evidence packet cache lookup failed (%s); treating as miss", type(exc).__name__)
        return None


def store_result(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    packet: dict,
    raw_model_output: dict,
    model_used: str,
) -> None:
    if not packet.get("cache_eligible"):
        return

    try:
        text = _packet_text(packet)
        vector = embed_text(text)
        if vector is None:
            logger.warning("embedding unavailable; skipping evidence packet cache write")
            return

        insert_evidence_packet_cache_row(
            dsn,
            installation_id,
            repo_full_name,
            _content_hash(text),
            vector,
            packet,
            raw_model_output,
            model_used,
        )
    except Exception as exc:
        logger.warning("evidence packet cache write failed (%s); continuing without cache", type(exc).__name__)
