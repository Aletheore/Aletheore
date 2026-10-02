# Aletheore Hosted MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose Aletheore's MCP tools over a hosted `streamable-http` endpoint for paid-plan installations, backed by AIRview's existing server-side evidence plus a new persistent git mirror and embedding index, with LLM/embedding calls served by a self-hosted Ollama model (no external API cost).

**Architecture:** A new FastMCP instance mounted into the existing `github-app/app_server` FastAPI app at `/mcp`, authenticated by a thin Bearer-token middleware reusing the existing `api_tokens` table, with three tool data-access layers (pure evidence-table queries, git-mirror file reads, and a Postgres-array embedding index) instead of local disk.

**Tech Stack:** Python, FastAPI/Starlette, `mcp==1.23.3` (FastMCP), `psycopg` (sync), RQ (background jobs), Ollama (self-hosted, existing container), Postgres.

## Global Constraints

- Gate every new capability on `installations.plan != "free"` — identical to the existing AIRview wiki-build gate at `github-app/scan_worker/jobs.py:954`. No new tier logic.
- Every DB helper follows the existing sync `psycopg` pattern: `def fn(dsn: str, ...) -> ...`, `with psycopg.connect(dsn) as conn: with conn.cursor() as cur: ...`, explicit `conn.commit()`.
- **Container boundary**: `Dockerfile.app-server` copies only `app_server/`, `scripts/`, `migrations/` — it does NOT copy `scan_worker/`. `Dockerfile.scan-worker` copies `scan_worker/` (and, separately, does not run the `/mcp` mount). This means `app_server/mcp_hosted.py` (Tasks 6-9, runs inside the app-server container) cannot import anything from `scan_worker.*` — it would be an `ImportError` at runtime, not caught by any test that doesn't actually build the Docker image. Follow the codebase's existing precedent for this split: `app_server/db.py` and `scan_worker/db.py` already both exist as separate files with their own (sometimes overlapping) helpers — e.g. `get_installation_by_token_hash` lives in `app_server/db.py`, used only by `app_server`. New read-only helpers needed by hosted MCP tools go in `app_server/db.py`; the write-side helpers used by the mirror-sync/reindex background job go in `scan_worker/db.py`. Same split applies to `embedding_client.py` — Task 8 adds a second, small copy at `app_server/embedding_client.py` for `mcp_hosted.py` to import, alongside the existing `scan_worker/embedding_client.py` used by the reindex job.
- Every new per-installation table has `installation_id BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE`.
- Side-effect steps added to `run_pr_scan_job` follow the existing pattern at `jobs.py:333-341` — wrapped in their own `try/except Exception: pass`, never allowed to break the primary PR-comment flow that already succeeded.
- Reads on hosted MCP are fail-closed: missing/stale data returns an explicit tool-level error, never silently-wrong data. This is the opposite of the fail-open pattern used for audit-signing/caching elsewhere in this codebase — do not copy that pattern here.
- No pgvector. Embeddings are `DOUBLE PRECISION[]` columns compared in Python, matching `evidence_packet_cache`/`flash_review_cache`.
- Full spec: `docs/superpowers/specs/2026-07-24-aletheore-hosted-mcp-design.md` (merged to master, PR #25). This plan implements it task-by-task; consult it for the "why" behind any decision below.

---

### Task 1: DB migrations and helpers for git mirrors and code embeddings

**Files:**
- Create: `github-app/migrations/016_mcp_git_mirrors.sql`
- Create: `github-app/migrations/017_mcp_code_embeddings.sql`
- Modify: `github-app/scan_worker/db.py` (write-side helpers, used by the sync/reindex job — Tasks 2-3)
- Modify: `github-app/app_server/db.py` (read-side helpers, used by hosted MCP tools — Tasks 6-9)
- Test: `github-app/tests/test_scan_worker_db.py`, `github-app/tests/test_app_server_db.py`

**Interfaces:**
- Produces in `scan_worker/db.py`: `upsert_mcp_git_mirror(dsn, installation_id, repo_full_name, local_path, last_synced_commit, size_bytes) -> None`, `upsert_mcp_code_embedding(dsn, installation_id, repo_full_name, file_path, chunk_index, content_hash, chunk_text, embedding) -> None`, `get_mcp_code_embedding_hashes(dsn, installation_id, repo_full_name, file_path) -> dict[int, str]`, `delete_mcp_code_embeddings_for_file(dsn, installation_id, repo_full_name, file_path) -> None`, `list_mcp_code_embeddings(dsn, installation_id, repo_full_name) -> list[dict]` (reindex needs this too, to find stale files — see Task 3).
- Produces in `app_server/db.py` (read-only, same query bodies, separate module since `app_server` cannot import `scan_worker`): `get_mcp_git_mirror(dsn, installation_id, repo_full_name) -> dict | None`, `list_mcp_code_embeddings(dsn, installation_id, repo_full_name) -> list[dict]`, `get_latest_evidence(dsn, installation_id, repo_full_name) -> dict | None` (identical query to `scan_worker/db.py:293-308`, duplicated here since `mcp_hosted.py` needs it and lives in `app_server`).

- [ ] **Step 1: Write the migrations**

`github-app/migrations/016_mcp_git_mirrors.sql`:
```sql
CREATE TABLE mcp_git_mirrors (
    id BIGSERIAL PRIMARY KEY,
    installation_id BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name TEXT NOT NULL,
    local_path TEXT NOT NULL,
    last_synced_commit TEXT,
    last_synced_at TIMESTAMPTZ,
    size_bytes BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (installation_id, repo_full_name)
);
```

`github-app/migrations/017_mcp_code_embeddings.sql`:
```sql
CREATE TABLE mcp_code_embeddings (
    id BIGSERIAL PRIMARY KEY,
    installation_id BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    chunk_index INT NOT NULL,
    content_hash TEXT NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding DOUBLE PRECISION[] NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX mcp_code_embeddings_lookup ON mcp_code_embeddings (installation_id, repo_full_name, file_path);
```

- [ ] **Step 2: Write the failing tests**

```python
def test_upsert_and_get_mcp_git_mirror(test_dsn):
    insert_installation_row(test_dsn, installation_id=1, account_login="acme", plan="team")
    upsert_mcp_git_mirror(test_dsn, 1, "acme/widgets", "/var/aletheore/mirrors/1/acme__widgets", "abc123", 4096)
    row = get_mcp_git_mirror(test_dsn, 1, "acme/widgets")
    assert row["local_path"] == "/var/aletheore/mirrors/1/acme__widgets"
    assert row["last_synced_commit"] == "abc123"

    upsert_mcp_git_mirror(test_dsn, 1, "acme/widgets", "/var/aletheore/mirrors/1/acme__widgets", "def456", 5000)
    row = get_mcp_git_mirror(test_dsn, 1, "acme/widgets")
    assert row["last_synced_commit"] == "def456"


def test_mcp_code_embedding_hash_skip_roundtrip(test_dsn):
    insert_installation_row(test_dsn, installation_id=2, account_login="acme", plan="team")
    upsert_mcp_code_embedding(test_dsn, 2, "acme/widgets", "src/foo.py", 0, "hash-a", "def foo(): pass", [0.1, 0.2])
    upsert_mcp_code_embedding(test_dsn, 2, "acme/widgets", "src/foo.py", 1, "hash-b", "def bar(): pass", [0.3, 0.4])

    hashes = get_mcp_code_embedding_hashes(test_dsn, 2, "acme/widgets", "src/foo.py")
    assert hashes == {0: "hash-a", 1: "hash-b"}

    delete_mcp_code_embeddings_for_file(test_dsn, 2, "acme/widgets", "src/foo.py")
    assert get_mcp_code_embedding_hashes(test_dsn, 2, "acme/widgets", "src/foo.py") == {}


def test_mcp_code_embedding_cascade_delete_on_installation_removal(test_dsn):
    insert_installation_row(test_dsn, installation_id=3, account_login="acme", plan="team")
    upsert_mcp_code_embedding(test_dsn, 3, "acme/widgets", "src/foo.py", 0, "hash-a", "text", [0.1])
    delete_installation(test_dsn, 3)
    assert list_mcp_code_embeddings(test_dsn, 3, "acme/widgets") == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_scan_worker_db.py -k mcp_git_mirror or mcp_code_embedding -v`
Expected: FAIL with `ImportError`/`AttributeError` (functions and tables don't exist yet)

- [ ] **Step 4: Apply migrations to the test DB and implement the helpers**

Apply both migration files via whatever the existing test fixture/migration runner does (matches how `evidence_packet_cache`'s migration is picked up in `test_scan_worker_db.py`).

Append to `github-app/scan_worker/db.py`:
```python
def upsert_mcp_git_mirror(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    local_path: str,
    last_synced_commit: str,
    size_bytes: int,
) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mcp_git_mirrors
                    (installation_id, repo_full_name, local_path, last_synced_commit, last_synced_at, size_bytes)
                VALUES (%s, %s, %s, %s, now(), %s)
                ON CONFLICT (installation_id, repo_full_name) DO UPDATE SET
                    local_path = EXCLUDED.local_path,
                    last_synced_commit = EXCLUDED.last_synced_commit,
                    last_synced_at = now(),
                    size_bytes = EXCLUDED.size_bytes
                """,
                (installation_id, repo_full_name, local_path, last_synced_commit, size_bytes),
            )
        conn.commit()


def get_mcp_git_mirror(dsn: str, installation_id: int, repo_full_name: str) -> dict | None:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT local_path, last_synced_commit, last_synced_at, size_bytes
                FROM mcp_git_mirrors
                WHERE installation_id = %s AND repo_full_name = %s
                """,
                (installation_id, repo_full_name),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def upsert_mcp_code_embedding(
    dsn: str,
    installation_id: int,
    repo_full_name: str,
    file_path: str,
    chunk_index: int,
    content_hash: str,
    chunk_text: str,
    embedding: list[float],
) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM mcp_code_embeddings
                WHERE installation_id = %s AND repo_full_name = %s AND file_path = %s AND chunk_index = %s
                """,
                (installation_id, repo_full_name, file_path, chunk_index),
            )
            cur.execute(
                """
                INSERT INTO mcp_code_embeddings
                    (installation_id, repo_full_name, file_path, chunk_index, content_hash, chunk_text, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (installation_id, repo_full_name, file_path, chunk_index, content_hash, chunk_text, embedding),
            )
        conn.commit()


def get_mcp_code_embedding_hashes(
    dsn: str, installation_id: int, repo_full_name: str, file_path: str
) -> dict[int, str]:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT chunk_index, content_hash FROM mcp_code_embeddings
                WHERE installation_id = %s AND repo_full_name = %s AND file_path = %s
                """,
                (installation_id, repo_full_name, file_path),
            )
            rows = cur.fetchall()
    return {row[0]: row[1] for row in rows}


def delete_mcp_code_embeddings_for_file(
    dsn: str, installation_id: int, repo_full_name: str, file_path: str
) -> None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM mcp_code_embeddings
                WHERE installation_id = %s AND repo_full_name = %s AND file_path = %s
                """,
                (installation_id, repo_full_name, file_path),
            )
        conn.commit()


def list_mcp_code_embeddings(dsn: str, installation_id: int, repo_full_name: str) -> list[dict]:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT file_path, chunk_index, chunk_text, embedding
                FROM mcp_code_embeddings
                WHERE installation_id = %s AND repo_full_name = %s
                """,
                (installation_id, repo_full_name),
            )
            rows = cur.fetchall()
    return [dict(row) for row in rows]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_scan_worker_db.py -k "mcp_git_mirror or mcp_code_embedding" -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Write the failing test for the `app_server/db.py` read-side helpers**

```python
def test_app_server_get_mcp_git_mirror_and_evidence(test_dsn):
    insert_installation_row(test_dsn, installation_id=5, account_login="acme", plan="team")
    upsert_mcp_git_mirror(test_dsn, 5, "acme/widgets", "/var/aletheore/mirrors/5/acme__widgets", "abc", 100)  # scan_worker.db helper, used only to seed the row
    insert_repo_history(test_dsn, 5, "acme/widgets", {"repository": {"modules": []}})

    from app_server.db import get_mcp_git_mirror, get_latest_evidence, list_mcp_code_embeddings

    row = get_mcp_git_mirror(test_dsn, 5, "acme/widgets")
    assert row["local_path"] == "/var/aletheore/mirrors/5/acme__widgets"

    evidence = get_latest_evidence(test_dsn, 5, "acme/widgets")
    assert evidence == {"repository": {"modules": []}}

    assert list_mcp_code_embeddings(test_dsn, 5, "acme/widgets") == []
```

- [ ] **Step 7: Run test to verify it fails**

Run: `cd github-app && python -m pytest tests/test_app_server_db.py -v`
Expected: FAIL — `ImportError` (functions don't exist in `app_server.db` yet)

- [ ] **Step 8: Add the read-only counterparts to `app_server/db.py`**

Append (same query bodies as the `scan_worker/db.py` versions above — this is intentional duplication across the container boundary, not a typo):

```python
def get_mcp_git_mirror(dsn: str, installation_id: int, repo_full_name: str) -> dict | None:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT local_path, last_synced_commit, last_synced_at, size_bytes
                FROM mcp_git_mirrors
                WHERE installation_id = %s AND repo_full_name = %s
                """,
                (installation_id, repo_full_name),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def list_mcp_code_embeddings(dsn: str, installation_id: int, repo_full_name: str) -> list[dict]:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT file_path, chunk_index, chunk_text, embedding
                FROM mcp_code_embeddings
                WHERE installation_id = %s AND repo_full_name = %s
                """,
                (installation_id, repo_full_name),
            )
            rows = cur.fetchall()
    return [dict(row) for row in rows]


def get_latest_evidence(dsn: str, installation_id: int, repo_full_name: str) -> dict | None:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT evidence
                FROM repo_history
                WHERE installation_id = %s AND repo_full_name = %s
                ORDER BY scanned_at DESC, id DESC
                LIMIT 1
                """,
                (installation_id, repo_full_name),
            )
            row = cur.fetchone()
    return row[0] if row else None
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_scan_worker_db.py tests/test_app_server_db.py -v`
Expected: PASS (4 tests total)

- [ ] **Step 10: Commit**

```bash
git add github-app/migrations/016_mcp_git_mirrors.sql github-app/migrations/017_mcp_code_embeddings.sql github-app/scan_worker/db.py github-app/app_server/db.py github-app/tests/test_scan_worker_db.py github-app/tests/test_app_server_db.py
git commit -m "feat: add DB tables and helpers for hosted-MCP git mirrors and code embeddings"
```

---

### Task 2: Persistent git mirror sync, wired into the PR-scan job

**Files:**
- Modify: `github-app/scan_worker/jobs.py`
- Test: `github-app/tests/test_scan_worker_jobs.py`

**Interfaces:**
- Consumes: `upsert_mcp_git_mirror`, `get_mcp_git_mirror` (Task 1); existing `_clone_url(repo_full_name, token) -> str` (`jobs.py:104-105`); existing `get_installation_row(dsn, installation_id) -> dict | None`.
- Produces: `MIRROR_ROOT: Path`, `_mirror_path(installation_id: int, repo_full_name: str) -> Path`, `_sync_mcp_mirror(installation_id: int, repo_full_name: str, clone_url: str) -> Path | None` (returns the mirror path on success, `None` on failure — callers must not treat `None` as "no mirror exists," only as "sync failed this run").

- [ ] **Step 1: Write the failing test**

```python
def test_sync_mcp_mirror_clones_then_fetches(tmp_path, monkeypatch, test_dsn):
    monkeypatch.setattr(jobs, "MIRROR_ROOT", tmp_path)
    insert_installation_row(test_dsn, installation_id=10, account_login="acme", plan="team")
    monkeypatch.setattr(jobs, "get_settings", lambda: FakeSettings(database_url=test_dsn))

    source_repo = tmp_path / "source_origin"
    source_repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(source_repo)], check=True)
    (source_repo / "a.py").write_text("def a(): pass\n")
    subprocess.run(["git", "-C", str(source_repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(source_repo), "-c", "user.email=t@t.com", "-c", "user.name=t",
                     "commit", "-q", "-m", "init"], check=True)

    mirror_path = jobs._sync_mcp_mirror(10, "acme/widgets", f"file://{source_repo}")
    assert mirror_path is not None
    assert (mirror_path / "a.py").exists()
    row = get_mcp_git_mirror(test_dsn, 10, "acme/widgets")
    assert row is not None
    assert row["last_synced_commit"]

    # second sync (fetch path, not re-clone) picks up a new commit
    (source_repo / "b.py").write_text("def b(): pass\n")
    subprocess.run(["git", "-C", str(source_repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(source_repo), "-c", "user.email=t@t.com", "-c", "user.name=t",
                     "commit", "-q", "-m", "second"], check=True)

    mirror_path_2 = jobs._sync_mcp_mirror(10, "acme/widgets", f"file://{source_repo}")
    assert mirror_path_2 == mirror_path
    assert (mirror_path / "b.py").exists()


def test_sync_mcp_mirror_returns_none_on_clone_failure(tmp_path, monkeypatch, test_dsn):
    monkeypatch.setattr(jobs, "MIRROR_ROOT", tmp_path)
    insert_installation_row(test_dsn, installation_id=11, account_login="acme", plan="team")
    monkeypatch.setattr(jobs, "get_settings", lambda: FakeSettings(database_url=test_dsn))

    result = jobs._sync_mcp_mirror(11, "acme/does-not-exist", "file:///no/such/path")
    assert result is None
    assert get_mcp_git_mirror(test_dsn, 11, "acme/does-not-exist") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_scan_worker_jobs.py -k sync_mcp_mirror -v`
Expected: FAIL with `AttributeError: module 'jobs' has no attribute '_sync_mcp_mirror'`

- [ ] **Step 3: Implement `_sync_mcp_mirror`**

Add near the other clone helpers in `github-app/scan_worker/jobs.py` (after `_clone_ref`, `jobs.py:110`):

```python
MIRROR_ROOT = Path("/var/aletheore/mirrors")


def _mirror_path(installation_id: int, repo_full_name: str) -> Path:
    return MIRROR_ROOT / str(installation_id) / repo_full_name.replace("/", "__")


def _dir_size_bytes(path: Path) -> int:
    total = 0
    for entry in path.rglob("*"):
        if entry.is_file():
            total += entry.stat().st_size
    return total


def _sync_mcp_mirror(installation_id: int, repo_full_name: str, clone_url: str) -> Path | None:
    dest = _mirror_path(installation_id, repo_full_name)
    settings = get_settings()
    try:
        if not (dest / ".git").exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", "-q", clone_url, str(dest)], check=True)
        else:
            subprocess.run(["git", "fetch", "-q", "origin"], cwd=dest, check=True)
            default_branch = subprocess.run(
                ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
                cwd=dest, check=True, capture_output=True, text=True,
            ).stdout.strip().removeprefix("refs/remotes/")
            subprocess.run(["git", "reset", "-q", "--hard", default_branch], cwd=dest, check=True)

        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=dest, check=True, capture_output=True, text=True
        ).stdout.strip()
        size_bytes = _dir_size_bytes(dest)
        upsert_mcp_git_mirror(settings.database_url, installation_id, repo_full_name, str(dest), commit, size_bytes)
        return dest
    except subprocess.CalledProcessError:
        logging.getLogger(__name__).warning(
            "mcp mirror sync failed for installation=%s repo=%s", installation_id, repo_full_name
        )
        return None
```

Import at the top of `jobs.py`: add `upsert_mcp_git_mirror` to the existing `from app_server...` / local db import block (matches how other db helpers are already imported into this file).

Wire into `run_pr_scan_job`, immediately after the existing `_maybe_update_live_wiki` try/except block (`jobs.py:337-341`), following the exact same isolation pattern:

```python
        try:
            installation = get_installation_row(settings.database_url, installation_id)
            if installation is not None and installation["plan"] != "free":
                _sync_mcp_mirror(installation_id, repo_full_name, clone_url)
        except Exception:  # noqa: BLE001
            pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_scan_worker_jobs.py -k sync_mcp_mirror -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add github-app/scan_worker/jobs.py github-app/tests/test_scan_worker_jobs.py
git commit -m "feat: persistent git mirror sync for hosted MCP, wired into PR-scan job"
```

---

### Task 3: Embedding re-index, reusing existing chunking logic

**Files:**
- Create: `github-app/scan_worker/mcp_embedding_index.py`
- Modify: `github-app/scan_worker/jobs.py` (wire the call in)
- Test: `github-app/tests/test_mcp_embedding_index.py`

**Interfaces:**
- Consumes: `aletheore.search_index.build_chunks(evidence: dict, repo_path: Path) -> list[dict]` (existing, pure function — each dict has `module_path`, `symbol_name`, `text`); `github-app/scan_worker/embedding_client.py`'s `embed_text(text: str, base_url=None, timeout_seconds=5.0) -> list[float] | None` (existing); Task 1's `get_mcp_code_embedding_hashes`, `upsert_mcp_code_embedding`, `delete_mcp_code_embeddings_for_file`.
- Produces: `reindex_mcp_embeddings(dsn: str, installation_id: int, repo_full_name: str, evidence: dict, mirror_path: Path) -> None`.

- [ ] **Step 1: Write the failing test**

```python
def test_reindex_skips_unchanged_chunks_and_reembeds_changed_ones(test_dsn, monkeypatch):
    insert_installation_row(test_dsn, installation_id=20, account_login="acme", plan="team")
    evidence = {"repository": {"modules": [{
        "path": "a.py", "language": "python",
        "symbols": {"functions": [{"name": "foo", "start_line": 1, "end_line": 1}], "classes": []},
    }]}}
    mirror = tmp_path_factory_dir()
    (mirror / "a.py").write_text("def foo(): pass\n")

    calls = []
    def fake_embed_text(text, base_url=None, timeout_seconds=5.0):
        calls.append(text)
        return [1.0, 2.0]
    monkeypatch.setattr("scan_worker.mcp_embedding_index.embed_text", fake_embed_text)

    reindex_mcp_embeddings(test_dsn, 20, "acme/widgets", evidence, mirror)
    assert len(calls) == 1
    rows = list_mcp_code_embeddings(test_dsn, 20, "acme/widgets")
    assert len(rows) == 1

    # unchanged content on a second run -> no re-embed call
    reindex_mcp_embeddings(test_dsn, 20, "acme/widgets", evidence, mirror)
    assert len(calls) == 1

    # changed content -> re-embed
    (mirror / "a.py").write_text("def foo(): return 1\n")
    evidence["repository"]["modules"][0]["symbols"]["functions"][0]["end_line"] = 1
    reindex_mcp_embeddings(test_dsn, 20, "acme/widgets", evidence, mirror)
    assert len(calls) == 2


def test_reindex_deletes_embeddings_for_removed_files(test_dsn, monkeypatch):
    insert_installation_row(test_dsn, installation_id=21, account_login="acme", plan="team")
    monkeypatch.setattr("scan_worker.mcp_embedding_index.embed_text", lambda *a, **k: [1.0])
    upsert_mcp_code_embedding(test_dsn, 21, "acme/widgets", "gone.py", 0, "old-hash", "old text", [0.1])

    evidence = {"repository": {"modules": []}}
    mirror = tmp_path_factory_dir()
    reindex_mcp_embeddings(test_dsn, 21, "acme/widgets", evidence, mirror)

    assert list_mcp_code_embeddings(test_dsn, 21, "acme/widgets") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_mcp_embedding_index.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scan_worker.mcp_embedding_index'`

- [ ] **Step 3: Implement `mcp_embedding_index.py`**

```python
"""Embedding re-index for hosted MCP's search_codebase/answer tools."""

import hashlib
from pathlib import Path

from aletheore.search_index import build_chunks

from scan_worker.embedding_client import embed_text
from scan_worker.db import (
    delete_mcp_code_embeddings_for_file,
    get_mcp_code_embedding_hashes,
    list_mcp_code_embeddings,
    upsert_mcp_code_embedding,
)


def _chunk_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def reindex_mcp_embeddings(
    dsn: str, installation_id: int, repo_full_name: str, evidence: dict, mirror_path: Path
) -> None:
    chunks = build_chunks(evidence, mirror_path)
    chunks_by_file: dict[str, list[dict]] = {}
    for chunk in chunks:
        chunks_by_file.setdefault(chunk["module_path"], []).append(chunk)

    present_files = set(chunks_by_file.keys())

    for file_path, file_chunks in chunks_by_file.items():
        existing_hashes = get_mcp_code_embedding_hashes(dsn, installation_id, repo_full_name, file_path)
        new_hashes: dict[int, str] = {}
        for index, chunk in enumerate(file_chunks):
            chunk_hash = _chunk_hash(chunk["text"])
            new_hashes[index] = chunk_hash
            if existing_hashes.get(index) == chunk_hash:
                continue
            embedding = embed_text(chunk["text"])
            if embedding is None:
                continue
            upsert_mcp_code_embedding(
                dsn, installation_id, repo_full_name, file_path, index, chunk_hash, chunk["text"], embedding
            )
        if set(existing_hashes) - set(new_hashes):
            # the file shrank (fewer chunks than before) - simplest correct fix is a
            # full delete-and-rewrite for this file rather than tracking which
            # trailing indices became stale
            delete_mcp_code_embeddings_for_file(dsn, installation_id, repo_full_name, file_path)
            for index, chunk in enumerate(file_chunks):
                embedding = embed_text(chunk["text"])
                if embedding is not None:
                    upsert_mcp_code_embedding(
                        dsn, installation_id, repo_full_name, file_path, index,
                        _chunk_hash(chunk["text"]), chunk["text"], embedding,
                    )

    # files that no longer produce any chunks (deleted, or evidence dropped them) lose their embeddings entirely
    all_indexed_files = {
        row["file_path"] for row in list_mcp_code_embeddings(dsn, installation_id, repo_full_name)
    }
    for stale_file in all_indexed_files - present_files:
        delete_mcp_code_embeddings_for_file(dsn, installation_id, repo_full_name, stale_file)
```

Wire into `run_pr_scan_job` in `jobs.py`, immediately after the `_sync_mcp_mirror` call added in Task 2, same try/except isolation:

```python
        try:
            if installation is not None and installation["plan"] != "free":
                mirror_path = _mirror_path(installation_id, repo_full_name)
                if (mirror_path / ".git").exists():
                    reindex_mcp_embeddings(settings.database_url, installation_id, repo_full_name, new, mirror_path)
        except Exception:  # noqa: BLE001
            pass
```

(`new` is the head evidence dict already computed earlier in `run_pr_scan_job` — reused, not recomputed.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_mcp_embedding_index.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add github-app/scan_worker/mcp_embedding_index.py github-app/scan_worker/jobs.py github-app/tests/test_mcp_embedding_index.py
git commit -m "feat: embedding re-index for hosted MCP search/answer tools"
```

---

### Task 4: Uninstall cleanup — delete mirror directories

**Files:**
- Modify: `github-app/app_server/webhooks/installation.py`
- Test: `github-app/tests/test_webhooks_installation.py`

**Interfaces:**
- Consumes: `scan_worker.jobs._mirror_path` is scan-worker-local; app-server needs its own path helper since it doesn't import scan_worker. Produces: `_mcp_mirror_root_for_installation(installation_id: int) -> Path` defined locally in `installation.py`, matching `MIRROR_ROOT / str(installation_id)` from Task 2's `_mirror_path` (same root constant, duplicated as a plain constant here rather than a cross-package import, since app-server and scan-worker are separate deployable units that both mount the same `/var/aletheore/mirrors` volume).

- [ ] **Step 1: Write the failing test**

```python
def test_uninstall_deletes_mirror_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(installation_module, "MIRROR_ROOT", tmp_path)
    mirror_dir = tmp_path / "42"
    mirror_dir.mkdir()
    (mirror_dir / "acme__widgets").mkdir()
    (mirror_dir / "acme__widgets" / "a.py").write_text("x = 1\n")

    async def fake_delete_installation(pool, installation_id):
        pass

    monkeypatch.setattr(installation_module, "delete_installation", fake_delete_installation)
    await installation_module.handle_installation_event(
        pool=None, payload={"action": "deleted", "installation": {"id": 42}}
    )
    assert not mirror_dir.exists()


def test_uninstall_missing_mirror_directory_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(installation_module, "MIRROR_ROOT", tmp_path)

    async def fake_delete_installation(pool, installation_id):
        pass

    monkeypatch.setattr(installation_module, "delete_installation", fake_delete_installation)
    await installation_module.handle_installation_event(
        pool=None, payload={"action": "deleted", "installation": {"id": 999}}
    )  # no exception
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_webhooks_installation.py -v`
Expected: FAIL (`AttributeError: module has no attribute 'MIRROR_ROOT'`)

- [ ] **Step 3: Implement**

In `github-app/app_server/webhooks/installation.py`, add near the top:

```python
import shutil
from pathlib import Path

MIRROR_ROOT = Path("/var/aletheore/mirrors")
```

Modify `handle_installation_event` (currently `installation.py:4-14`) so the `deleted` branch also removes the mirror directory — add this line right after the existing `await delete_installation(pool, installation_id)` call:

```python
        shutil.rmtree(MIRROR_ROOT / str(installation_id), ignore_errors=True)
```

`ignore_errors=True` is deliberate: an installation that never triggered a paid-plan sync has no mirror directory at all, and that must not be an error.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_webhooks_installation.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add github-app/app_server/webhooks/installation.py github-app/tests/test_webhooks_installation.py
git commit -m "fix: delete hosted-MCP mirror directory on app uninstall"
```

---

### Task 5: Bearer auth middleware for the hosted MCP mount

**Files:**
- Create: `github-app/app_server/mcp_auth.py`
- Test: `github-app/tests/test_mcp_auth.py`

**Interfaces:**
- Consumes: existing `_authenticate_token`-equivalent lookup — reuse `get_installation_by_token_hash(dsn, token_hash) -> dict | None` (`db.py:483`, already used by `managed_audit_api.py`) and `hashlib.sha256` hashing exactly as `managed_audit_api.py` does it.
- Produces: `CURRENT_INSTALLATION_ID: contextvars.ContextVar[int | None]`, `McpAuthMiddleware` (Starlette `BaseHTTPMiddleware` subclass) with `dispatch(self, request, call_next)`.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_missing_token_returns_401(test_app_client):
    response = test_app_client.get("/mcp")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_free_plan_token_returns_402(test_app_client, test_dsn):
    insert_installation_row(test_dsn, installation_id=1, account_login="acme", plan="free")
    token = insert_api_token_row(test_dsn, installation_id=1)  # existing test helper, returns raw token
    response = test_app_client.get("/mcp", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 402


@pytest.mark.asyncio
async def test_valid_paid_token_sets_context_var(test_app_client, test_dsn):
    insert_installation_row(test_dsn, installation_id=2, account_login="acme", plan="team")
    token = insert_api_token_row(test_dsn, installation_id=2)
    captured = {}

    @test_app.get("/mcp/_debug_installation_id")
    def debug_route():
        captured["installation_id"] = CURRENT_INSTALLATION_ID.get()
        return {"ok": True}

    response = test_app_client.get("/mcp/_debug_installation_id", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert captured["installation_id"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_mcp_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app_server.mcp_auth'`

- [ ] **Step 3: Implement `mcp_auth.py`**

```python
"""Bearer-token auth for the hosted MCP mount, reusing the existing api_tokens scheme."""

import contextvars
import hashlib

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app_server.config import get_settings
from app_server.db import get_installation_by_token_hash

CURRENT_INSTALLATION_ID: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "current_installation_id", default=None
)


class McpAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith("/mcp"):
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        if not auth_header.lower().startswith("bearer "):
            return JSONResponse({"error": "missing bearer token"}, status_code=401)

        token = auth_header[len("bearer "):].strip()
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        settings = get_settings()
        installation = get_installation_by_token_hash(settings.database_url, token_hash)
        if installation is None:
            return JSONResponse({"error": "invalid token"}, status_code=401)
        if installation["plan"] == "free":
            return JSONResponse({"error": "hosted MCP requires a paid plan"}, status_code=402)

        reset_token = CURRENT_INSTALLATION_ID.set(installation["installation_id"])
        try:
            return await call_next(request)
        finally:
            CURRENT_INSTALLATION_ID.reset(reset_token)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_mcp_auth.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add github-app/app_server/mcp_auth.py github-app/tests/test_mcp_auth.py
git commit -m "feat: Bearer-token auth middleware for hosted MCP"
```

---

### Task 6: Hosted FastMCP app — pure-query tools, mounted into main.py

**Files:**
- Create: `github-app/app_server/mcp_hosted.py`
- Modify: `github-app/app_server/main.py`
- Test: `github-app/tests/test_mcp_hosted.py`

**Interfaces:**
- Consumes: `aletheore.query.QUERY_FUNCTIONS` (existing dict of `name -> Callable[[dict], Any]`), `app_server.db.get_latest_evidence(dsn, installation_id, repo_full_name) -> dict | None` (added in Task 1, Step 8 — `app_server` cannot import `scan_worker`, see Global Constraints), Task 5's `CURRENT_INSTALLATION_ID`.
- Produces: `build_hosted_mcp_app() -> Starlette`, registered tool functions named identically to `prototype/aletheore/mcp_server.py`'s (`aletheore_imports`, `aletheore_secrets`, etc. — the `_register_query_wrapper_tools` dispatch table names, verify exact names against `prototype/aletheore/mcp_server.py:70-92` before implementing so tool names match 1:1 between local and hosted).

- [ ] **Step 1: Confirm exact tool names to mirror**

Run: `grep -n "QUERY_FUNCTIONS\|_register_query_wrapper_tools" prototype/aletheore/mcp_server.py prototype/aletheore/query.py`
Read the output and use those exact key names as the hosted tool names — do not invent new names.

- [ ] **Step 2: Write the failing test**

```python
def test_hosted_mcp_imports_tool_returns_only_own_installations_evidence(test_dsn, monkeypatch):
    insert_installation_row(test_dsn, installation_id=1, account_login="acme", plan="team")
    insert_installation_row(test_dsn, installation_id=2, account_login="other", plan="team")
    insert_repo_history(test_dsn, 1, "acme/widgets", {"repository": {"modules": [
        {"path": "a.py", "imports": ["os"], "language": "python", "symbols": {"functions": [], "classes": []}}
    ]}})
    insert_repo_history(test_dsn, 2, "other/gizmos", {"repository": {"modules": [
        {"path": "b.py", "imports": ["sys"], "language": "python", "symbols": {"functions": [], "classes": []}}
    ]}})

    token = CURRENT_INSTALLATION_ID.set(1)
    try:
        result = _hosted_imports(repo_full_name="acme/widgets")
    finally:
        CURRENT_INSTALLATION_ID.reset(token)

    assert "a.py" in result
    assert "b.py" not in result


def test_hosted_mcp_tool_rejects_repo_not_owned_by_installation(test_dsn):
    insert_installation_row(test_dsn, installation_id=1, account_login="acme", plan="team")
    insert_repo_history(test_dsn, 1, "acme/widgets", {"repository": {"modules": []}})

    token = CURRENT_INSTALLATION_ID.set(1)
    try:
        result = _hosted_imports(repo_full_name="someone-elses/repo")
    finally:
        CURRENT_INSTALLATION_ID.reset(token)

    assert "error" in result.lower() or "not found" in result.lower()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_mcp_hosted.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: Implement `mcp_hosted.py`**

```python
"""Hosted MCP server: same tool surface as prototype/aletheore/mcp_server.py, backed
by server-side AIRview evidence and scoped to the authenticated installation."""

from aletheore.query import QUERY_FUNCTIONS
from mcp.server.fastmcp import FastMCP

from app_server.config import get_settings
from app_server.db import get_latest_evidence
from app_server.mcp_auth import CURRENT_INSTALLATION_ID, McpAuthMiddleware


def _current_installation_id() -> int:
    installation_id = CURRENT_INSTALLATION_ID.get()
    if installation_id is None:
        raise RuntimeError("hosted MCP tool called outside an authenticated request")
    return installation_id


def _load_evidence(repo_full_name: str) -> dict | None:
    settings = get_settings()
    return get_latest_evidence(settings.database_url, _current_installation_id(), repo_full_name)


def _query_wrapper_tool(name: str, query_fn):
    def tool(repo_full_name: str, **kwargs) -> str:
        evidence = _load_evidence(repo_full_name)
        if evidence is None:
            return "error: no evidence found for this installation's repo (has it been scanned yet?)"
        return str(query_fn(evidence, **kwargs))
    tool.__name__ = f"aletheore_{name}"
    return tool


def build_hosted_mcp_app():
    mcp = FastMCP("aletheore-hosted", stateless_http=True)
    for name, query_fn in QUERY_FUNCTIONS.items():
        mcp.tool(name=f"aletheore_{name}")(_query_wrapper_tool(name, query_fn))

    app = mcp.streamable_http_app()
    app.add_middleware(McpAuthMiddleware)
    return app
```

(`_hosted_imports` referenced in the tests above is the concrete tool function produced by `_query_wrapper_tool("imports", QUERY_FUNCTIONS["imports"])` — expose it for direct testing by also assigning `_hosted_imports = _query_wrapper_tool("imports", QUERY_FUNCTIONS["imports"])` at module level in `mcp_hosted.py`, so tests can import and call it without going through the full MCP protocol.)

In `github-app/app_server/main.py`, add near the other router mounts:

```python
from app_server.mcp_hosted import build_hosted_mcp_app

app.mount("/mcp", build_hosted_mcp_app())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_mcp_hosted.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add github-app/app_server/mcp_hosted.py github-app/app_server/main.py github-app/tests/test_mcp_hosted.py
git commit -m "feat: mount hosted MCP app with pure-query tools scoped per installation"
```

---

### Task 7: Exact-text tools (symbol_source, search, scan) from the git mirror

**Files:**
- Modify: `github-app/app_server/mcp_hosted.py`
- Test: `github-app/tests/test_mcp_hosted.py`

**Interfaces:**
- Consumes: Task 1's `app_server.db.get_mcp_git_mirror(dsn, installation_id, repo_full_name) -> dict | None`.
- Produces: `aletheore_symbol_source`, `aletheore_search`, `aletheore_scan` hosted tool functions, plus `_resolve_mirror_path(repo_full_name: str) -> Path | None` (returns `None`, not a path, if unsynced — callers turn that into the fail-closed error).

- [ ] **Step 1: Write the failing test**

```python
def test_hosted_search_returns_resync_pending_when_mirror_missing(test_dsn):
    insert_installation_row(test_dsn, installation_id=1, account_login="acme", plan="team")
    token = CURRENT_INSTALLATION_ID.set(1)
    try:
        result = _hosted_search(repo_full_name="acme/widgets", query="foo")
    finally:
        CURRENT_INSTALLATION_ID.reset(token)
    assert "resync pending" in result.lower() or "not available" in result.lower()


def test_hosted_symbol_source_reads_from_own_mirror_only(test_dsn, tmp_path):
    insert_installation_row(test_dsn, installation_id=1, account_login="acme", plan="team")
    mirror = tmp_path / "1" / "acme__widgets"
    mirror.mkdir(parents=True)
    (mirror / "a.py").write_text("def foo():\n    return 1\n")
    upsert_mcp_git_mirror(test_dsn, 1, "acme/widgets", str(mirror), "abc123", 100)

    token = CURRENT_INSTALLATION_ID.set(1)
    try:
        result = _hosted_symbol_source(repo_full_name="acme/widgets", file_path="a.py", start_line=1, end_line=2)
    finally:
        CURRENT_INSTALLATION_ID.reset(token)
    assert "return 1" in result


def test_hosted_symbol_source_rejects_path_escaping_mirror(test_dsn, tmp_path):
    insert_installation_row(test_dsn, installation_id=1, account_login="acme", plan="team")
    mirror = tmp_path / "1" / "acme__widgets"
    mirror.mkdir(parents=True)
    upsert_mcp_git_mirror(test_dsn, 1, "acme/widgets", str(mirror), "abc123", 100)

    token = CURRENT_INSTALLATION_ID.set(1)
    try:
        result = _hosted_symbol_source(
            repo_full_name="acme/widgets", file_path="../../../etc/passwd", start_line=1, end_line=1
        )
    finally:
        CURRENT_INSTALLATION_ID.reset(token)
    assert "error" in result.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_mcp_hosted.py -k "search or symbol_source" -v`
Expected: FAIL

- [ ] **Step 3: Implement**

Append to `mcp_hosted.py`:

```python
from pathlib import Path


def _resolve_mirror_path(repo_full_name: str) -> Path | None:
    settings = get_settings()
    row = get_mcp_git_mirror(settings.database_url, _current_installation_id(), repo_full_name)
    if row is None:
        return None
    return Path(row["local_path"])


def _resolve_file_in_mirror(mirror: Path, file_path: str) -> Path | None:
    """Resolves file_path against mirror, rejecting any path that escapes it."""
    candidate = (mirror / file_path).resolve()
    try:
        candidate.relative_to(mirror.resolve())
    except ValueError:
        return None
    return candidate


def _hosted_symbol_source(repo_full_name: str, file_path: str, start_line: int, end_line: int) -> str:
    mirror = _resolve_mirror_path(repo_full_name)
    if mirror is None:
        return "error: mirror not yet synced / resync pending"
    resolved = _resolve_file_in_mirror(mirror, file_path)
    if resolved is None or not resolved.exists():
        return "error: file not found in mirror"
    lines = resolved.read_text(encoding="utf-8", errors="ignore").splitlines()
    return "\n".join(lines[start_line - 1:end_line])


def _hosted_search(repo_full_name: str, query: str, max_results: int = 20) -> str:
    mirror = _resolve_mirror_path(repo_full_name)
    if mirror is None:
        return "error: mirror not yet synced / resync pending"
    matches = []
    for path in mirror.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
                if query in line:
                    matches.append(f"{path.relative_to(mirror)}:{lineno}: {line.strip()}")
                    if len(matches) >= max_results:
                        return "\n".join(matches)
        except OSError:
            continue
    return "\n".join(matches) if matches else "no matches"


def _hosted_scan(repo_full_name: str) -> str:
    return (
        "error: aletheore_scan is not available in hosted MCP - hosted mode reflects "
        "AIRview's already-scanned server-side evidence, updated automatically on every "
        "push to the default branch. Use a local `aletheore mcp` connection to trigger an "
        "ad hoc scan."
    )


mcp_symbol_source = mcp.tool(name="aletheore_symbol_source")(_hosted_symbol_source)  # noqa: see registration note below
```

Registration note: rather than the ad hoc last line above, add all three to `build_hosted_mcp_app()`'s body alongside the existing `QUERY_FUNCTIONS` loop:

```python
    mcp.tool(name="aletheore_symbol_source")(_hosted_symbol_source)
    mcp.tool(name="aletheore_search")(_hosted_search)
    mcp.tool(name="aletheore_scan")(_hosted_scan)
```

`aletheore_scan` is deliberately a stub that explains why — the design spec's "Branch Scope" section already documents that hosted MCP reflects default-branch server state, not an ad hoc scan; making the tool itself say so directly is better than silently returning stale/wrong results or a bare error.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_mcp_hosted.py -k "search or symbol_source" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add github-app/app_server/mcp_hosted.py github-app/tests/test_mcp_hosted.py
git commit -m "feat: exact-text hosted MCP tools reading from the synced git mirror"
```

---

### Task 8: search_codebase — cosine similarity over stored embeddings

**Files:**
- Create: `github-app/app_server/embedding_client.py` (a copy of `scan_worker/embedding_client.py` — `app_server` cannot import `scan_worker`, see Global Constraints)
- Modify: `github-app/app_server/mcp_hosted.py`
- Test: `github-app/tests/test_mcp_hosted.py`

**Interfaces:**
- Consumes: Task 1's `app_server.db.list_mcp_code_embeddings`.
- Produces: `app_server.embedding_client.embed_text(text, base_url=None, timeout_seconds=5.0) -> list[float] | None` (identical implementation to `scan_worker/embedding_client.py`), `_cosine_similarity(a: list[float], b: list[float]) -> float`, `_hosted_search_codebase(repo_full_name: str, query: str, k: int = 10) -> str`.

- [ ] **Step 0: Create `app_server/embedding_client.py`**

Copy `github-app/scan_worker/embedding_client.py` verbatim to `github-app/app_server/embedding_client.py` (same content, same `EMBEDDING_MODEL = "nomic-embed-text"`, same `embed_text` function — this is deliberate duplication across the container boundary, not a refactor target for this plan).

- [ ] **Step 1: Write the failing test**

```python
def test_search_codebase_ranks_by_similarity_and_stays_scoped_to_own_installation(test_dsn, monkeypatch):
    insert_installation_row(test_dsn, installation_id=1, account_login="acme", plan="team")
    insert_installation_row(test_dsn, installation_id=2, account_login="other", plan="team")

    def fake_embed_text(text, base_url=None, timeout_seconds=5.0):
        return {"def foo(): pass": [1.0, 0.0], "def bar(): pass": [0.0, 1.0], "query about foo": [0.9, 0.1]}[text]

    monkeypatch.setattr("app_server.mcp_hosted.embed_text", fake_embed_text)

    upsert_mcp_code_embedding(test_dsn, 1, "acme/widgets", "a.py", 0, "h1", "def foo(): pass", [1.0, 0.0])
    upsert_mcp_code_embedding(test_dsn, 1, "acme/widgets", "b.py", 0, "h2", "def bar(): pass", [0.0, 1.0])
    upsert_mcp_code_embedding(test_dsn, 2, "other/gizmos", "c.py", 0, "h3", "def foo(): pass", [1.0, 0.0])

    token = CURRENT_INSTALLATION_ID.set(1)
    try:
        result = _hosted_search_codebase(repo_full_name="acme/widgets", query="query about foo", k=1)
    finally:
        CURRENT_INSTALLATION_ID.reset(token)

    assert "a.py" in result
    assert "c.py" not in result  # never leaks installation 2's chunk, even though it's the same text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_mcp_hosted.py -k search_codebase -v`
Expected: FAIL

- [ ] **Step 3: Implement**

```python
import math

from app_server.embedding_client import embed_text
from app_server.db import list_mcp_code_embeddings


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _hosted_search_codebase(repo_full_name: str, query: str, k: int = 10) -> str:
    query_embedding = embed_text(query, base_url="http://ollama:11434")
    if query_embedding is None:
        return "error: embedding model temporarily unavailable, try again"

    settings = get_settings()
    rows = list_mcp_code_embeddings(settings.database_url, _current_installation_id(), repo_full_name)
    if not rows:
        return "error: no indexed code for this repo yet (has it synced?)"

    scored = sorted(
        rows, key=lambda row: _cosine_similarity(query_embedding, row["embedding"]), reverse=True
    )[:k]
    return "\n\n---\n\n".join(f"{row['file_path']}:\n{row['chunk_text']}" for row in scored)
```

Register in `build_hosted_mcp_app()`:
```python
    mcp.tool(name="aletheore_search_codebase")(_hosted_search_codebase)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_mcp_hosted.py -k search_codebase -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add github-app/app_server/mcp_hosted.py github-app/tests/test_mcp_hosted.py
git commit -m "feat: hosted search_codebase tool over tenant-scoped embeddings"
```

---

### Task 9: answer tool — self-hosted generation with a concurrency cap

**Files:**
- Create: `github-app/app_server/hosted_generation.py`
- Modify: `github-app/app_server/mcp_hosted.py`
- Test: `github-app/tests/test_hosted_generation.py`, `github-app/tests/test_mcp_hosted.py`

**Interfaces:**
- Produces: `generate_answer(question: str, chunks: list[str], timeout_seconds: float = 20.0) -> str | None` (returns `None` on failure/timeout — same "None means unavailable" convention as `embed_text`); `GENERATION_SEMAPHORE: threading.Semaphore(2)`; `_hosted_answer(repo_full_name: str, question: str, k: int = 5) -> str`.

- [ ] **Step 1: Write the failing test**

```python
def test_generate_answer_calls_ollama_chat_endpoint(monkeypatch):
    captured = {}

    def fake_post(self, path, json, timeout):
        captured["path"] = path
        captured["json"] = json
        class FakeResponse:
            def raise_for_status(self): pass
            def json(self): return {"message": {"content": "It does X because Y."}}
        return FakeResponse()

    monkeypatch.setattr("httpx.Client.post", fake_post)
    result = generate_answer("why does foo exist", ["def foo(): pass"])
    assert result == "It does X because Y."
    assert "chat" in captured["path"]
    assert captured["json"]["model"] == GENERATION_MODEL


def test_generate_answer_returns_none_on_timeout(monkeypatch):
    def fake_post(self, path, json, timeout):
        raise httpx.TimeoutException("timed out")
    monkeypatch.setattr("httpx.Client.post", fake_post)
    assert generate_answer("q", ["chunk"]) is None


def test_generation_semaphore_bounds_concurrency(monkeypatch):
    import time
    slow_calls = []

    def fake_post(self, path, json, timeout):
        slow_calls.append(1)
        time.sleep(0.2)
        class FakeResponse:
            def raise_for_status(self): pass
            def json(self): return {"message": {"content": "ok"}}
        return FakeResponse()

    monkeypatch.setattr("httpx.Client.post", fake_post)
    monkeypatch.setattr("app_server.hosted_generation.GENERATION_SEMAPHORE", threading.Semaphore(1))

    results = []
    def call():
        results.append(generate_answer("q", ["c"], acquire_timeout_seconds=0.05))

    t1 = threading.Thread(target=call)
    t2 = threading.Thread(target=call)
    t1.start(); time.sleep(0.05); t2.start()
    t1.join(); t2.join()
    assert results.count(None) == 1  # the second call times out waiting for the semaphore
    assert results.count("ok") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_hosted_generation.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `hosted_generation.py`**

```python
"""Self-hosted generation for the hosted MCP answer tool - Ollama only, no external API."""

import logging
import threading

import httpx

GENERATION_MODEL = "qwen2.5:3b-instruct"
GENERATION_BASE_URL = "http://ollama:11434"
GENERATION_SEMAPHORE = threading.Semaphore(2)

ANSWER_SYSTEM_PROMPT = (
    "You answer questions about a specific codebase using only the code chunks provided "
    "below. Answer in 2-5 sentences. If the provided chunks don't actually answer the "
    "question, say so plainly rather than guessing."
)

logger = logging.getLogger(__name__)


def generate_answer(
    question: str,
    chunks: list[str],
    timeout_seconds: float = 20.0,
    acquire_timeout_seconds: float = 15.0,
) -> str | None:
    acquired = GENERATION_SEMAPHORE.acquire(timeout=acquire_timeout_seconds)
    if not acquired:
        logger.warning("generation semaphore saturated; rejecting request")
        return None
    try:
        context = "\n\n---\n\n".join(chunks)
        user_prompt = f"Question: {question}\n\nRetrieved code chunks:\n\n{context}"
        try:
            with httpx.Client(base_url=GENERATION_BASE_URL) as client:
                response = client.post(
                    "/api/chat",
                    json={
                        "model": GENERATION_MODEL,
                        "messages": [
                            {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        "stream": False,
                    },
                    timeout=timeout_seconds,
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("generation call failed (%s)", type(exc).__name__)
            return None
        content = data.get("message", {}).get("content")
        return content if isinstance(content, str) and content else None
    finally:
        GENERATION_SEMAPHORE.release()
```

Append `_hosted_answer` to `mcp_hosted.py`:

```python
from app_server.hosted_generation import generate_answer
# `embed_text` and `list_mcp_code_embeddings` are already imported at module level from
# Task 8 (`app_server.embedding_client`, `app_server.db`) - reused here, not re-imported.

DEFAULT_ANSWER_CONFIDENCE_THRESHOLD = 0.3  # cosine similarity floor, not the local prototype's distance threshold


def _hosted_answer(repo_full_name: str, question: str, k: int = 5) -> str:
    query_embedding = embed_text(question, base_url="http://ollama:11434")
    if query_embedding is None:
        return "error: embedding model temporarily unavailable, try again"

    settings = get_settings()
    rows = list_mcp_code_embeddings(settings.database_url, _current_installation_id(), repo_full_name)
    if not rows:
        return "error: no indexed code for this repo yet (has it synced?)"

    scored = sorted(rows, key=lambda row: _cosine_similarity(query_embedding, row["embedding"]), reverse=True)
    top = scored[:k]
    if not top or _cosine_similarity(query_embedding, top[0]["embedding"]) < DEFAULT_ANSWER_CONFIDENCE_THRESHOLD:
        return "Not enough evidence in the indexed codebase to answer this confidently."

    answer = generate_answer(question, [row["chunk_text"] for row in top])
    if answer is None:
        return "error: model temporarily unavailable, try again"
    return answer
```

Register:
```python
    mcp.tool(name="aletheore_answer")(_hosted_answer)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_hosted_generation.py tests/test_mcp_hosted.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add github-app/app_server/hosted_generation.py github-app/app_server/mcp_hosted.py github-app/tests/test_hosted_generation.py github-app/tests/test_mcp_hosted.py
git commit -m "feat: hosted answer tool with self-hosted Qwen2.5-3B and a concurrency cap"
```

---

### Task 10: Ollama resource bump and model pull

**Files:**
- Modify: `github-app/docker-compose.yml`

**Interfaces:** None (infra config only).

- [ ] **Step 1: Edit the `ollama` service definition**

Change (verified live host capacity: 4 vCPU / 15GB RAM, 14GB free, confirmed via SSH `nproc`/`free -h` on `$PROD_SSH`):

```yaml
  ollama:
    image: ollama/ollama:latest
    cpus: "2.0"
    mem_limit: 6g
    command:
      - |
        ollama serve &
        sleep 3
        ollama pull nomic-embed-text
        ollama pull qwen2.5:3b-instruct
        wait
```

- [ ] **Step 2: Deploy and verify the model is pulled**

This step happens at actual deploy time (Task 13), not standalone — noted here so the docker-compose change isn't forgotten. Verification command once deployed:

```bash
ssh $PROD_SSH "docker exec github-app-ollama-1 ollama list"
```
Expected output includes both `nomic-embed-text` and `qwen2.5:3b-instruct`.

- [ ] **Step 3: Commit**

```bash
git add github-app/docker-compose.yml
git commit -m "feat: bump Ollama resources and add Qwen2.5-3B generation model"
```

---

### Task 11: Multi-tenant isolation test suite

**Files:**
- Create: `github-app/tests/test_mcp_hosted_isolation.py`

**Interfaces:**
- Consumes: everything built in Tasks 6-9.

- [ ] **Step 1: Write the isolation tests** (these are integration tests, not unit tests — they seed two full fake installations with deliberately overlapping content and assert zero cross-contamination)

```python
import asyncio

import pytest


def _seed_installation(test_dsn, installation_id, account_login, repo_full_name, mirror_root):
    insert_installation_row(test_dsn, installation_id, account_login, plan="team")
    insert_repo_history(test_dsn, installation_id, repo_full_name, {"repository": {"modules": [
        {"path": "shared_name.py", "imports": ["os"], "language": "python",
         "symbols": {"functions": [{"name": "process", "start_line": 1, "end_line": 2}], "classes": []}}
    ]}})
    mirror = mirror_root / str(installation_id) / repo_full_name.replace("/", "__")
    mirror.mkdir(parents=True)
    (mirror / "shared_name.py").write_text(f"def process():\n    return '{account_login}-secret'\n")
    upsert_mcp_git_mirror(test_dsn, installation_id, repo_full_name, str(mirror), "abc", 100)
    upsert_mcp_code_embedding(
        test_dsn, installation_id, repo_full_name, "shared_name.py", 0, "h",
        f"def process(): return '{account_login}-secret'", [1.0, float(installation_id)],
    )


def test_query_tools_never_cross_tenant(test_dsn, tmp_path):
    # no MIRROR_ROOT monkeypatch needed: `_resolve_mirror_path` reads `local_path` straight
    # from the `mcp_git_mirrors` row (seeded below pointing at `tmp_path`), not a module constant
    _seed_installation(test_dsn, 100, "acme", "acme/widgets", tmp_path)
    _seed_installation(test_dsn, 200, "other", "other/widgets", tmp_path)

    token = CURRENT_INSTALLATION_ID.set(100)
    try:
        source = _hosted_symbol_source(repo_full_name="acme/widgets", file_path="shared_name.py", start_line=1, end_line=2)
    finally:
        CURRENT_INSTALLATION_ID.reset(token)
    assert "acme-secret" in source
    assert "other-secret" not in source


@pytest.mark.asyncio
async def test_concurrent_requests_from_two_installations_never_leak(test_dsn, tmp_path):
    _seed_installation(test_dsn, 300, "acme", "acme/widgets", tmp_path)
    _seed_installation(test_dsn, 400, "other", "other/widgets", tmp_path)

    async def call_as(installation_id, repo_full_name, expected_secret, forbidden_secret):
        token = CURRENT_INSTALLATION_ID.set(installation_id)
        try:
            result = _hosted_symbol_source(
                repo_full_name=repo_full_name, file_path="shared_name.py", start_line=1, end_line=2
            )
        finally:
            CURRENT_INSTALLATION_ID.reset(token)
        assert expected_secret in result
        assert forbidden_secret not in result

    await asyncio.gather(
        *[call_as(300, "acme/widgets", "acme-secret", "other-secret") for _ in range(20)],
        *[call_as(400, "other/widgets", "other-secret", "acme-secret") for _ in range(20)],
    )


def test_embedding_query_path_never_returns_another_tenants_row(test_dsn, tmp_path, monkeypatch):
    _seed_installation(test_dsn, 500, "acme", "acme/widgets", tmp_path)
    _seed_installation(test_dsn, 600, "other", "other/widgets", tmp_path)
    monkeypatch.setattr("app_server.mcp_hosted.embed_text", lambda *a, **k: [1.0, 500.0])

    token = CURRENT_INSTALLATION_ID.set(500)
    try:
        result = _hosted_search_codebase(repo_full_name="acme/widgets", query="anything", k=5)
    finally:
        CURRENT_INSTALLATION_ID.reset(token)
    assert "acme-secret" in result
    assert "other-secret" not in result
```

- [ ] **Step 2: Run and verify all pass**

Run: `cd github-app && python -m pytest tests/test_mcp_hosted_isolation.py -v`
Expected: PASS (3 tests). If any fail, this is a real security bug — do not weaken the assertion to make it pass; fix the scoping logic in `mcp_hosted.py`.

- [ ] **Step 3: Commit**

```bash
git add github-app/tests/test_mcp_hosted_isolation.py
git commit -m "test: multi-tenant isolation suite for hosted MCP"
```

---

### Task 12: CLI hosted-install mode

**Files:**
- Modify: `prototype/aletheore/cli.py`
- Test: `prototype/tests/test_cli.py`

**Interfaces:**
- Consumes: existing `_MCP_CLIENT_CONFIGS`, `_write_json_mcp_client_config`, `_write_toml_mcp_client_config` (already built this session).
- Produces: `_hosted_entry(token: str) -> dict`, `mcp_install(..., hosted_token: str | None = typer.Option(None, "--hosted", help="Write a hosted MCP config using this API token instead of local stdio."))`.

- [ ] **Step 1: Write the failing test**

```python
def test_mcp_install_hosted_writes_url_and_bearer_token(tmp_path, runner):
    result = runner.invoke(app, ["mcp-install", str(tmp_path), "--target", "claude-code", "--hosted", "atk_test123"])
    assert result.exit_code == 0
    config = json.loads((tmp_path / ".mcp.json").read_text())
    entry = config["mcpServers"]["aletheore"]
    assert entry["url"] == "https://mcp.aletheore.com/mcp"
    assert entry["headers"]["Authorization"] == "Bearer atk_test123"
    assert "command" not in entry


def test_mcp_install_without_hosted_flag_still_writes_local_stdio(tmp_path, runner):
    result = runner.invoke(app, ["mcp-install", str(tmp_path), "--target", "claude-code"])
    assert result.exit_code == 0
    config = json.loads((tmp_path / ".mcp.json").read_text())
    assert config["mcpServers"]["aletheore"]["command"] == "aletheore"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd prototype && python -m pytest tests/test_cli.py -k mcp_install_hosted -v`
Expected: FAIL — unexpected `--hosted` option

- [ ] **Step 3: Implement**

In `prototype/aletheore/cli.py`, near `_stdio_entry`/`_opencode_entry`:

```python
def _hosted_entry(token: str) -> dict:
    return {"url": "https://mcp.aletheore.com/mcp", "headers": {"Authorization": f"Bearer {token}"}}
```

Modify the `mcp_install` command signature to accept `hosted_token: str | None = typer.Option(None, "--hosted", help="Write a hosted MCP config using this API token instead of local stdio.")`, and in `_mcp_install`, when `hosted_token` is provided, build each target's entry from `_hosted_entry(hosted_token)` instead of `_stdio_entry(repo_path, include_type)`/`_opencode_entry(repo_path)` — same merge-safe writer calls (`_write_json_mcp_client_config`/`_write_toml_mcp_client_config`), just a different entry dict. For `codex-cli`'s TOML target specifically, the hosted entry becomes `[mcp_servers.aletheore]` with `url`/`headers` keys instead of `command`/`args`, matching whatever shape the TOML writer already expects for non-stdio entries (verify against the existing `_write_toml_mcp_client_config` implementation before assuming the key names carry over unchanged).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd prototype && python -m pytest tests/test_cli.py -k mcp_install -v`
Expected: PASS (all `mcp-install` tests, old and new)

- [ ] **Step 5: Commit**

```bash
git add prototype/aletheore/cli.py prototype/tests/test_cli.py
git commit -m "feat: --hosted flag for mcp-install to write hosted MCP configs"
```

---

### Task 13: Deploy and real end-to-end verification

**Files:** None (deployment + manual verification only).

- [ ] **Step 1: Push all task branches, open one PR per already-established review discipline, wait for real CI**

Follow this session's established pattern exactly: push branch, `gh pr create`, run both `github-app` and `prototype` test suites locally, wait for real CI via `gh pr checks`, merge only when genuinely green.

- [ ] **Step 2: Deploy**

```bash
ssh $PROD_SSH "cd /root/aletheore && git pull && mkdir -p /var/aletheore/mirrors && docker compose build app-server scan-worker ollama && docker compose up -d"
```
Note the new `mkdir -p /var/aletheore/mirrors` — this must exist and be a mounted/persistent path (add a bind mount for it in `docker-compose.yml`'s `app-server` and `scan-worker` service definitions if not already present, since mirrors need to survive container restarts and be visible to both services).

- [ ] **Step 3: Verify migrations applied**

```bash
ssh $PROD_SSH "docker compose logs app-server | grep -i migration | tail -5"
```
Expected: confirmation that migrations 016 and 017 applied.

- [ ] **Step 4: Verify Ollama has both models**

```bash
ssh $PROD_SSH "docker exec github-app-ollama-1 ollama list"
```
Expected: `nomic-embed-text` and `qwen2.5:3b-instruct` both listed.

- [ ] **Step 5: Real end-to-end test with a live coding agent**

Using a real test installation with a paid plan and an actual `api_tokens` row: run `aletheore mcp-install <path> --target claude-code --hosted <real-token>`, restart Claude Code, and manually drive at least one tool from each of the three data-access layers (a pure-query tool like `aletheore_secrets`, `aletheore_symbol_source`, and `aletheore_search_codebase`) against a real repo with the app installed. Confirm real responses, not just "no error thrown."

- [ ] **Step 6: Confirm no regression on existing local MCP / other endpoints**

`curl https://app.aletheore.com/` (expect 200) and spot-check that `managed_audit` and the dashboard still work — the `/mcp` mount and its middleware must not affect unrelated routes.
