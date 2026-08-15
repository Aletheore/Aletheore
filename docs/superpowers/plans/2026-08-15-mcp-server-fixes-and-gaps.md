# MCP Server Fixes and Gap-Closing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix five real bugs found in a live audit of Aletheore's MCP server (`src/aletheore/mcp_server.py`) and close three of the audit's identified capability gaps, then prove the whole server works end-to-end against this repo's own evidence.

**Architecture:** No new subsystems. Every task is a small, targeted change inside the existing MCP server / query / search-index modules, following patterns that already exist in each file (the effect-class consent system in `mcp_server.py`, the version-checked evidence loader in `evidence.py`, the query-function registry in `query.py`). Three brand-new read-only tools are added using the exact same `_register_*_tool` + `QUERY_FUNCTIONS`-style pattern already used by the 26 existing tools.

**Tech Stack:** Python 3.11+, `mcp>=2.0,<3.0` (`MCPServer`), `lancedb`, `pytest` + `pytest-asyncio`.

**Spec:** No separate spec doc — this plan is grounded directly in a live code audit performed in-conversation (file:line citations throughout) plus direct verification against this repo's own `.aletheore/air.json`. Out of scope, explicitly deferred: hosted MCP resurrection (a full abandoned branch, `codex/aletheore-hosted-mcp`, exists for that separately) and any PR-level MCP tools. Do not touch either in this plan.

## Global Constraints

- Every new/changed MCP tool must keep the existing TOON-encoding convention (`_toon_result`) — never return raw JSON.
- Every new tool that only reads `.aletheore/air.json` is read-only (`READ_ONLY_ANNOTATIONS`) and registers unconditionally (no effect gating) — this matches all 23 existing read-only tools.
- Never add a bare `json.loads` on an evidence or snapshot file anywhere in `mcp_server.py` — always route through `load_evidence_file` (already imported at `mcp_server.py:18`) so version/schema errors surface clearly instead of as a raw `KeyError` three calls later.
- Test fixtures: use `make_repo_with_evidence(tmp_path)` from `src/tests/test_mcp_server.py` (or `tests.air_fixtures.minimal_air_evidence()` directly, matching `test_mcp_consent.py`'s pattern) — do not hand-roll evidence dicts missing required AIR schema keys.
- Tool call tests use the pattern already established at `test_mcp_server.py:317-330`: `@pytest.mark.asyncio async def test_x(tmp_path): repo = make_repo_with_evidence(tmp_path); server = build_server(repo); result = await server.call_tool("tool_name", {...}); assert tool_result_body(result) == {"result": expected}`.
- Run tests with: `cd src && python3 -m pytest tests/test_mcp_server.py tests/test_mcp_consent.py tests/test_query.py tests/test_search_index.py -v` (adjust the exact interpreter/venv the environment actually has `mcp`/`lancedb` installed under — check with `python3 -c "import mcp, lancedb"` first; if that fails, find the project's real venv before running any test step).

---

### Task 1: Gate hosted-embedding upload behind explicit consent

**Files:**
- Modify: `src/aletheore/search_index.py:734-775` (`_embed_in_batches`), `:814-820` (`_embed_stale_by_hash`), `:823-887` (`build_index`)
- Modify: `src/aletheore/mcp_server.py:488-509` (`_register_index_tool`), `:630-641` (call site in `build_server`)
- Test: `src/tests/test_search_index.py`, `src/tests/test_mcp_server.py`

**Interfaces:**
- Consumes: existing `get_api_key`, `embed_texts_hosted`, `embed_texts` (unchanged signatures).
- Produces: `_embed_in_batches(texts, batch_size=EMBED_BATCH_SIZE, repo_id=None, allow_hosted=True)`, `_embed_stale_by_hash(stale, repo_id=None, allow_hosted=True)`, `build_index(repo_path, evidence, allow_hosted=True)` — all three gain one new keyword-only-in-practice `bool` parameter, default `True` (preserves current CLI behavior exactly). `_register_index_tool(mcp_instance, repo_path, effects)` gains a third positional parameter.

**Why:** `_embed_in_batches` (`search_index.py:753-756`) silently uses Aletheore's hosted embedding endpoint whenever `ALETHEORE_API_TOKEN` resolves — with **no** TTY/consent check. Contrast `embed_texts`'s OpenAI fallback (`search_index.py:682-695`), which explicitly refuses on a non-interactive session (`sys.stdin.isatty()`) and prompts on a real terminal. Every MCP tool call is non-interactive by definition, so `aletheore_index` called via MCP by a logged-in user silently ships this repo's source chunks to `app.aletheore.com` — directly contradicting `src/README.md:527-532`'s claim and `test_mcp_consent.py`'s own docstring (`:165-191`) that MCP "cannot send code to OpenAI." The codebase already has the right mechanism for exactly this class of decision — `mcp_server.py`'s `EFFECT_EXTERNAL` ("transmits repository evidence to a third-party service", `mcp_server.py:220-232`) — this task wires the hosted-embedding path into it instead of inventing a new consent flow.

- [ ] **Step 1: Write the failing tests**

```python
# src/tests/test_search_index.py — add near the existing _embed_in_batches tests
# (around test_no_token_means_no_hosted_call_at_all, ~line 1271)

def test_allow_hosted_false_skips_hosted_call_even_with_a_token(capsys):
    http = MagicMock()
    with patch("aletheore.search_index.get_api_key", return_value="tok"), \
         patch("aletheore.search_index.httpx.Client", return_value=http), \
         patch("aletheore.search_index.embed_texts", side_effect=lambda t: [[0.0] * 768] * len(t)):
        vectors = _embed_in_batches(["chunk"], allow_hosted=False)

    http.post.assert_not_called()
    assert len(vectors[0]) == 768
    assert "not permitted in this context" in capsys.readouterr().err


def test_allow_hosted_true_is_the_default_and_preserves_existing_behavior():
    http = MagicMock()
    http.post.return_value = _hosted_response(200, {"vectors": [[0.1] * 1536]})
    with patch("aletheore.search_index.get_api_key", return_value="tok"), \
         patch("aletheore.search_index.httpx.Client", return_value=http):
        vectors = _embed_in_batches(["chunk"])  # no allow_hosted kwarg at all

    http.post.assert_called_once()
    assert len(vectors[0]) == 1536
```

```python
# src/tests/test_mcp_server.py — add near test_aletheore_index_tool_builds_the_search_index (~line 371)

@pytest.mark.asyncio
async def test_aletheore_index_tool_forbids_hosted_embeddings_by_default(tmp_path):
    # Default MCP posture is EFFECT_WRITE + EFFECT_NETWORK only (mcp_server.py's
    # _DEFAULT_ALLOWED_EFFECTS) - EFFECT_EXTERNAL is off, so a logged-in user's
    # code must not silently reach the hosted embedding endpoint through MCP.
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)  # default effects, no `allow=` override

    with patch("aletheore.search_index.build_index", return_value=3) as mock_build_index:
        await server.call_tool("aletheore_index", {})

    _, kwargs = mock_build_index.call_args
    assert kwargs["allow_hosted"] is False


@pytest.mark.asyncio
async def test_aletheore_index_tool_permits_hosted_embeddings_when_external_is_allowed(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo, allow=frozenset({"write", "network", "external"}))

    with patch("aletheore.search_index.build_index", return_value=3) as mock_build_index:
        await server.call_tool("aletheore_index", {})

    _, kwargs = mock_build_index.call_args
    assert kwargs["allow_hosted"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src && python3 -m pytest tests/test_search_index.py::test_allow_hosted_false_skips_hosted_call_even_with_a_token tests/test_search_index.py::test_allow_hosted_true_is_the_default_and_preserves_existing_behavior tests/test_mcp_server.py::test_aletheore_index_tool_forbids_hosted_embeddings_by_default tests/test_mcp_server.py::test_aletheore_index_tool_permits_hosted_embeddings_when_external_is_allowed -v`
Expected: FAIL — `_embed_in_batches() got an unexpected keyword argument 'allow_hosted'` and `mock_build_index.call_args` has no `allow_hosted` kwarg.

- [ ] **Step 3: Implement in `search_index.py`**

Replace `_embed_in_batches` (currently `search_index.py:734-775`):

```python
def _embed_in_batches(
    texts: list[str],
    batch_size: int = EMBED_BATCH_SIZE,
    repo_id: str | None = None,
    allow_hosted: bool = True,
) -> list[list[float]]:
    """Embed everything, preferring Aletheore's endpoint when entitled.

    Hosted first, then local, and never the reverse: someone paying for
    hosted embeddings should not silently have their code sent to their own
    OpenAI account instead.

    The fallback is only allowed before the first batch succeeds. After that,
    switching providers mid-run would mix 1536-dimension vectors with 768-
    dimension ones in a single index, which LanceDB rejects outright - so a
    hosted failure partway through is raised rather than worked around. A
    half-built index that errors is recoverable; one built from two models
    is not.

    repo_id: forwarded to embed_texts_hosted so the hosted rate limit can be
    keyed per repo rather than per installation - see _repo_id.

    allow_hosted: the caller's consent to transmit this repository's code to
    Aletheore's hosted embedding endpoint. Defaults to True to preserve the
    CLI's existing interactive behavior. MCP's aletheore_index tool passes
    False unless the operator has explicitly permitted EFFECT_EXTERNAL (see
    mcp_server.py's consent model) - MCP tool calls are always
    non-interactive, so there is no equivalent of embed_texts's isatty()
    prompt available to ask for consent in the moment.
    """
    token = get_api_key(
        "ALETHEORE_API_TOKEN", "aletheore-managed-audit", prompt_fn=lambda _: ""
    )
    use_hosted = bool(token) and allow_hosted
    if token and not allow_hosted:
        print(
            "aletheore: hosted embeddings available but not permitted in this "
            "context; using local provider",
            file=sys.stderr,
        )
    vectors: list[list[float]] = []

    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        if use_hosted:
            try:
                vectors.extend(embed_texts_hosted(batch, token, repo_id=repo_id))
                continue
            except HostedEmbeddingUnavailableError as exc:
                if vectors:
                    raise
                # Nothing embedded yet, so falling back costs no consistency.
                # Printed rather than swallowed: a 402 means the plan
                # changed, which the user needs to see.
                print(f"aletheore: hosted embeddings unavailable ({exc}); using local provider")
                use_hosted = False
        vectors.extend(embed_texts(batch))

    return vectors
```

Update `_embed_stale_by_hash` (currently `search_index.py:814-820`):

```python
def _embed_stale_by_hash(
    stale: list[dict], repo_id: str | None = None, allow_hosted: bool = True
) -> dict[str, list[float]]:
    stale_by_hash = {chunk["chunk_hash"]: chunk["text"] for chunk in stale}
    stale_hashes = list(stale_by_hash)
    fresh_vectors = _embed_in_batches(
        [stale_by_hash[chunk_hash] for chunk_hash in stale_hashes],
        repo_id=repo_id,
        allow_hosted=allow_hosted,
    )
    return dict(zip(stale_hashes, fresh_vectors))
```

In `build_index` (currently `search_index.py:823-887`), change the signature and thread the flag through both call sites of `_embed_stale_by_hash` / `_embed_in_batches`:

```python
def build_index(repo_path: Path, evidence: dict, allow_hosted: bool = True) -> int:
    chunks = build_chunks(evidence, repo_path)
    if not chunks:
        return 0

    for chunk in chunks:
        chunk["chunk_hash"] = _chunk_hash(chunk["text"])

    index_path = _index_path(repo_path)
    reusable = _reusable_vectors(index_path)
    stale = [chunk for chunk in chunks if chunk["chunk_hash"] not in reusable]
    repo = _repo_id(repo_path)
    fresh = _embed_stale_by_hash(stale, repo_id=repo, allow_hosted=allow_hosted)
    fresh_vectors = list(fresh.values())

    if reusable:
        current_dimension = (
            len(fresh_vectors[0])
            if fresh_vectors
            else len(_embed_in_batches([chunks[0]["text"]], repo_id=repo, allow_hosted=allow_hosted)[0])
        )
        reused_dimensions = {len(vector) for vector in reusable.values()}
        if reused_dimensions != {current_dimension}:
            reusable = {}
            stale = chunks
            fresh = _embed_stale_by_hash(stale, repo_id=repo, allow_hosted=allow_hosted)

    rows = [
        {**chunk, "vector": reusable.get(chunk["chunk_hash"]) or fresh[chunk["chunk_hash"]]}
        for chunk in chunks
    ]

    index_path.parent.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(index_path))
    table = db.create_table(TABLE_NAME, data=rows, mode="overwrite")
    try:
        table.create_index("text", config=FTS(), replace=True)
    except Exception:  # noqa: BLE001 - any backend failure degrades, never fails the build
        pass
    return len(rows)
```

(Leave every comment already in `build_index` in place — only the signature and the two `_embed_stale_by_hash`/`_embed_in_batches` call sites change.)

- [ ] **Step 4: Implement in `mcp_server.py`**

Change `_register_index_tool` (currently `mcp_server.py:488-509`):

```python
def _register_index_tool(mcp_instance: MCPServer, repo_path: Path, effects: frozenset[str]) -> None:
    @mcp_instance.tool(
        name="aletheore_index",
        # Writes the vector index and sends code chunks to the embedding
        # provider - Aletheore's hosted endpoint if entitled and permitted,
        # else local Ollama, else OpenAI on fallback.
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
        ),
    )
    def aletheore_index() -> str:
        """Build the semantic search index for this repo's evidence, required
        before aletheore_search_codebase or aletheore_answer can be used.
        Embeds via Aletheore's hosted endpoint if this session has permission
        to transmit evidence externally and a token is available, else a
        local Ollama instance, falling back to OpenAI if that's unavailable
        too."""
        from aletheore.search_index import build_index

        evidence = read_evidence(repo_path)
        try:
            count = build_index(repo_path, evidence, allow_hosted=EFFECT_EXTERNAL in effects)
        except Exception as exc:  # noqa: BLE001
            return _toon_result({"error": str(exc)})
        return _toon_result({"indexed_chunks": count})
```

Update the call site in `build_server` (currently `mcp_server.py:634-635`):

```python
    if permitted("aletheore_index"):
        _register_index_tool(mcp_instance, repo_path, effects)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd src && python3 -m pytest tests/test_search_index.py tests/test_mcp_server.py tests/test_mcp_consent.py -v`
Expected: PASS — including all pre-existing tests in these three files (no regressions).

- [ ] **Step 6: Commit**

```bash
git add src/aletheore/search_index.py src/aletheore/mcp_server.py src/tests/test_search_index.py src/tests/test_mcp_server.py
git commit -m "fix: gate hosted-embedding upload behind EFFECT_EXTERNAL consent

aletheore_index called via MCP silently shipped this repo's source chunks
to Aletheore's hosted embedding endpoint whenever a saved token existed -
no TTY/consent check, unlike the OpenAI-fallback path which explicitly
refuses on a non-interactive session. Wires the hosted-embedding decision
into the existing EFFECT_EXTERNAL consent class instead of inventing a new
gate, so it's off by default under MCP and matches what the README/tests
already claim."
```

---

### Task 2: Guard against a dimension mismatch on the search side

**Files:**
- Modify: `src/aletheore/search_index.py:36-37` (near `IndexNotFoundError`), `:1096-1103` (`search_index`)
- Modify: `src/aletheore/mcp_server.py:37` (import), `:517-535` (`_register_search_codebase_tool`)
- Test: `src/tests/test_search_index.py`, `src/tests/test_mcp_server.py`

**Interfaces:**
- Produces: `class IndexDimensionMismatchError(Exception)` in `search_index.py`, raised by `search_index()`.
- Consumes/re-exports: `mcp_server.py` imports it alongside the existing `IndexNotFoundError, search_index` at line 37.

**Why:** `build_index` has careful dimension-drift handling (`search_index.py:848-886`, comment explains the exact reproduction: "index with Ollama, lose Ollama, and the next build crashed on the fallback rather than degrading"). `search_index()` (`search_index.py:1096-1103`) has none: it always embeds the query with the pure-local `embed_texts`, with no check against the table's actual stored vector width. An index built via the hosted path (1536-dim, `text-embedding-3-small`) searched with a 768-dim local query vector currently either crashes inside LanceDB with an opaque error or (worse) silently ranks nonsense, for exactly the paying users hosted embeddings exist for. `src/tests/test_search_index.py:1202-1260` covers the build side only — no existing test covers search-after-hosted-build.

- [ ] **Step 1: Write the failing test**

```python
# src/tests/test_search_index.py — add near the other IndexNotFoundError-adjacent tests

def test_search_index_raises_a_clear_error_on_dimension_mismatch(tmp_path):
    """An index built with 1536-dim hosted vectors, searched with a 768-dim
    local query vector, must fail with an actionable message - not an
    opaque LanceDB internal error and not silently wrong rankings."""
    from aletheore.search_index import (
        IndexDimensionMismatchError,
        TABLE_NAME,
        _index_path,
        search_index,
    )
    import lancedb

    repo = tmp_path
    index_path = _index_path(repo)
    index_path.parent.mkdir(parents=True)
    db = lancedb.connect(str(index_path))
    db.create_table(
        TABLE_NAME,
        data=[
            {
                "module_path": "a.py",
                "symbol_name": "foo",
                "start_line": 1,
                "end_line": 2,
                "language": "python",
                "imports": [],
                "text": "def foo(): pass",
                "chunk_hash": "abc",
                "vector": [0.1] * 1536,
            }
        ],
    )

    with patch("aletheore.search_index.embed_texts", return_value=[[0.0] * 768]):
        with pytest.raises(IndexDimensionMismatchError, match="1536.*768|768.*1536"):
            search_index(repo, "where is foo")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src && python3 -m pytest tests/test_search_index.py::test_search_index_raises_a_clear_error_on_dimension_mismatch -v`
Expected: FAIL — `ImportError: cannot import name 'IndexDimensionMismatchError'`.

- [ ] **Step 3: Implement**

Add the exception next to the existing ones in `search_index.py` (near line 36-37):

```python
class IndexNotFoundError(Exception):
    pass


class IndexDimensionMismatchError(Exception):
    pass
```

Update `search_index()` (currently `search_index.py:1096-1103`):

```python
def search_index(
    repo_path: Path, query_text: str, k: int = 10, language: str | None = None
) -> list[dict]:
    if language is None:
        # The question may name its own language - see _detect_query_language.
        language = _detect_query_language(query_text)
    table = open_index(repo_path)
    query_vector = embed_texts([query_text])[0]

    # The index and the query must come from the same embedding model - see
    # build_index's dimension-drift handling for the mechanism that keeps
    # the index internally consistent. This is the mirror check for the
    # query itself: the available provider can differ between when the
    # index was built and when it's searched (e.g. a hosted token was
    # revoked, or Ollama came up where it wasn't before), and a raw
    # dimension mismatch otherwise surfaces as an opaque LanceDB error deep
    # inside table.search() rather than a message telling the user what to
    # do about it.
    table_dimension = table.schema.field("vector").type.list_size
    if len(query_vector) != table_dimension:
        raise IndexDimensionMismatchError(
            f"the index at {_index_path(repo_path)} holds {table_dimension}-dimension "
            f"vectors but the query embedded to {len(query_vector)} dimensions with the "
            f"embedding provider available right now - re-run 'aletheore index {repo_path}' "
            "to rebuild the index with the current provider"
        )

    # Over-fetch, then thin by file: the chunks displaced by the per-file cap
    # have to be replaced by something, and that something is only available
    # if the search returned more than k to begin with.
    limit = k * _OVERFETCH_FACTOR
    vector_query = table.search(query_vector).limit(limit)
    if language:
        vector_query = vector_query.where(f"language = '{_escape_sql_literal(language)}'")
    candidates = _rrf_fuse(
        vector_query.to_list(), _fts_candidates(table, query_text, limit, language)
    )

    per_file: dict[str, int] = {}
    raw_results = []
    for candidate in candidates:
        path = candidate["module_path"]
        if per_file.get(path, 0) >= MAX_CHUNKS_PER_FILE:
            continue
        per_file[path] = per_file.get(path, 0) + 1
        raw_results.append(candidate)
        if len(raw_results) == k:
            break
    return [
        {
            "module_path": result["module_path"],
            "symbol_name": result["symbol_name"],
            "start_line": result["start_line"],
            "end_line": result["end_line"],
            "language": result["language"],
            "imports": result.get("imports") or [],
            "text": result["text"],
            "score": result.get("_distance"),
        }
        for result in raw_results
    ]
```

In `mcp_server.py`, update the import at line 37:

```python
from aletheore.search_index import IndexDimensionMismatchError, IndexNotFoundError, search_index
```

Update `_register_search_codebase_tool` (currently `mcp_server.py:517-535`) to handle the new error:

```python
    def aletheore_search_codebase(query: str, k: int = 10, language: str | None = None) -> str:
        """Hybrid search (meaning + exact identifiers) over the repository's
        indexed code. language: optional filter, e.g. 'python', 'typescript' -
        use it on a polyglot repo when the question is about one stack, since
        an unfiltered search ranks every language's chunks against each other.
        Names must match evidence's own repository.languages values."""
        try:
            return _toon_result(search_index(repo_path, query, k=k, language=language))
        except IndexNotFoundError:
            return _toon_result(_NO_INDEX_ERROR)
        except IndexDimensionMismatchError as exc:
            return _toon_result({"error": str(exc)})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src && python3 -m pytest tests/test_search_index.py::test_search_index_raises_a_clear_error_on_dimension_mismatch -v`
Expected: PASS

- [ ] **Step 5: Add the MCP-level test**

```python
# src/tests/test_mcp_server.py — add near test_aletheore_search_codebase_returns_friendly_error_when_index_not_built

@pytest.mark.asyncio
async def test_aletheore_search_codebase_returns_friendly_error_on_dimension_mismatch(tmp_path):
    from aletheore.mcp_server import IndexDimensionMismatchError

    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    with patch(
        "aletheore.mcp_server.search_index",
        side_effect=IndexDimensionMismatchError("the index at ... holds 1536-dimension vectors ..."),
    ):
        result = await server.call_tool("aletheore_search_codebase", {"query": "where is foo"})

    assert "1536-dimension" in tool_result_body(result)["result"]["error"]
```

- [ ] **Step 6: Run the full file and verify it passes**

Run: `cd src && python3 -m pytest tests/test_search_index.py tests/test_mcp_server.py -v`
Expected: PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
git add src/aletheore/search_index.py src/aletheore/mcp_server.py src/tests/test_search_index.py src/tests/test_mcp_server.py
git commit -m "fix: raise a clear error instead of corrupting/crashing on index dimension mismatch

build_index already handles the embedding-provider-changed-underneath-it
case; search_index had no equivalent guard, so a hosted-built (1536-dim)
index searched after the provider changed either crashed inside LanceDB
with an opaque error or silently ranked garbage against a 768-dim local
query vector. Same class of bug the build-side comment at search_index.py
already documents reproducing, just on the other half of the code path."
```

---

### Task 3: Fix `aletheore_changes` bypassing the version-compatibility check

**Files:**
- Modify: `src/aletheore/mcp_server.py:303-315` (`_register_changes_tool`)
- Test: `src/tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `load_evidence_file` (already imported at `mcp_server.py:18`), `IncompatibleEvidenceVersionError`, `MalformedEvidenceError` (already imported at `mcp_server.py:16-17`).

**Why:** Every other evidence reader in this file routes through `load_evidence_file`, which checks `aletheore_version` compatibility before returning (see `read_evidence`'s own comment, `mcp_server.py:67-75`, explaining exactly why: "a truncated or hand-edited air.json with a *compatible* version used to pass straight through here and only surface as a raw KeyError deep inside whichever tool first touched the missing/wrong field"). `_register_changes_tool` (`mcp_server.py:303-315`) is the one exception — it reads snapshot files with a bare `json.loads`, so a stale or incompatible snapshot produces a raw, unhelpful exception (or a `KeyError` inside `compute_diff`) instead of the same clear, actionable error every other tool gives.

- [ ] **Step 1: Write the failing test**

```python
# src/tests/test_mcp_server.py — add near the other test_read_evidence_rejects_* tests

@pytest.mark.asyncio
async def test_aletheore_changes_returns_clear_error_for_incompatible_snapshot(tmp_path):
    from aletheore.history import save_snapshot
    from tests.air_fixtures import minimal_air_evidence

    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    evidence = minimal_air_evidence()
    save_snapshot(evidence, repo)  # first snapshot, compatible version

    bad = minimal_air_evidence()
    bad["aletheore_version"] = "0.0.1-does-not-exist"
    save_snapshot(bad, repo)  # second snapshot, incompatible

    result = await server.call_tool("aletheore_changes", {})

    body = tool_result_body(result)["result"]
    assert "error" in body
    assert "0.0.1-does-not-exist" in body["error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src && python3 -m pytest tests/test_mcp_server.py::test_aletheore_changes_returns_clear_error_for_incompatible_snapshot -v`
Expected: FAIL — either an uncaught `IncompatibleEvidenceVersionError` propagating out of the tool call, or a `KeyError`/assertion failure because the current code returns `{"message": ...}` shaped output for a bare-JSON success case instead of an `"error"` key.

- [ ] **Step 3: Implement**

Replace `_register_changes_tool` (currently `mcp_server.py:303-315`):

```python
def _register_changes_tool(mcp_instance: MCPServer, repo_path: Path) -> None:
    @mcp_instance.tool(name="aletheore_changes", annotations=READ_ONLY_ANNOTATIONS)
    def aletheore_changes(full: bool = False) -> str:
        """What changed between the two most recent scans of this repo."""
        snapshots = list_snapshots(repo_path)
        if len(snapshots) < 2:
            return _toon_result({"message": "no prior snapshot to compare against"})
        try:
            old = load_evidence_file(snapshots[-2])
            new = load_evidence_file(snapshots[-1])
        except json.JSONDecodeError:
            return _toon_result({"message": f"most recent snapshot is unreadable ({snapshots[-2]})"})
        except (IncompatibleEvidenceVersionError, MalformedEvidenceError) as exc:
            return _toon_result({"error": str(exc)})
        return _toon_result(compute_diff(old, new, full=full))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src && python3 -m pytest tests/test_mcp_server.py::test_aletheore_changes_returns_clear_error_for_incompatible_snapshot -v`
Expected: PASS

- [ ] **Step 5: Run the full file to check for regressions**

Run: `cd src && python3 -m pytest tests/test_mcp_server.py -v -k changes`
Expected: PASS, including any pre-existing `aletheore_changes` tests.

- [ ] **Step 6: Commit**

```bash
git add src/aletheore/mcp_server.py src/tests/test_mcp_server.py
git commit -m "fix: route aletheore_changes through the version-checking evidence loader

Every other evidence reader in this file uses load_evidence_file, which
refuses an incompatible aletheore_version with a clear, actionable error.
aletheore_changes read snapshot files with a bare json.loads instead, so a
stale snapshot surfaced as a raw exception or a KeyError inside
compute_diff rather than the same clear message every other tool gives."
```

---

### Task 4: Make `aletheore_search` respect the repo's ignore patterns

**Files:**
- Modify: `src/aletheore/mcp_server.py:36` (import), `:158-185` (`_search_files`)
- Test: `src/tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `load_repo_config` (new import from `aletheore.repo_config`), whose return dict has an `"ignored_paths"` key (confirmed usage pattern at `src/aletheore/secrets.py:201-204`).

**Why:** The real scanner (`secrets.py:201-204`) calls `iter_all_files(repo_path, load_repo_config(repo_path)["ignored_paths"])` — `.aletheore.yml`'s `ignored_paths` config is honored everywhere else. `_search_files` (`mcp_server.py:158-185`) calls `iter_all_files(repo_path)` with no second argument (`mcp_server.py:166`) — `iter_all_files` already accepts an optional `ignored_paths` parameter (`secrets.py:86`), it's just never passed here. Anything a repo owner has explicitly configured to be ignored (generated code, vendored bundles, large data fixtures) is currently searched anyway by `aletheore_search`.

- [ ] **Step 1: Write the failing test**

```python
# src/tests/test_mcp_server.py — add near the search tests

@pytest.mark.asyncio
async def test_aletheore_search_respects_repo_config_ignored_paths(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    (repo / "vendor").mkdir()
    (repo / "vendor" / "bundle.js").write_text("needle in a vendored file")
    (repo / "real.py").write_text("needle in a real file")
    (repo / ".aletheore.yml").write_text("ignored_paths:\n  - vendor/**\n")
    server = build_server(repo)

    result = await server.call_tool("aletheore_search", {"pattern": "needle"})

    matches = tool_result_body(result)["result"]["matches"]
    matched_paths = {m["path"] for m in matches}
    assert "real.py" in matched_paths
    assert "vendor/bundle.js" not in matched_paths
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src && python3 -m pytest tests/test_mcp_server.py::test_aletheore_search_respects_repo_config_ignored_paths -v`
Expected: FAIL — `vendor/bundle.js` is in `matched_paths`.

- [ ] **Step 3: Implement**

Add the import in `mcp_server.py` near line 36:

```python
from aletheore.repo_config import load_repo_config
from aletheore.secrets import iter_all_files
```

Update `_search_files` (currently `mcp_server.py:158-185`):

```python
def _search_files(repo_path: Path, pattern: str, regex: bool, path_glob: str | None) -> dict:
    """The actual search. Literal (non-regex) mode has no backtracking risk
    and is called directly in-process; regex mode is only ever called
    through _run_search in a subprocess (see _SEARCH_TIMEOUT_SECONDS)."""
    compiled = re.compile(pattern) if regex else None
    matches: list[dict] = []
    truncated = False
    ignored_paths = load_repo_config(repo_path)["ignored_paths"]

    for path in iter_all_files(repo_path, ignored_paths):
        rel_path = path.relative_to(repo_path).as_posix()
        if path_glob is not None and not PurePath(rel_path).match(path_glob):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for line_no, line in enumerate(text.splitlines(), start=1):
            found = compiled.search(line) is not None if compiled else pattern in line
            if found:
                if len(matches) >= _SEARCH_MATCH_CAP:
                    truncated = True
                    break
                matches.append({"path": rel_path, "line": line_no, "text": line})
        if truncated:
            break

    return {"matches": matches, "truncated": truncated}
```

(This single change covers both the literal in-process path and the regex subprocess path — both call `_search_files`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src && python3 -m pytest tests/test_mcp_server.py::test_aletheore_search_respects_repo_config_ignored_paths -v`
Expected: PASS

- [ ] **Step 5: Run the full file to check for regressions**

Run: `cd src && python3 -m pytest tests/test_mcp_server.py -v -k search`
Expected: PASS, including the existing search timeout/regex/truncation tests.

- [ ] **Step 6: Commit**

```bash
git add src/aletheore/mcp_server.py src/tests/test_mcp_server.py
git commit -m "fix: make aletheore_search respect .aletheore.yml's ignored_paths

The real scanner (secrets.py) and every file-walk site already honor
repo-configured ignore patterns; aletheore_search called iter_all_files
with no ignored_paths argument even though the function already accepts
one, so vendored/generated files a repo owner explicitly excluded were
searched anyway."
```

---

### Task 5: Add `aletheore_list` — list modules, clusters, and branches by name

**Files:**
- Modify: `src/aletheore/query.py` (add three functions)
- Modify: `src/aletheore/mcp_server.py` (add one new tool registration)
- Test: `src/tests/test_query.py`, `src/tests/test_mcp_server.py`

**Interfaces:**
- Produces (in `query.py`): `list_modules(evidence: dict) -> list[str]`, `list_clusters(evidence: dict) -> list[dict]`, `list_branches(evidence: dict) -> list[str]`.
- Produces (in `mcp_server.py`): tool `aletheore_list(kind: str) -> str`, `kind` one of `"modules"`, `"clusters"`, `"branches"`.

**Why:** 8 of the 16 dynamic query tools (`aletheore_imports`, `_imported_by`, `_symbols`, `_secrets`, `_cluster` need a file path; `_branch` needs a branch name) require an exact name "as it appears in evidence" — but no tool returns the list of valid names. An agent must bootstrap from `aletheore_search` or the raw filesystem before most of the toolset is usable. Confirmed against this repo's own evidence (`.aletheore/air.json`): `repository.modules[].path`, `architecture.clusters[].id`/`.modules`, `git.branches[].name` are the exact real shapes.

One tool with a `kind` parameter, not three separate tools — avoids repeating the `imports`/`imported_by`/`cluster` redundancy already flagged in the audit (three tool-list slots for what `aletheore_neighborhood` already returns in one call).

- [ ] **Step 1: Write the failing tests**

```python
# src/tests/test_query.py — add at the end of the file

def test_list_modules_returns_every_module_path():
    from aletheore.query import list_modules

    evidence = {"repository": {"modules": [{"path": "a.py"}, {"path": "b.py"}]}}
    assert list_modules(evidence) == ["a.py", "b.py"]


def test_list_clusters_returns_id_and_module_count():
    from aletheore.query import list_clusters

    evidence = {
        "architecture": {
            "clusters": [
                {"id": 0, "modules": ["a.py", "b.py"], "internal_edges": 1},
                {"id": 1, "modules": ["c.py"], "internal_edges": 0},
            ]
        }
    }
    assert list_clusters(evidence) == [
        {"id": 0, "module_count": 2},
        {"id": 1, "module_count": 1},
    ]


def test_list_branches_returns_every_branch_name():
    from aletheore.query import list_branches

    evidence = {"git": {"branches": [{"name": "main"}, {"name": "dev"}]}}
    assert list_branches(evidence) == ["main", "dev"]
```

```python
# src/tests/test_mcp_server.py — add near test_aletheore_imports_tool_returns_correct_result

@pytest.mark.asyncio
async def test_aletheore_list_modules(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("aletheore_list", {"kind": "modules"})

    assert tool_result_body(result) == {"result": ["a.py", "b.py"]}


@pytest.mark.asyncio
async def test_aletheore_list_unknown_kind_returns_an_error(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("aletheore_list", {"kind": "nonsense"})

    assert "error" in tool_result_body(result)["result"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src && python3 -m pytest tests/test_query.py -k list_ tests/test_mcp_server.py -k aletheore_list -v`
Expected: FAIL — `ImportError` / `ToolError: unknown tool "aletheore_list"`.

- [ ] **Step 3: Implement in `query.py`**

Add near the end of `query.py`, before `QUERY_FUNCTIONS`:

```python
def list_modules(evidence: dict) -> list[str]:
    return [module["path"] for module in evidence["repository"]["modules"]]


def list_clusters(evidence: dict) -> list[dict]:
    return [
        {"id": cluster["id"], "module_count": len(cluster["modules"])}
        for cluster in evidence["architecture"]["clusters"]
    ]


def list_branches(evidence: dict) -> list[str]:
    return [branch["name"] for branch in evidence["git"]["branches"]]
```

- [ ] **Step 4: Implement in `mcp_server.py`**

Add the import at the top, alongside the existing `query` import (`mcp_server.py:25-35`):

```python
from aletheore.query import (
    ModuleNotFoundInEvidenceError,
    QUERY_FUNCTIONS,
    find_code_evidence_for_dependency,
    find_code_evidence_for_endpoint,
    find_code_evidence_for_symbol,
    find_cluster,
    find_imported_by,
    find_imports,
    find_symbol_source,
    list_branches,
    list_clusters,
    list_modules,
)
```

Add a new registration function, next to `_register_neighborhood_tool`:

```python
_LIST_KIND_TO_FUNCTION = {
    "modules": list_modules,
    "clusters": list_clusters,
    "branches": list_branches,
}


def _register_list_tool(mcp_instance: MCPServer, repo_path: Path) -> None:
    @mcp_instance.tool(name="aletheore_list", annotations=READ_ONLY_ANNOTATIONS)
    def aletheore_list(kind: str) -> str:
        """Lists the valid names/identifiers for one evidence collection, so
        other tools' exact-match `target` arguments can be filled in
        correctly. kind: one of 'modules' (file paths, for aletheore_imports/
        _imported_by/_symbols/_secrets/_cluster/_neighborhood/_symbol_source's
        module argument), 'clusters' (architecture cluster ids), or
        'branches' (git branch names, for aletheore_branch)."""
        func = _LIST_KIND_TO_FUNCTION.get(kind)
        if func is None:
            return _toon_result(
                {"error": f"unknown kind {kind!r} - expected one of {sorted(_LIST_KIND_TO_FUNCTION)}"}
            )
        evidence = read_evidence(repo_path)
        return _toon_result(func(evidence))
```

Register it in `build_server` next to `_register_neighborhood_tool` (currently `mcp_server.py:615-620`):

```python
    _register_query_wrapper_tools(mcp_instance, repo_path)
    _register_changes_tool(mcp_instance, repo_path)
    _register_neighborhood_tool(mcp_instance, repo_path)
    _register_list_tool(mcp_instance, repo_path)
    _register_search_tool(mcp_instance, repo_path)
    _register_symbol_source_tool(mcp_instance, repo_path)
    _register_code_evidence_tools(mcp_instance, repo_path)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd src && python3 -m pytest tests/test_query.py -k list_ tests/test_mcp_server.py -k aletheore_list -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/aletheore/query.py src/aletheore/mcp_server.py src/tests/test_query.py src/tests/test_mcp_server.py
git commit -m "feat: add aletheore_list MCP tool for modules/clusters/branches

8 of the 16 dynamic query tools require an exact name 'as it appears in
evidence', but nothing returned the list of valid names - an agent had to
bootstrap from aletheore_search or the raw filesystem before most of the
toolset was usable. One tool with a kind parameter rather than three
separate ones, to avoid repeating the existing imports/imported_by/cluster
redundancy that aletheore_neighborhood already solves."
```

---

### Task 6: Add `aletheore_overview` — the "what is this repo?" tool

**Files:**
- Modify: `src/aletheore/query.py` (add one function)
- Modify: `src/aletheore/mcp_server.py` (add one new tool registration)
- Test: `src/tests/test_query.py`, `src/tests/test_mcp_server.py`

**Interfaces:**
- Produces (in `query.py`): `find_repo_overview(evidence: dict) -> dict`.
- Produces (in `mcp_server.py`): tool `aletheore_overview() -> str`, no arguments.

**Why:** `repository.languages`, `.frameworks`, `.monorepo`, `.dependency_graph`, `git.commit_cadence`, `.repo_age_days`, `.total_commits`, and `architecture.cross_cluster_edges` are all real evidence fields (confirmed directly against this repo's own `.aletheore/air.json`) reachable by **no** existing tool. "What is this repo?" is the first question any agent asks, and today answering it means calling several of the wrong-shaped tools or none at all. The full `dependency_graph` (nodes+edges) and full `cross_cluster_edges` list can be large for a big repo, so this returns counts for those rather than the raw graph, consistent with the file's existing TOON-cost-consciousness (see the `_toon_result` comment at `mcp_server.py:81-87`) — an agent that wants the full graph already has `aletheore_neighborhood`/`aletheore_cluster` for that.

- [ ] **Step 1: Write the failing test**

```python
# src/tests/test_query.py — add at the end of the file

def test_find_repo_overview_summarizes_the_real_evidence_shape():
    from aletheore.query import find_repo_overview

    evidence = {
        "repository": {
            "languages": [{"name": "python", "file_count": 271, "loc": 65970}],
            "frameworks": [{"name": "fastapi"}],
            "monorepo": {"detected": False, "workspaces": []},
            "dependency_graph": {"nodes": ["a.py", "b.py"], "edges": [["a.py", "b.py"]]},
            "modules": [{"path": "a.py"}, {"path": "b.py"}],
        },
        "architecture": {
            "clusters": [{"id": 0, "modules": ["a.py"], "internal_edges": 0}],
            "cross_cluster_edges": [["a.py", "b.py"]],
        },
        "git": {
            "repo_age_days": 400,
            "total_commits": 1200,
            "commit_cadence": {"weekly_counts": [10, 20], "trend": "increasing"},
            "branches": [{"name": "main"}, {"name": "dev"}],
        },
    }

    overview = find_repo_overview(evidence)

    assert overview == {
        "languages": [{"name": "python", "file_count": 271, "loc": 65970}],
        "frameworks": [{"name": "fastapi"}],
        "monorepo": {"detected": False, "workspaces": []},
        "dependency_graph_summary": {"node_count": 2, "edge_count": 1},
        "module_count": 2,
        "cluster_count": 1,
        "cross_cluster_edge_count": 1,
        "git": {
            "repo_age_days": 400,
            "total_commits": 1200,
            "commit_cadence": {"weekly_counts": [10, 20], "trend": "increasing"},
            "branch_count": 2,
        },
    }
```

```python
# src/tests/test_mcp_server.py — add near test_aletheore_imports_tool_returns_correct_result

@pytest.mark.asyncio
async def test_aletheore_overview_returns_repo_summary(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("aletheore_overview", {})

    body = tool_result_body(result)["result"]
    assert body["module_count"] == 2
    assert "languages" in body
    assert "git" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src && python3 -m pytest tests/test_query.py -k overview tests/test_mcp_server.py -k overview -v`
Expected: FAIL — `ImportError` / `ToolError: unknown tool "aletheore_overview"`.

- [ ] **Step 3: Implement in `query.py`**

Add near the other `find_*` functions (grouping doesn't matter, but keep it near `list_modules`/`list_clusters`/`list_branches` added in Task 5):

```python
def find_repo_overview(evidence: dict) -> dict:
    repo = evidence["repository"]
    git = evidence["git"]
    arch = evidence["architecture"]
    dependency_graph = repo["dependency_graph"]
    return {
        "languages": repo["languages"],
        "frameworks": repo["frameworks"],
        "monorepo": repo["monorepo"],
        "dependency_graph_summary": {
            "node_count": len(dependency_graph["nodes"]),
            "edge_count": len(dependency_graph["edges"]),
        },
        "module_count": len(repo["modules"]),
        "cluster_count": len(arch["clusters"]),
        "cross_cluster_edge_count": len(arch["cross_cluster_edges"]),
        "git": {
            "repo_age_days": git["repo_age_days"],
            "total_commits": git["total_commits"],
            "commit_cadence": git["commit_cadence"],
            "branch_count": len(git["branches"]),
        },
    }
```

- [ ] **Step 4: Implement in `mcp_server.py`**

Add the import alongside the Task 5 additions:

```python
from aletheore.query import (
    ModuleNotFoundInEvidenceError,
    QUERY_FUNCTIONS,
    find_code_evidence_for_dependency,
    find_code_evidence_for_endpoint,
    find_code_evidence_for_symbol,
    find_cluster,
    find_imported_by,
    find_imports,
    find_repo_overview,
    find_symbol_source,
    list_branches,
    list_clusters,
    list_modules,
)
```

Add a registration function next to `_register_list_tool`:

```python
def _register_overview_tool(mcp_instance: MCPServer, repo_path: Path) -> None:
    @mcp_instance.tool(name="aletheore_overview", annotations=READ_ONLY_ANNOTATIONS)
    def aletheore_overview() -> str:
        """A repo-level summary: languages, frameworks, monorepo structure,
        dependency-graph size, module/cluster counts, and git age/commit
        cadence/branch count. The starting point for 'what is this repo?' -
        call this before anything else on an unfamiliar repository."""
        evidence = read_evidence(repo_path)
        return _toon_result(find_repo_overview(evidence))
```

Register it in `build_server`, next to `_register_list_tool`:

```python
    _register_query_wrapper_tools(mcp_instance, repo_path)
    _register_changes_tool(mcp_instance, repo_path)
    _register_neighborhood_tool(mcp_instance, repo_path)
    _register_list_tool(mcp_instance, repo_path)
    _register_overview_tool(mcp_instance, repo_path)
    _register_search_tool(mcp_instance, repo_path)
    _register_symbol_source_tool(mcp_instance, repo_path)
    _register_code_evidence_tools(mcp_instance, repo_path)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd src && python3 -m pytest tests/test_query.py -k overview tests/test_mcp_server.py -k overview -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/aletheore/query.py src/aletheore/mcp_server.py src/tests/test_query.py src/tests/test_mcp_server.py
git commit -m "feat: add aletheore_overview MCP tool - the 'what is this repo?' answer

repository.languages/.frameworks/.monorepo/.dependency_graph and
git.commit_cadence/.repo_age_days/.total_commits sit in evidence but were
reachable by no tool - the first question any agent asks about an
unfamiliar repo had no single call to answer it. Returns summarized
counts for the dependency graph and cross-cluster edges rather than the
raw graph, matching the file's existing TOON-cost-consciousness -
aletheore_neighborhood/_cluster already cover the full-detail case."
```

---

### Task 7: Expose citation verification as `aletheore_verify_citations`

**Files:**
- Modify: `src/aletheore/mcp_server.py` (add one new tool registration)
- Test: `src/tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `verify_citations` and `local_line_count_fetcher` from `aletheore.citation_verifier` (`citation_verifier.py:105`, `:169`).
- Produces: tool `aletheore_verify_citations(report_text: str) -> str`.

**Why:** `aletheore verify` (CLI, `cli.py:865-895`) checks a report's `file:line` citations against real evidence and real file line counts — arguably the product's most differentiated capability, and precisely what an agent writing a grounded report from these same MCP tools would want before presenting it. Not exposed via MCP at all. Takes `report_text` directly (a string) rather than a file path: an MCP-calling agent already has the report text in its own context and has no reason to round-trip it through disk first, unlike the CLI's `_verify` which is driven by a human pointing at an existing file.

- [ ] **Step 1: Write the failing test**

```python
# src/tests/test_mcp_server.py — add near test_aletheore_symbol_source_returns_exact_source

@pytest.mark.asyncio
async def test_aletheore_verify_citations_reports_verified_and_unverified(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    (repo / "a.py").write_text("def foo():\n    pass\n")
    server = build_server(repo)

    report = "See `a.py:1` for the real one and `nonexistent.py:5` for the fake one."
    result = await server.call_tool("aletheore_verify_citations", {"report_text": report})

    body = tool_result_body(result)["result"]
    assert body["total_citations"] == 2
    assert len(body["verified"]) == 1
    assert len(body["unverified"]) == 1
    assert body["unverified"][0]["file"] == "nonexistent.py"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src && python3 -m pytest tests/test_mcp_server.py::test_aletheore_verify_citations_reports_verified_and_unverified -v`
Expected: FAIL — `ToolError: unknown tool "aletheore_verify_citations"`.

- [ ] **Step 3: Implement**

Add a registration function in `mcp_server.py`, next to `_register_symbol_source_tool`:

```python
def _register_verify_citations_tool(mcp_instance: MCPServer, repo_path: Path) -> None:
    @mcp_instance.tool(name="aletheore_verify_citations", annotations=READ_ONLY_ANNOTATIONS)
    def aletheore_verify_citations(report_text: str) -> str:
        """Checks every `file:line` citation in report_text against this
        repo's real evidence and real file line counts. Call this on any
        report you write before presenting it - a citation naming a file
        that isn't in evidence, or a line beyond the file's real length, is
        flagged as unverified rather than trusted."""
        from aletheore.citation_verifier import local_line_count_fetcher, verify_citations

        evidence = read_evidence(repo_path)
        result = verify_citations(
            report_text, evidence, fetch_line_count=local_line_count_fetcher(repo_path)
        )
        return _toon_result(result)
```

Register it in `build_server`, next to `_register_symbol_source_tool` (currently `mcp_server.py:619`):

```python
    _register_symbol_source_tool(mcp_instance, repo_path)
    _register_verify_citations_tool(mcp_instance, repo_path)
    _register_code_evidence_tools(mcp_instance, repo_path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src && python3 -m pytest tests/test_mcp_server.py::test_aletheore_verify_citations_reports_verified_and_unverified -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/aletheore/mcp_server.py src/tests/test_mcp_server.py
git commit -m "feat: add aletheore_verify_citations MCP tool

aletheore verify (CLI) checks a report's file:line citations against real
evidence and real file line counts - arguably the product's most
differentiated capability, and exactly what an agent writing a grounded
report from these same MCP tools would want before presenting it. Takes
report_text directly rather than a file path, since an MCP-calling agent
already has the text in context."
```

---

### Task 8: Update the tool-inventory tests for the three new tools

**Files:**
- Modify: `src/tests/test_mcp_server.py:220-263` (`test_build_server_registers_expected_tools`)
- Modify: `src/tests/test_mcp_consent.py` (any hardcoded default-tool-count assertions)

**Interfaces:**
- Consumes: nothing new — this task only updates assertions to match the tool set Tasks 1-7 produced.

**Why:** `test_build_server_registers_expected_tools` (`test_mcp_server.py:262`) asserts `len(names) == 28` — the pre-existing full inventory. After Tasks 5-7 add `aletheore_list`, `aletheore_overview`, `aletheore_verify_citations` (all unconditionally-registered read-only tools), the full inventory (with every effect allowed) becomes 31. `test_mcp_consent.py` may have its own count-based assertions for the *default* posture (previously 27; now 30, since the three new tools are all read-only and register unconditionally regardless of `ALETHEORE_MCP_ALLOW`).

- [ ] **Step 1: Update the full-inventory test**

In `test_mcp_server.py`, update `test_build_server_registers_expected_tools` (currently ending at line 262-263):

```python
    expected = {
        "aletheore_imports",
        "aletheore_imported_by",
        "aletheore_symbols",
        "aletheore_branch",
        "aletheore_ownership",
        "aletheore_secrets",
        "aletheore_vulnerabilities",
        "aletheore_licenses",
        "aletheore_endpoints",
        "aletheore_cluster",
        "aletheore_layer_violations",
        "aletheore_dead_code",
        "aletheore_hotspots",
        "aletheore_database",
        "aletheore_infrastructure",
        "aletheore_environment_variables",
        "aletheore_changes",
        "aletheore_neighborhood",
        "aletheore_list",
        "aletheore_overview",
        "aletheore_verify_citations",
        "aletheore_search",
        "aletheore_symbol_source",
        "aletheore_scan",
        "aletheore_healthcheck",
        "aletheore_index",
        "aletheore_search_codebase",
        "aletheore_managed_audit",
        "aletheore_find_evidence_for_endpoint",
        "aletheore_find_evidence_for_symbol",
        "aletheore_find_evidence_for_dependency",
    }
    assert expected.issubset(names)
    assert len(names) == 31
    assert "aletheore_answer" not in names
```

- [ ] **Step 2: Run it, find every other hardcoded count**

Run: `cd src && python3 -m pytest tests/test_mcp_server.py::test_build_server_registers_expected_tools -v`
Expected: FAIL first (still says 28), then search the whole consent-test file for any other now-stale count:

Run: `grep -n "len(names)\|== 27\|== 28\|_DEFAULT" tests/test_mcp_consent.py`

For each match found, update the expected count the same way: add 3 for every posture that includes the default read-only tool set (every posture does, since `aletheore_list`/`_overview`/`_verify_citations` register unconditionally like the other 20 always-on read-only tools — they are never in `TOOL_REQUIRED_EFFECTS` and never behind `permitted(...)`).

- [ ] **Step 3: Run the full test suite to verify no other count is stale**

Run: `cd src && python3 -m pytest tests/test_mcp_server.py tests/test_mcp_consent.py -v`
Expected: PASS, zero failures.

- [ ] **Step 4: Commit**

```bash
git add src/tests/test_mcp_server.py src/tests/test_mcp_consent.py
git commit -m "test: update tool-inventory counts for aletheore_list/_overview/_verify_citations

Three new always-on read-only tools shift every hardcoded tool-count
assertion up by 3."
```

---

### Task 9: Fix the website doc inconsistency, then verify the whole server end-to-end

**Files:**
- Modify: `website/developers.html:152-183`
- No test file — this task's verification is a real, live invocation of the built server, not a pytest run.

**Why (doc fix):** `website/developers.html:183` says "28 tools are always available" and lists `aletheore_managed_audit` (line 178) with no gated marker — both wrong. `aletheore_managed_audit` requires `EFFECT_EXTERNAL` (`mcp_server.py:595`), off by default, exactly like `aletheore_answer` which the page already marks with an `optional` class and a title tooltip. `README.md` and `website/llms.txt` already state the correct default count. After Task 5-7, the default-posture count is also higher than what any doc currently says.

**Why (end-to-end verification):** This whole plan started because 26 of 27 tools were confirmed dead on this exact machine (`air.json` written by `aletheore_version='0.2.0'`, incompatible with the current build). Every fix above was verified only by mocked unit tests. Before calling this done, run the real server against this repo's real, freshly-regenerated evidence and call a representative sample of tools for real — including the three brand-new ones and the two tools this plan changed the error paths of.

- [ ] **Step 1: Fix `website/developers.html`**

Replace lines 152-181 (the `<span>` list) by inserting the three new tool names in the same style as the existing list, and mark `aletheore_managed_audit` as gated to match `aletheore_answer*`'s existing pattern:

```html
        <div class="mcp-tool-list">
          <span>aletheore_scan</span>
          <span>aletheore_imports</span>
          <span>aletheore_imported_by</span>
          <span>aletheore_symbols</span>
          <span>aletheore_branch</span>
          <span>aletheore_ownership</span>
          <span>aletheore_secrets</span>
          <span>aletheore_vulnerabilities</span>
          <span>aletheore_licenses</span>
          <span>aletheore_endpoints</span>
          <span>aletheore_cluster</span>
          <span>aletheore_layer_violations</span>
          <span>aletheore_dead_code</span>
          <span>aletheore_hotspots</span>
          <span>aletheore_database</span>
          <span>aletheore_infrastructure</span>
          <span>aletheore_environment_variables</span>
          <span>aletheore_changes</span>
          <span>aletheore_neighborhood</span>
          <span>aletheore_list</span>
          <span>aletheore_overview</span>
          <span>aletheore_verify_citations</span>
          <span>aletheore_search</span>
          <span>aletheore_symbol_source</span>
          <span>aletheore_find_evidence_for_endpoint</span>
          <span>aletheore_find_evidence_for_symbol</span>
          <span>aletheore_find_evidence_for_dependency</span>
          <span>aletheore_healthcheck</span>
          <span>aletheore_index</span>
          <span>aletheore_search_codebase</span>
          <span class="optional" title="Only registered when the MCP server is started with --agent">aletheore_answer*</span>
          <span class="optional" title="Requires ALETHEORE_MCP_ALLOW to include 'external' - off by default">aletheore_managed_audit*</span>
        </div>
        <p class="mcp-tool-note">30 tools are available by default. <code>aletheore_answer</code>* is registered only when the server is started with <code>--agent</code>; <code>aletheore_managed_audit</code>* requires explicit consent to transmit evidence externally (<code>ALETHEORE_MCP_ALLOW=...,external</code>) and is off by default.</p>
```

- [ ] **Step 2: Check `README.md` and `website/llms.txt` for the same stale counts**

Run: `grep -n "27\|28\b" README.md website/llms.txt 2>/dev/null`

For any hit describing the MCP tool count, update it to match: 30 tools by default (23 original read-only + 3 new read-only + `aletheore_scan`/`_healthcheck`/`_index`/`_search_codebase` = the same effectful set as before, all still on by default), 31 with `external` allowed, 32 with `--agent` too.

- [ ] **Step 3: Real end-to-end verification — regenerate evidence and hand-invoke the server**

```bash
cd /path/to/this/worktree
python3 -m pip install -e src   # if not already installed in this environment
python3 -m aletheore.cli scan .   # or: aletheore scan . — regenerates .aletheore/air.json at the current schema version
```

Expected: completes without error; `.aletheore/air.json`'s `aletheore_version` now matches the current build (check with `python3 -c "import json; print(json.load(open('.aletheore/air.json'))['aletheore_version'])"` against `src/aletheore/evidence.py`'s `EVIDENCE_VERSION`).

Then, from a Python REPL or a throwaway script in this worktree, build the server directly and call a representative sample of tools for real — not mocked:

```python
import asyncio
from pathlib import Path
from aletheore.mcp_server import build_server

async def main():
    server = build_server(Path("."), allow=frozenset({"write", "network", "external"}))
    tools = await server.list_tools()
    print(f"{len(tools)} tools registered")
    assert len(tools) == 31  # or 32 if --agent-equivalent answer_adapter is also passed

    for name, args in [
        ("aletheore_overview", {}),
        ("aletheore_list", {"kind": "modules"}),
        ("aletheore_changes", {}),
        ("aletheore_search", {"pattern": "def build_server"}),
        ("aletheore_verify_citations", {"report_text": "See `src/aletheore/mcp_server.py:1` for the top of the file."}),
    ]:
        result = await server.call_tool(name, args)
        print(f"--- {name} ---")
        print(result.content[0].text[:500])

asyncio.run(main())
```

Expected: every call returns real, non-error TOON output (`aletheore_overview` shows this repo's actual language/module counts; `aletheore_list` with `kind=modules` returns real file paths from this repo; `aletheore_search` finds the real `build_server` definition; `aletheore_verify_citations` reports the citation verified since `mcp_server.py` line 1 is real). None of these being possible on this exact machine before Task 9 (everything failed with the version-incompatibility error) is the actual proof this plan is done, not just that the mocked unit tests pass.

- [ ] **Step 4: Run the complete test suite one final time**

Run: `cd src && python3 -m pytest tests/test_mcp_server.py tests/test_mcp_consent.py tests/test_query.py tests/test_search_index.py -v`
Expected: PASS, zero failures, zero skips beyond any pre-existing ones unrelated to this plan.

- [ ] **Step 5: Commit**

```bash
git add website/developers.html README.md website/llms.txt
git commit -m "docs: fix stale MCP tool count and missing gated-marker for managed_audit

website/developers.html said '28 tools are always available' and listed
aletheore_managed_audit with no gated indicator, though it requires
explicit external-transmission consent and is off by default - same
treatment aletheore_answer already gets. Also brings the count current
after Tasks 5-7 added three new default-on tools."
```

---

## Self-Review

**Spec coverage:** All 5 bugs (consent gap, dimension mismatch, changes version-check, search ignored_paths, doc inconsistency) → Tasks 1-4, 9. All 3 gaps (list, overview, verify) → Tasks 5-7. Task 8 keeps the test suite internally consistent after 5-7. Task 9's Step 3 is the actual "retest it" the user asked for — real invocation, not just mocks, specifically because the live-broken-tools discovery is what started this plan.

**Placeholder scan:** No TBD/TODO. Every step has real code, real file:line grounding verified directly against this repo's current source (not the audit transcript alone — re-read live during plan-writing), and real assertions.

**Type consistency:** `allow_hosted: bool` threads unchanged through `_embed_in_batches` → `_embed_stale_by_hash` → `build_index` → `_register_index_tool`. `IndexDimensionMismatchError` is defined once (Task 2) and imported once (`mcp_server.py`), never redefined. `find_repo_overview`/`list_modules`/`list_clusters`/`list_branches` signatures in Tasks 5-6 match exactly what Task 5/6's registration functions call them with (`func(evidence)`, no extra args).
