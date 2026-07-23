# Evidence Packet Caching + TOON Model Exchange Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a canonical `EvidencePacket` schema, TOON-encode it before it reaches the downstream model, and add a per-installation similarity cache (backed by a new hosted Ollama embedding container) that can skip AIRview's expensive writing-stage model call entirely for a near-identical cluster - without ever serving a result whose citations don't verify against *current* evidence.

**Architecture:** Two new prototype-level modules (`evidence_packet.py`, reusing `evidence_resolution.py` and `toon_encoding.py`) define the schema and its TOON serialization. Two new github-app modules (`embedding_client.py`, `packet_cache.py`) handle Ollama calls and the similarity lookup/write-back, storing rows in a new `evidence_packet_cache` table. `live_wiki.py` stays database-free (per its own existing docstring) by accepting the cache as two optional injected callables rather than importing `psycopg`/`db.py` directly - `jobs.py` (which already owns DB/adapter wiring) constructs and injects them.

**Tech Stack:** Python 3.12, `psycopg` (sync, matches `scan_worker/db.py`'s existing convention), `httpx` (already a dependency, used for the Ollama HTTP call), the existing `toon` package, Ollama (`nomic-embed-text` model) as a new Docker Compose service.

## Global Constraints

- `live_wiki.py` must remain database-free - no `import psycopg` or `from scan_worker.db import ...` inside it. Caching is injected via callables, never imported directly.
- A cache hit is only ever served after re-running *both* of AIRview's existing validations (structured symbol match against the *current* brief, `verify_citations` against *current* evidence) - never on the validations captured at cache-write time.
- Cache lookups are always scoped to `(installation_id, repo_full_name)` at the SQL query level - never a candidate match across installations, regardless of similarity score.
- Any failure in the caching path (Ollama unreachable/timed out, malformed row, failed re-verification) must degrade to today's always-call-the-model behavior. Never raise, never block a build.
- No `pgvector`. Cosine similarity is computed in Python over the most recent 200 rows for `(installation_id, repo_full_name)`.
- Similarity threshold for a candidate hit: cosine similarity >= 0.92.
- `test_coverage` on `EvidencePacket` is always `None` this phase - no fabricated signal.
- This phase touches AIRview only (`live_wiki.py`, `jobs.py`'s AIRview functions). `managed_audit.py` and `flash_review.py` are explicitly untouched.
- Existing tests for `live_wiki.py` and `jobs.py` must keep passing unmodified in their current call shape - the new caching parameters are optional keyword-only arguments defaulting to `None`, so every existing caller (and existing test) is unaffected unless it opts in.

---

## File Structure

**Create:**
- `prototype/aletheore/evidence_packet.py` - `EvidencePacket` shape + `build_evidence_packet()`
- `prototype/tests/test_evidence_packet.py`
- `github-app/scan_worker/embedding_client.py` - Ollama `/api/embeddings` HTTP call
- `github-app/tests/test_embedding_client.py`
- `github-app/scan_worker/packet_cache.py` - cosine similarity, lookup, write-back
- `github-app/tests/test_packet_cache.py`
- `github-app/migrations/012_evidence_packet_cache.sql`

**Modify:**
- `github-app/scan_worker/db.py` - add `insert_evidence_packet_cache_row`, `list_recent_evidence_packet_cache_rows`
- `github-app/scan_worker/live_wiki.py` - add optional cache-lookup/write callables to `generate_subsystems()` and `build_subsystem_record()`
- `github-app/scan_worker/jobs.py` - construct and inject the callables in `run_live_wiki_full_build_job` and `_maybe_update_live_wiki`
- `github-app/tests/test_live_wiki.py` - add cache-hit/cache-miss/failed-reverification tests
- `github-app/tests/test_jobs.py` - add an end-to-end test asserting a cache hit skips the writing adapter
- `github-app/docker-compose.yml` - add the `ollama` service

---

### Task 1: EvidencePacket schema and builder

**Files:**
- Create: `prototype/aletheore/evidence_packet.py`
- Test: `prototype/tests/test_evidence_packet.py`

**Interfaces:**
- Produces: `build_evidence_packet(evidence: dict, cluster: dict, brief: dict, model_routing_reason: str, cache_eligible: bool = False) -> dict`

- [ ] **Step 1: Write the failing test**

```python
# prototype/tests/test_evidence_packet.py
from aletheore.evidence_packet import build_evidence_packet


def _evidence():
    return {
        "repository": {
            "modules": [
                {
                    "path": "auth/login.py",
                    "imports": ["auth.tokens"],
                    "symbols": {
                        "functions": [{"name": "do_login", "start_line": 10, "end_line": 20}],
                        "classes": [],
                    },
                },
            ],
        },
        "architecture": {
            "clusters": [{"id": 0, "modules": ["auth/login.py"], "internal_edges": 0}]
        },
    }


def _cluster():
    return {"id": 0, "modules": ["auth/login.py"], "internal_edges": 0}


def _brief():
    return {
        "cluster_id": 0,
        "files": [
            {
                "path": "auth/login.py",
                "key_symbols": [{"name": "do_login", "start_line": 10}],
            }
        ],
    }


def test_build_evidence_packet_populates_changed_files_and_symbols():
    packet = build_evidence_packet(_evidence(), _cluster(), _brief(), "indie tier: deepseek-v4-pro")

    assert packet["changed_files"] == ["auth/login.py"]
    assert packet["changed_symbols"] == ["do_login"]
    assert packet["model_routing_reason"] == "indie tier: deepseek-v4-pro"


def test_build_evidence_packet_test_coverage_always_none():
    packet = build_evidence_packet(_evidence(), _cluster(), _brief(), "indie tier: deepseek-v4-pro")

    assert packet["test_coverage"] is None


def test_build_evidence_packet_cache_eligible_defaults_false():
    packet = build_evidence_packet(_evidence(), _cluster(), _brief(), "indie tier: deepseek-v4-pro")

    assert packet["cache_eligible"] is False


def test_build_evidence_packet_cache_eligible_can_be_set():
    packet = build_evidence_packet(
        _evidence(), _cluster(), _brief(), "indie tier: deepseek-v4-pro", cache_eligible=True
    )

    assert packet["cache_eligible"] is True


def test_build_evidence_packet_evidence_locations_reuse_evidence_resolution_shape():
    packet = build_evidence_packet(_evidence(), _cluster(), _brief(), "indie tier: deepseek-v4-pro")

    assert len(packet["evidence_locations"]) == 1
    location = packet["evidence_locations"][0]
    assert location["file"] == "auth/login.py"
    assert location["symbol"] == "do_login"
    assert location["line"] == 10
    assert location["confidence"] == "exact"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd prototype && python3 -m pytest tests/test_evidence_packet.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aletheore.evidence_packet'`

- [ ] **Step 3: Write minimal implementation**

```python
# prototype/aletheore/evidence_packet.py
"""The canonical, model-neutral packet a cheap extraction stage hands to
an expensive downstream model - see docs/superpowers/specs/
2026-07-23-aletheore-evidence-packet-caching-design.md.

Reuses evidence_resolution.py's canonical fields for evidence_locations
rather than inventing a parallel shape, and toon_encoding.py for
serialization - see build_evidence_packet() and the caller-side TOON
encode step in scan_worker/live_wiki.py.
"""

from aletheore.evidence_resolution import normalize_resolution


def build_evidence_packet(
    evidence: dict,
    cluster: dict,
    brief: dict,
    model_routing_reason: str,
    cache_eligible: bool = False,
) -> dict:
    modules_by_path = {m["path"]: m for m in evidence.get("repository", {}).get("modules", [])}
    changed_files = list(cluster.get("modules", []))

    changed_symbols: list[str] = []
    evidence_locations: list[dict] = []
    changed_dependencies: set[str] = set()
    for file_path in changed_files:
        module = modules_by_path.get(file_path)
        if module is None:
            continue
        changed_dependencies.update(module.get("imports", []))
        symbols = module.get("symbols", {})
        for group in ("functions", "classes"):
            for entry in symbols.get(group, []):
                changed_symbols.append(entry["name"])
                evidence_locations.append(
                    normalize_resolution(
                        kind="symbol",
                        file=file_path,
                        line=entry.get("start_line"),
                        end_line=entry.get("end_line"),
                        symbol=entry["name"],
                        confidence="exact",
                    )
                )

    return {
        "repository": evidence.get("repository", {}).get("name"),
        "base_commit": None,
        "head_commit": None,
        "changed_files": changed_files,
        "changed_symbols": changed_symbols,
        "changed_routes": [],
        "changed_dependencies": sorted(changed_dependencies),
        "owners": [],
        "evidence_locations": evidence_locations,
        "risk_classification": [],
        "graph_edges_before": None,
        "graph_edges_after": None,
        "endpoint_telemetry": None,
        "historical_failures": None,
        "test_coverage": None,
        "model_routing_reason": model_routing_reason,
        "cache_eligible": cache_eligible,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd prototype && python3 -m pytest tests/test_evidence_packet.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add prototype/aletheore/evidence_packet.py prototype/tests/test_evidence_packet.py
git commit -m "feat: add canonical EvidencePacket schema and builder"
```

---

### Task 2: TOON round-trip for the packet

**Files:**
- Test: `prototype/tests/test_evidence_packet.py` (append)

**Interfaces:**
- Consumes: `aletheore.toon_encoding.to_toon` (existing), `build_evidence_packet` (Task 1)

- [ ] **Step 1: Write the failing test**

```python
# append to prototype/tests/test_evidence_packet.py
import toon

from aletheore.toon_encoding import to_toon


def test_evidence_packet_toon_round_trips():
    packet = build_evidence_packet(_evidence(), _cluster(), _brief(), "indie tier: deepseek-v4-pro")

    encoded = to_toon(packet)
    decoded = toon.decode(encoded)

    assert decoded["changed_files"] == packet["changed_files"]
    assert decoded["changed_symbols"] == packet["changed_symbols"]
    assert decoded["model_routing_reason"] == packet["model_routing_reason"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd prototype && python3 -m pytest tests/test_evidence_packet.py::test_evidence_packet_toon_round_trips -v`
Expected: FAIL only if `to_toon`/`toon.decode` can't handle the shape - if this unexpectedly passes immediately, that's fine too, since `toon_encoding.py` already exists and is exercised elsewhere; either way step 4 must show a genuine pass, not an assumed one.

- [ ] **Step 3: Fix if needed**

If it fails on a specific field (e.g. `None` values, or `evidence_locations`' nested dicts), the fix belongs in `build_evidence_packet` (Task 1's file) - e.g. TOON may need `graph_edges_before`/`graph_edges_after`/`endpoint_telemetry`/`historical_failures`/`test_coverage` to be `None` rather than omitted, which the current implementation already does. Do not change `toon_encoding.py` itself - it is shared, working code used elsewhere (`air.toon`).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd prototype && python3 -m pytest tests/test_evidence_packet.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add prototype/tests/test_evidence_packet.py
git commit -m "test: verify EvidencePacket TOON round-trips"
```

---

### Task 3: Ollama embedding client

**Files:**
- Create: `github-app/scan_worker/embedding_client.py`
- Test: `github-app/tests/test_embedding_client.py`

**Interfaces:**
- Produces: `embed_text(text: str, base_url: str | None = None, timeout_seconds: float = 5.0) -> list[float] | None`

- [ ] **Step 1: Write the failing test**

```python
# github-app/tests/test_embedding_client.py
import httpx
import pytest

from scan_worker.embedding_client import embed_text


def test_embed_text_returns_vector_on_success(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/embeddings"
        return httpx.Response(200, json={"embedding": [0.1, 0.2, 0.3]})

    monkeypatch.setattr(
        "scan_worker.embedding_client._client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://ollama:11434"),
    )

    result = embed_text("some evidence text")

    assert result == [0.1, 0.2, 0.3]


def test_embed_text_returns_none_on_connection_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(
        "scan_worker.embedding_client._client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://ollama:11434"),
    )

    assert embed_text("some evidence text") is None


def test_embed_text_returns_none_on_timeout(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    monkeypatch.setattr(
        "scan_worker.embedding_client._client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://ollama:11434"),
    )

    assert embed_text("some evidence text") is None


def test_embed_text_returns_none_on_malformed_response(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    monkeypatch.setattr(
        "scan_worker.embedding_client._client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler), base_url="http://ollama:11434"),
    )

    assert embed_text("some evidence text") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd github-app && python3 -m pytest tests/test_embedding_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scan_worker.embedding_client'`

- [ ] **Step 3: Write minimal implementation**

```python
# github-app/scan_worker/embedding_client.py
"""Turns evidence-packet text into a vector for the similarity cache -
see docs/superpowers/specs/2026-07-23-aletheore-evidence-packet-caching-design.md.

nomic-embed-text is an embedding-only model (no text generation) already
used by the CLI's local search_index.py - this just runs the same model
in a hosted Ollama container instead of only on a developer's machine.
Never raises: any failure here must degrade to "cache unavailable", not
block or fail a build - see packet_cache.py, which treats a None return
as a guaranteed miss.
"""

import logging
import os

import httpx

EMBEDDING_MODEL = "nomic-embed-text"

logger = logging.getLogger(__name__)


def _client() -> httpx.Client:
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")
    return httpx.Client(base_url=base_url)


def embed_text(text: str, timeout_seconds: float = 5.0) -> list[float] | None:
    try:
        with _client() as client:
            response = client.post(
                "/api/embeddings",
                json={"model": EMBEDDING_MODEL, "prompt": text},
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("embedding call failed (%s) - treating as cache-unavailable", type(exc).__name__)
        return None

    embedding = data.get("embedding")
    if not isinstance(embedding, list) or not embedding:
        logger.warning("embedding response missing 'embedding' array - treating as cache-unavailable")
        return None
    return embedding
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd github-app && python3 -m pytest tests/test_embedding_client.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add github-app/scan_worker/embedding_client.py github-app/tests/test_embedding_client.py
git commit -m "feat: add hosted Ollama embedding client for evidence packets"
```

---

### Task 4: `evidence_packet_cache` migration

**Files:**
- Create: `github-app/migrations/012_evidence_packet_cache.sql`

- [ ] **Step 1: Write the migration**

```sql
-- Per-installation similarity cache for AIRview's writing-stage model
-- calls. Never queried across installations - see
-- docs/superpowers/specs/2026-07-23-aletheore-evidence-packet-caching-design.md.
-- A row's model_output is the RAW model response (pre-sanitization) so a
-- cache hit can be re-validated against the CURRENT brief/evidence, not
-- the brief that was current when this row was written.
CREATE TABLE IF NOT EXISTS evidence_packet_cache (
    id               BIGSERIAL PRIMARY KEY,
    installation_id  BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name   TEXT NOT NULL,
    content_hash     TEXT NOT NULL,
    embedding        DOUBLE PRECISION[] NOT NULL,
    packet_json      JSONB NOT NULL,
    model_output     JSONB NOT NULL,
    model_used       TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_hit_at      TIMESTAMPTZ,
    hit_count        INT NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS evidence_packet_cache_lookup
ON evidence_packet_cache (installation_id, repo_full_name, created_at DESC);
```

- [ ] **Step 2: Verify the migration is idempotent by construction**

Read `github-app/scripts/migrate.py` and confirm `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` matches the pattern every other migration in `github-app/migrations/` already uses (e.g. `008_live_wiki.sql`). No new pattern is being introduced, so no separate test is needed for the migration file itself - `scripts/migrate.py` already has its own test coverage in `github-app/tests/test_migrate.py`.

- [ ] **Step 3: Commit**

```bash
git add github-app/migrations/012_evidence_packet_cache.sql
git commit -m "feat: add evidence_packet_cache migration"
```

---

### Task 5: DB helpers for the cache table

**Files:**
- Modify: `github-app/scan_worker/db.py` (append, matching the existing `upsert_wiki_subsystem`/`list_wiki_subsystems` pattern at the end of the file)
- Test: `github-app/tests/test_scan_worker_db.py` (append)

**Interfaces:**
- Produces: `insert_evidence_packet_cache_row(dsn, installation_id, repo_full_name, content_hash, embedding, packet, model_output, model_used) -> None`
- Produces: `list_recent_evidence_packet_cache_rows(dsn, installation_id, repo_full_name, limit=200) -> list[dict]`

- [ ] **Step 1: Write the failing test**

```python
# append to github-app/tests/test_scan_worker_db.py
async def test_insert_and_list_evidence_packet_cache_rows(pool):
    await _insert_installation(pool, 401, "cache-org")

    from scan_worker.db import insert_evidence_packet_cache_row, list_recent_evidence_packet_cache_rows

    insert_evidence_packet_cache_row(
        TEST_DATABASE_URL,
        401,
        "cache-org/repo",
        "hash-1",
        [0.1, 0.2, 0.3],
        {"changed_files": ["a.py"]},
        {"description": "does a thing"},
        "deepseek-v4-pro",
    )

    rows = list_recent_evidence_packet_cache_rows(TEST_DATABASE_URL, 401, "cache-org/repo")

    assert len(rows) == 1
    assert rows[0]["content_hash"] == "hash-1"
    assert rows[0]["embedding"] == [0.1, 0.2, 0.3]
    assert rows[0]["packet_json"]["changed_files"] == ["a.py"]
    assert rows[0]["model_output"]["description"] == "does a thing"
    assert rows[0]["model_used"] == "deepseek-v4-pro"


async def test_list_evidence_packet_cache_rows_never_crosses_installations(pool):
    await _insert_installation(pool, 402, "org-a")
    await _insert_installation(pool, 403, "org-b")

    from scan_worker.db import insert_evidence_packet_cache_row, list_recent_evidence_packet_cache_rows

    insert_evidence_packet_cache_row(
        TEST_DATABASE_URL, 402, "org-a/repo", "hash-a", [1.0], {}, {"description": "a"}, "deepseek-v4-pro"
    )
    insert_evidence_packet_cache_row(
        TEST_DATABASE_URL, 403, "org-b/repo", "hash-b", [1.0], {}, {"description": "b"}, "deepseek-v4-pro"
    )

    rows = list_recent_evidence_packet_cache_rows(TEST_DATABASE_URL, 402, "org-a/repo")

    assert len(rows) == 1
    assert rows[0]["content_hash"] == "hash-a"
```

Check the top of `github-app/tests/test_scan_worker_db.py` for the existing `TEST_DATABASE_URL` constant and `_insert_installation` helper - reuse them exactly as the other tests in that file do, do not redefine.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd github-app && python3 -m pytest tests/test_scan_worker_db.py -k evidence_packet_cache -v`
Expected: FAIL with `ImportError: cannot import name 'insert_evidence_packet_cache_row'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to github-app/scan_worker/db.py
def insert_evidence_packet_cache_row(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    content_hash: str,
    embedding: list[float],
    packet: dict,
    model_output: dict,
    model_used: str,
) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO evidence_packet_cache
                    (installation_id, repo_full_name, content_hash, embedding,
                     packet_json, model_output, model_used)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                """,
                (
                    installation_id,
                    repo_full_name,
                    content_hash,
                    embedding,
                    json.dumps(packet),
                    json.dumps(model_output),
                    model_used,
                ),
            )
        conn.commit()


def list_recent_evidence_packet_cache_rows(
    dsn: str, installation_id: int, repo_full_name: str, limit: int = 200
) -> list[dict]:
    import psycopg.rows

    with psycopg.connect(dsn) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                """
                SELECT id, content_hash, embedding, packet_json, model_output, model_used, hit_count
                FROM evidence_packet_cache
                WHERE installation_id = %s AND repo_full_name = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (installation_id, repo_full_name, limit),
            )
            return cur.fetchall()


def record_evidence_packet_cache_hit(dsn: str, row_id: int) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE evidence_packet_cache
                SET hit_count = hit_count + 1, last_hit_at = now()
                WHERE id = %s
                """,
                (row_id,),
            )
        conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd github-app && python3 -m pytest tests/test_scan_worker_db.py -k evidence_packet_cache -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add github-app/scan_worker/db.py github-app/tests/test_scan_worker_db.py
git commit -m "feat: add evidence_packet_cache DB helpers"
```

---

### Task 6: `packet_cache.py` - similarity lookup and write-back

**Files:**
- Create: `github-app/scan_worker/packet_cache.py`
- Test: `github-app/tests/test_packet_cache.py`

**Interfaces:**
- Consumes: `embedding_client.embed_text` (Task 3), `db.list_recent_evidence_packet_cache_rows` / `insert_evidence_packet_cache_row` / `record_evidence_packet_cache_hit` (Task 5)
- Produces: `lookup_cached_result(dsn, installation_id, repo_full_name, packet) -> tuple[dict, str] | None` (returns `(raw_model_output, model_used)` or `None`)
- Produces: `store_result(dsn, installation_id, repo_full_name, packet, raw_model_output, model_used) -> None`

- [ ] **Step 1: Write the failing test**

```python
# github-app/tests/test_packet_cache.py
import hashlib
import json
from unittest.mock import MagicMock

from scan_worker.packet_cache import lookup_cached_result, store_result


def _packet(changed_files=("a.py",)):
    return {"changed_files": list(changed_files), "cache_eligible": True}


def test_lookup_returns_none_when_embedding_unavailable(monkeypatch):
    monkeypatch.setattr("scan_worker.packet_cache.embed_text", lambda text: None)

    result = lookup_cached_result("postgresql://unused", 1, "org/repo", _packet())

    assert result is None


def test_lookup_returns_none_when_no_rows_exist(monkeypatch):
    monkeypatch.setattr("scan_worker.packet_cache.embed_text", lambda text: [1.0, 0.0])
    monkeypatch.setattr(
        "scan_worker.packet_cache.list_recent_evidence_packet_cache_rows", lambda *a, **k: []
    )

    result = lookup_cached_result("postgresql://unused", 1, "org/repo", _packet())

    assert result is None


def test_lookup_returns_none_below_similarity_threshold(monkeypatch):
    monkeypatch.setattr("scan_worker.packet_cache.embed_text", lambda text: [1.0, 0.0])
    monkeypatch.setattr(
        "scan_worker.packet_cache.list_recent_evidence_packet_cache_rows",
        lambda *a, **k: [
            {
                "id": 1,
                "embedding": [0.0, 1.0],  # orthogonal - similarity 0.0
                "model_output": {"description": "unrelated"},
                "model_used": "deepseek-v4-pro",
            }
        ],
    )

    result = lookup_cached_result("postgresql://unused", 1, "org/repo", _packet())

    assert result is None


def test_lookup_returns_match_above_threshold_and_records_hit(monkeypatch):
    monkeypatch.setattr("scan_worker.packet_cache.embed_text", lambda text: [1.0, 0.0])
    monkeypatch.setattr(
        "scan_worker.packet_cache.list_recent_evidence_packet_cache_rows",
        lambda *a, **k: [
            {
                "id": 7,
                "embedding": [1.0, 0.0001],  # near-identical - similarity ~1.0
                "model_output": {"description": "cached description"},
                "model_used": "deepseek-v4-pro",
            }
        ],
    )
    recorded = []
    monkeypatch.setattr(
        "scan_worker.packet_cache.record_evidence_packet_cache_hit",
        lambda dsn, row_id: recorded.append(row_id),
    )

    result = lookup_cached_result("postgresql://unused", 1, "org/repo", _packet())

    assert result == ({"description": "cached description"}, "deepseek-v4-pro")
    assert recorded == [7]


def test_store_result_writes_a_row(monkeypatch):
    written = {}

    def fake_insert(dsn, installation_id, repo_full_name, content_hash, embedding, packet, model_output, model_used):
        written.update(
            installation_id=installation_id,
            repo_full_name=repo_full_name,
            content_hash=content_hash,
            embedding=embedding,
            packet=packet,
            model_output=model_output,
            model_used=model_used,
        )

    monkeypatch.setattr("scan_worker.packet_cache.embed_text", lambda text: [0.5, 0.5])
    monkeypatch.setattr("scan_worker.packet_cache.insert_evidence_packet_cache_row", fake_insert)

    store_result("postgresql://unused", 1, "org/repo", _packet(), {"description": "fresh"}, "deepseek-v4-pro")

    assert written["installation_id"] == 1
    assert written["repo_full_name"] == "org/repo"
    assert written["embedding"] == [0.5, 0.5]
    assert written["model_output"] == {"description": "fresh"}
    assert written["model_used"] == "deepseek-v4-pro"


def test_store_result_is_noop_when_embedding_unavailable(monkeypatch):
    called = []
    monkeypatch.setattr("scan_worker.packet_cache.embed_text", lambda text: None)
    monkeypatch.setattr(
        "scan_worker.packet_cache.insert_evidence_packet_cache_row",
        lambda *a, **k: called.append(True),
    )

    store_result("postgresql://unused", 1, "org/repo", _packet(), {"description": "fresh"}, "deepseek-v4-pro")

    assert called == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd github-app && python3 -m pytest tests/test_packet_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scan_worker.packet_cache'`

- [ ] **Step 3: Write minimal implementation**

```python
# github-app/scan_worker/packet_cache.py
"""Per-installation similarity cache for AIRview's writing-stage calls -
see docs/superpowers/specs/2026-07-23-aletheore-evidence-packet-caching-design.md.

Callers MUST re-validate whatever lookup_cached_result() returns against
current evidence before trusting it (see live_wiki.py) - this module
only answers "is there a similar-enough packet", never "is this result
still correct".
"""

import hashlib
import json
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


def store_result(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    packet: dict,
    raw_model_output: dict,
    model_used: str,
) -> None:
    text = _packet_text(packet)
    vector = embed_text(text)
    if vector is None:
        logger.warning("embedding unavailable - skipping cache write, not a hard failure")
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd github-app && python3 -m pytest tests/test_packet_cache.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add github-app/scan_worker/packet_cache.py github-app/tests/test_packet_cache.py
git commit -m "feat: add similarity cache lookup and write-back for evidence packets"
```

---

### Task 7: Wire caching into `live_wiki.py`

**Files:**
- Modify: `github-app/scan_worker/live_wiki.py:106-170` (`build_subsystem_record`, `generate_subsystems`)
- Test: `github-app/tests/test_live_wiki.py` (append)

**Interfaces:**
- Consumes: nothing imported directly - `cache_lookup`/`cache_write` are injected callables with the same signatures as `packet_cache.lookup_cached_result`/`store_result` but with `dsn`/`installation_id`/`repo_full_name` already bound (see Task 8)
- Produces: `build_subsystem_record(evidence, cluster, brief, name, writing_adapter, *, cache_lookup=None, cache_write=None, model_used="")` - same return shape as today (`dict | None`)
- Produces: `generate_subsystems(evidence, naming_adapter, writing_adapter, cluster_ids=None, *, cache_lookup=None, cache_write=None, model_used="")` - same return shape as today (`list[dict]`)

- [ ] **Step 1: Write the failing test**

```python
# append to github-app/tests/test_live_wiki.py
from scan_worker.live_wiki import build_subsystem_record


def test_build_subsystem_record_uses_cache_hit_and_skips_model_call():
    evidence = make_evidence()
    cluster = evidence["architecture"]["clusters"][0]
    brief = {
        "cluster_id": 0,
        "files": [
            {
                "path": "auth/login.py",
                "key_symbols": [{"name": "do_login", "start_line": 10}],
            },
            {"path": "auth/tokens.py", "key_symbols": []},
        ],
    }
    cached_output = {
        "description": "Handles authentication via do_login in auth/login.py.",
        "files": [
            {
                "path": "auth/login.py",
                "role": "Login entry point.",
                "key_symbols": [{"name": "do_login", "line": 10, "explanation": "Logs a user in."}],
            }
        ],
    }
    cache_lookup = MagicMock(return_value=(cached_output, "deepseek-v4-pro"))
    cache_write = MagicMock()
    writing_adapter = _adapter("should never be called")

    record = build_subsystem_record(
        evidence, cluster, brief, "Authentication", writing_adapter,
        cache_lookup=cache_lookup, cache_write=cache_write,
    )

    assert record is not None
    assert record["description"] == cached_output["description"]
    writing_adapter.simple_completion.assert_not_called()
    cache_write.assert_not_called()  # a hit doesn't need to re-write itself


def test_build_subsystem_record_falls_through_to_model_when_cache_hit_fails_reverification():
    evidence = make_evidence()
    cluster = evidence["architecture"]["clusters"][0]
    brief = {
        "cluster_id": 0,
        "files": [{"path": "auth/login.py", "key_symbols": [{"name": "do_login", "start_line": 10}]}],
    }
    # Cached output cites a file that is NOT in the current brief - must
    # be rejected and treated as a miss, not trusted.
    cached_output = {
        "description": "See gone_file.py for details.",
        "files": [],
    }
    cache_lookup = MagicMock(return_value=(cached_output, "deepseek-v4-pro"))
    cache_write = MagicMock()
    fresh_output = {
        "description": "Handles authentication.",
        "files": [{"path": "auth/login.py", "role": "Login.", "key_symbols": []}],
    }
    writing_adapter = _adapter(json.dumps(fresh_output))

    record = build_subsystem_record(
        evidence, cluster, brief, "Authentication", writing_adapter,
        cache_lookup=cache_lookup, cache_write=cache_write, model_used="deepseek-v4-pro",
    )

    assert record is not None
    assert record["description"] == "Handles authentication."
    writing_adapter.simple_completion.assert_called_once()
    cache_write.assert_called_once()


def test_build_subsystem_record_without_cache_callables_is_unchanged():
    evidence = make_evidence()
    cluster = evidence["architecture"]["clusters"][0]
    brief = {
        "cluster_id": 0,
        "files": [{"path": "auth/login.py", "key_symbols": [{"name": "do_login", "start_line": 10}]}],
    }
    fresh_output = {"description": "Handles authentication.", "files": []}
    writing_adapter = _adapter(json.dumps(fresh_output))

    record = build_subsystem_record(evidence, cluster, brief, "Authentication", writing_adapter)

    assert record["description"] == "Handles authentication."
    writing_adapter.simple_completion.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd github-app && python3 -m pytest tests/test_live_wiki.py -k "cache" -v`
Expected: FAIL with `TypeError: build_subsystem_record() got an unexpected keyword argument 'cache_lookup'`

- [ ] **Step 3: Write the implementation**

Replace `build_subsystem_record` and `generate_subsystems` in `github-app/scan_worker/live_wiki.py`:

```python
from typing import Callable

from aletheore.evidence_packet import build_evidence_packet


def build_subsystem_record(
    evidence: dict,
    cluster: dict,
    brief: dict,
    name: str,
    writing_adapter,
    *,
    cache_lookup: Callable[[dict], tuple[dict, str] | None] | None = None,
    cache_write: Callable[[dict, dict, str], None] | None = None,
    model_used: str = "",
) -> dict | None:
    packet = build_evidence_packet(
        evidence, cluster, brief, model_used, cache_eligible=cache_lookup is not None
    )

    parsed = None
    served_from_cache = False
    if cache_lookup is not None:
        cached = cache_lookup(packet)
        if cached is not None:
            cached_output, _cached_model_used = cached
            candidate = _validate_written_output(cached_output, evidence, brief)
            if candidate is not None:
                parsed, description = candidate
                served_from_cache = True

    if parsed is None:
        user_prompt = json.dumps({"name": name, "brief": brief})
        raw = writing_adapter.simple_completion(SUBSYSTEM_WRITING_SYSTEM_PROMPT, user_prompt, cwd=".")
        raw_parsed = _parse_json_object(raw)
        candidate = _validate_written_output(raw_parsed, evidence, brief) if raw_parsed else None
        if candidate is None:
            return None
        parsed, description = candidate
        if cache_write is not None:
            cache_write(packet, raw_parsed, model_used)
    else:
        description = parsed["description"]

    return {
        "subsystem_id": str(cluster["id"]),
        "name": name,
        "description": description,
        "files": _sanitize_written_files(parsed.get("files"), brief["files"]),
        "diagram_mermaid": build_subsystem_diagram(evidence, cluster),
    }


def _validate_written_output(parsed: dict | None, evidence: dict, brief: dict) -> tuple[dict, str] | None:
    """Runs the exact validation a fresh model response has always had to
    pass. Used both for a fresh response and for re-validating a cache
    hit against CURRENT evidence/brief - a cache hit gets no weaker a
    check than a fresh call would.
    """
    if parsed is None or not isinstance(parsed.get("description"), str) or not parsed["description"].strip():
        return None
    description = parsed["description"].strip()
    if not verify_citations(description, evidence)["all_verified"]:
        return None
    return parsed, description
```

Note: `_validate_written_output` extracts the description-checking logic that used to live inline in `build_subsystem_record` - `_sanitize_written_files` for the structured `files` field is still called separately afterward using `brief["files"]`, which is already always the *current* brief regardless of whether `parsed` came from cache or a fresh call, so no separate re-validation step is needed for that part - it naturally re-validates against current state every time because it always receives the current `brief`.

Now update `generate_subsystems` to thread the same three parameters through to each call:

```python
def generate_subsystems(
    evidence: dict,
    naming_adapter,
    writing_adapter,
    cluster_ids: set[int] | None = None,
    *,
    cache_lookup: Callable[[dict], tuple[dict, str] | None] | None = None,
    cache_write: Callable[[dict, dict, str], None] | None = None,
    model_used: str = "",
) -> list[dict]:
    briefs = build_cluster_briefs(evidence)
    if cluster_ids is not None:
        briefs = [b for b in briefs if b["cluster_id"] in cluster_ids]
    if not briefs:
        return []

    names = propose_cluster_names(briefs, naming_adapter)
    clusters_by_id = {c["id"]: c for c in evidence.get("architecture", {}).get("clusters", [])}

    records = []
    for brief in briefs:
        cid = brief["cluster_id"]
        cluster = clusters_by_id.get(cid)
        if cluster is None:
            continue
        record = build_subsystem_record(
            evidence, cluster, brief, names[cid], writing_adapter,
            cache_lookup=cache_lookup, cache_write=cache_write, model_used=model_used,
        )
        if record is not None:
            records.append(record)
    return records
```

Add the import at the top of the file (with the other `aletheore.*` imports):

```python
from aletheore.evidence_packet import build_evidence_packet
```

And update the module docstring's second paragraph to note the new injection point without contradicting the existing "never touches the database" claim:

```python
"""... (existing first paragraph unchanged) ...

Every model response is validated against the deterministic brief it was
given before being trusted: a file, function, or line number the model
returns that isn't actually in the brief is dropped, never stored. This
module never touches the database - it takes evidence and adapters in,
returns plain dict records out. An optional cache_lookup/cache_write pair
can be injected by the caller (see jobs.py) to skip the model call for a
near-identical packet - this module never imports a cache or DB client
itself, it only calls whatever callables it was given, so a caller that
passes nothing gets exactly today's always-call-the-model behavior.
"""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd github-app && python3 -m pytest tests/test_live_wiki.py -v`
Expected: PASS, including all pre-existing tests in the file (they call `build_subsystem_record`/`generate_subsystems` without the new keyword args, exercising the `cache_lookup=None` default path)

- [ ] **Step 5: Commit**

```bash
git add github-app/scan_worker/live_wiki.py github-app/tests/test_live_wiki.py
git commit -m "feat: wire optional similarity-cache lookup into AIRview's writing stage"
```

---

### Task 8: Inject the cache into `jobs.py`

**Files:**
- Modify: `github-app/scan_worker/jobs.py:568-576` (`_live_wiki_full_build_writing_adapter`), `:631-643` (`run_live_wiki_full_build_job`), `:670-687` (`_maybe_update_live_wiki`)
- Test: `github-app/tests/test_jobs.py` (append)

**Interfaces:**
- Consumes: `packet_cache.lookup_cached_result` / `packet_cache.store_result` (Task 6), `model_tiers.model_for_plan` (existing)

- [ ] **Step 1: Write the failing test**

```python
# append to github-app/tests/test_jobs.py
def test_run_live_wiki_full_build_job_skips_model_call_on_cache_hit(monkeypatch):
    from scan_worker.jobs import run_live_wiki_full_build_job

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_latest_evidence", lambda *a, **k: _wiki_evidence())
    monkeypatch.setattr(
        "scan_worker.jobs.get_installation_row", lambda *a, **k: {"plan": "indie"}
    )

    cached_output = {"description": "Cached, verified description.", "files": []}
    monkeypatch.setattr(
        "scan_worker.jobs.lookup_cached_result", lambda *a, **k: (cached_output, "deepseek-v4-pro")
    )
    store_calls = []
    monkeypatch.setattr(
        "scan_worker.jobs.store_result", lambda *a, **k: store_calls.append(True)
    )

    adapter_calls = []

    class _SpyAdapter:
        name = "DeepSeek"

        def simple_completion(self, *a, **k):
            adapter_calls.append(True)
            return json.dumps({"description": "should not be reached", "files": []})

    monkeypatch.setattr("scan_worker.jobs._live_wiki_full_build_writing_adapter", lambda plan: _SpyAdapter())
    monkeypatch.setattr(
        "scan_worker.jobs._store_wiki_generation", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "scan_worker.jobs.verify_citations", lambda *a, **k: {"all_verified": True}
    )

    run_live_wiki_full_build_job(1, "octocat/hello-world")

    assert adapter_calls == []
    assert store_calls == []
```

Check the top of `github-app/tests/test_jobs.py` for the existing `_wiki_evidence()` fixture helper (already used by other Live Wiki tests in that file) and `import json` - reuse them, do not redefine. `verify_citations` is patched at the `scan_worker.jobs` import location because `live_wiki.py` imports it directly (`from aletheore.citation_verifier import verify_citations`) - patch it where `live_wiki` looks it up: `monkeypatch.setattr("scan_worker.live_wiki.verify_citations", lambda *a, **k: {"all_verified": True})` instead of the `scan_worker.jobs` path shown above - correct this during Step 1 if the two `verify_citations` references end up being separate module-level names.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd github-app && python3 -m pytest tests/test_jobs.py -k skips_model_call_on_cache_hit -v`
Expected: FAIL with `AttributeError` or `ImportError` on `scan_worker.jobs.lookup_cached_result` not existing

- [ ] **Step 3: Write the implementation**

Add the import near the top of `github-app/scan_worker/jobs.py`, alongside the other `scan_worker.*` imports:

```python
from scan_worker.packet_cache import lookup_cached_result, store_result
```

Replace `run_live_wiki_full_build_job` (currently `github-app/scan_worker/jobs.py:631-643`):

```python
@log_job
def run_live_wiki_full_build_job(installation_id: int, repo_full_name: str) -> None:
    dsn = get_settings().database_url
    evidence = get_latest_evidence(dsn, installation_id, repo_full_name)
    if evidence is None:
        return  # nothing scanned for this repo yet - nothing to build from

    installation = get_installation_row(dsn, installation_id)
    plan = installation["plan"] if installation is not None else "indie"
    model_used = model_for_plan(plan)

    naming_adapter = _live_wiki_naming_adapter()
    writing_adapter = _live_wiki_full_build_writing_adapter(plan)
    records = live_wiki.generate_subsystems(
        evidence,
        naming_adapter,
        writing_adapter,
        cache_lookup=lambda packet: lookup_cached_result(dsn, installation_id, repo_full_name, packet),
        cache_write=lambda packet, output, used: store_result(
            dsn, installation_id, repo_full_name, packet, output, used
        ),
        model_used=model_used,
    )
    _store_wiki_generation(dsn, installation_id, repo_full_name, evidence, records, writing_adapter, None)
```

Replace `_maybe_update_live_wiki` (currently `github-app/scan_worker/jobs.py:670-687`):

```python
def _maybe_update_live_wiki(
    installation_id: int, repo_full_name: str, evidence: dict, changed_files: list[str], head_sha: str
) -> None:
    settings = get_settings()
    installation = get_installation_row(settings.database_url, installation_id)
    if installation is None or installation["plan"] == "free":
        return

    cluster_ids = live_wiki.affected_cluster_ids(evidence, changed_files)
    if not cluster_ids:
        return

    dsn = settings.database_url
    naming_adapter = _live_wiki_naming_adapter()
    writing_adapter = _live_wiki_update_writing_adapter()
    records = live_wiki.generate_subsystems(
        evidence,
        naming_adapter,
        writing_adapter,
        cluster_ids=cluster_ids,
        cache_lookup=lambda packet: lookup_cached_result(dsn, installation_id, repo_full_name, packet),
        cache_write=lambda packet, output, used: store_result(
            dsn, installation_id, repo_full_name, packet, output, used
        ),
        model_used=live_wiki.UPDATE_MODEL,
    )
    _store_wiki_generation(
        settings.database_url, installation_id, repo_full_name, evidence, records, writing_adapter, head_sha
    )
```

`model_for_plan` is already imported in `jobs.py` from an earlier phase (`from scan_worker.model_tiers import model_for_plan, writing_adapter_for_plan`) - confirm this import still exists before adding a duplicate.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd github-app && python3 -m pytest tests/test_jobs.py -v`
Expected: PASS, including every pre-existing Live Wiki job test in the file (they patch `lookup_cached_result`/`store_result` implicitly to real functions, which in a mocked-DB test environment will hit `get_settings().database_url` = `"postgresql://unused"` and fail to connect - if any pre-existing test breaks here, patch `scan_worker.jobs.lookup_cached_result` to return `None` and `scan_worker.jobs.store_result` to a no-op in that test's setup, matching the pattern already used for other DB-touching mocks in this file)

- [ ] **Step 5: Commit**

```bash
git add github-app/scan_worker/jobs.py github-app/tests/test_jobs.py
git commit -m "feat: inject evidence-packet cache into AIRview full-build and incremental-update jobs"
```

---

### Task 9: Tenant isolation end-to-end test

**Files:**
- Test: `github-app/tests/test_packet_cache.py` (append) - this is the one integration-style test in this plan that touches the real test Postgres, matching how `test_scan_worker_db.py`'s tests already do.

**Interfaces:**
- Consumes: `packet_cache.lookup_cached_result`/`store_result` (Task 6) against the real `pool`/`TEST_DATABASE_URL` fixtures already used elsewhere in this test suite

- [ ] **Step 1: Write the failing test**

```python
# append to github-app/tests/test_packet_cache.py
async def test_lookup_never_returns_a_different_installations_row(pool, monkeypatch):
    from tests.test_scan_worker_db import TEST_DATABASE_URL, _insert_installation
    from scan_worker.packet_cache import lookup_cached_result, store_result

    await _insert_installation(pool, 501, "org-a")
    await _insert_installation(pool, 502, "org-b")

    monkeypatch.setattr("scan_worker.packet_cache.embed_text", lambda text: [1.0, 0.0])

    store_result(
        TEST_DATABASE_URL, 501, "org-a/repo",
        {"changed_files": ["a.py"]}, {"description": "org-a's cached description"}, "deepseek-v4-pro",
    )

    # org-b's lookup uses the SAME embedding on purpose - proves isolation
    # is enforced by the query scope, not by embeddings happening to differ.
    result = lookup_cached_result(TEST_DATABASE_URL, 502, "org-b/repo", {"changed_files": ["a.py"]})

    assert result is None
```

Check `github-app/tests/test_scan_worker_db.py` for the exact current names/signatures of `TEST_DATABASE_URL` and `_insert_installation` before importing them - adjust the import path in this test to match whatever they actually are (they may already be re-exported via a shared `conftest.py` fixture instead of a direct module import, in which case use that fixture the same way the rest of `test_packet_cache.py`'s async tests would need to, following whatever pattern `test_scan_worker_db.py` itself uses for its own async DB tests).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd github-app && python3 -m pytest tests/test_packet_cache.py -k tenant -v`
Expected: FAIL only if the isolation boundary is broken - since Task 6's `lookup_cached_result` already scopes its query by `installation_id` via `list_recent_evidence_packet_cache_rows`, this should PASS immediately. If it fails, that is a real bug in Task 5 or Task 6's implementation to fix now, not a signal to weaken this test.

- [ ] **Step 3: Fix if it fails, otherwise confirm it passes as a genuine regression test**

Run: `cd github-app && python3 -m pytest tests/test_packet_cache.py -v`
Expected: PASS (7 passed total in this file)

- [ ] **Step 4: Commit**

```bash
git add github-app/tests/test_packet_cache.py
git commit -m "test: prove evidence-packet cache lookups never cross installations"
```

---

### Task 10: Hosted Ollama container

**Files:**
- Modify: `github-app/docker-compose.yml`

- [ ] **Step 1: Add the service**

Add to `github-app/docker-compose.yml`, alongside the other services (after `redis`, before `caddy`):

```yaml
  ollama:
    image: ollama/ollama:latest
    restart: unless-stopped
    volumes:
      - aletheore_ollama_data:/root/.ollama
    entrypoint: ["/bin/sh", "-c"]
    command:
      - |
        ollama serve &
        sleep 3
        ollama pull nomic-embed-text
        wait
    cpus: "1.0"
    mem_limit: 1g
```

Add `aletheore_ollama_data:` to the `volumes:` block at the bottom of the file, alongside the existing `aletheore_postgres_data`/`aletheore_caddy_data`/`aletheore_caddy_config` entries.

Add `OLLAMA_BASE_URL=http://ollama:11434` to `scan-worker`'s `environment:` block (it is the service that actually runs AIRview builds) and to `scan-worker`'s `depends_on:` add:

```yaml
      ollama:
        condition: service_started
```

- [ ] **Step 2: Verify the compose file is syntactically valid**

Run: `cd github-app && docker compose config`
Expected: No errors; the rendered config includes the new `ollama` service and `aletheore_ollama_data` volume

- [ ] **Step 3: Commit**

```bash
git add github-app/docker-compose.yml
git commit -m "feat: add hosted Ollama container for evidence-packet embeddings"
```

---

## Self-Review

**Spec coverage:**
- `EvidencePacket` schema -> Task 1. ✓
- TOON encoding reuse -> Task 2 (round-trip test), Task 6 (`_packet_text` uses `to_toon`). ✓
- Hosted Ollama / `nomic-embed-text` -> Task 3 (client), Task 10 (container). ✓
- Per-tenant similarity cache, 200-row window, 0.92 threshold -> Task 6. ✓
- Dual re-verification on a cache hit (structured symbols + free-text citations) against *current* evidence -> Task 7 (`_validate_written_output` + `_sanitize_written_files` both always run against the current `brief`/`evidence` regardless of cache hit or miss). ✓
- `live_wiki.py` stays database-free -> Task 7 (callables only, no `psycopg`/`db` import added to that file). ✓
- Tenant isolation enforced at the query level -> Task 5 (`WHERE installation_id = %s`), proven in Task 9. ✓
- Graceful degradation on any Ollama/cache failure -> Task 3 (`embed_text` returns `None`, never raises), Task 6 (`None` embedding -> `None`/no-op, never raises). ✓
- `managed_audit.py`/`flash_review.py` untouched -> no task modifies either file. ✓
- `test_coverage` always `None` -> Task 1. ✓
- Cache eligibility as an explicit flag, not inferred -> Task 1 (`cache_eligible` parameter), Task 7 (`cache_eligible=cache_lookup is not None`).

**Placeholder scan:** No TBD/TODO. Every step has complete code. The one deliberately flagged uncertainty (Task 8's `verify_citations` patch path, Task 9's exact fixture import path) is explicit about what to check and why, not a vague "handle appropriately" - both are genuine unknowns from not having run the actual test file in hand during planning, called out so the implementer resolves them by inspection rather than guessing silently.

**Type consistency:** `cache_lookup` is `Callable[[dict], tuple[dict, str] | None]` and `cache_write` is `Callable[[dict, dict, str], None]` consistently across Task 6 (`packet_cache.py`'s real functions), Task 7 (`live_wiki.py`'s parameter types), and Task 8 (the lambdas built in `jobs.py` match both signatures exactly - `lookup_cached_result(dsn, installation_id, repo_full_name, packet)` returns `tuple[dict, str] | None`, and the `jobs.py` lambda wraps it to the single-argument `Callable[[dict], ...]` shape `live_wiki.py` expects).

**Scope check:** Ten tasks, each independently testable, building strictly upward (schema -> embedding client -> storage -> cache logic -> wiring -> deployment). This is appropriately sized for one plan - no further decomposition needed.
