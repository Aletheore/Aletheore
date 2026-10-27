"""Recommendation-only search of the public skills.sh registry.

The registry's `/api/search` endpoint is the same public endpoint used by the
Vercel Skills CLI. It is not a documented stable API, so this adapter is
deliberately small, bounded, and failure-tolerant. Results are metadata only:
they are never downloaded, installed, or treated as vetted Hallbook skills.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import quote

from aletheore._lazy import lazy_module

httpx = lazy_module("httpx")
logger = logging.getLogger(__name__)

SEARCH_URL = "https://skills.sh/api/search"
SEARCH_TIMEOUT = 5.0
MAX_QUERY_CHARS = 200
MAX_RESULTS = 8
_SAFE_SLUG = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+){2}")


def _text(value: object, limit: int = 120) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def search_external_skills(query: str, limit: int = 5) -> dict:
    """Search skills.sh for a task description; return safe, non-installable metadata."""
    query = " ".join(query.split())[:MAX_QUERY_CHARS]
    try:
        limit = int(limit)
    except (TypeError, ValueError, OverflowError):
        limit = 5
    limit = max(1, min(limit, MAX_RESULTS))
    if len(query) < 2:
        return {"status": "skipped", "results": []}
    try:
        response = httpx.get(
            SEARCH_URL,
            params={"q": query, "limit": str(limit)},
            timeout=httpx.Timeout(SEARCH_TIMEOUT, connect=2.0),
            follow_redirects=False,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.debug("skills.sh search unavailable: %s", exc.__class__.__name__)
        return {"status": "unavailable", "results": []}

    raw_results = payload.get("skills") if isinstance(payload, dict) else None
    if not isinstance(raw_results, list):
        return {"status": "unavailable", "results": []}

    results = []
    for raw in raw_results[:limit]:
        if not isinstance(raw, dict):
            continue
        name = _text(raw.get("name"), 80)
        slug = _text(raw.get("id"), 240)
        source = _text(raw.get("source"), 120)
        installs = raw.get("installs")
        if (
            not name
            or not _SAFE_SLUG.fullmatch(slug)
            or any(part in {".", ".."} for part in slug.split("/"))
        ):
            continue
        source = source or "/".join(slug.split("/")[:2])
        if not isinstance(installs, int) or isinstance(installs, bool) or installs < 0:
            installs = 0
        results.append(
            {
                "name": name,
                "slug": slug,
                "source": source,
                "installs": installs,
                "registry_url": "https://skills.sh/" + quote(slug, safe="/"),
                "trust": "unvetted; review source and security report before adding to Hallbook",
            }
        )
    return {"status": "ok", "results": results}
