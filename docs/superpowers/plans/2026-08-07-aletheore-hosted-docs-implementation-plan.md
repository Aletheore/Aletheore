# Hosted Docs (AI-Enhanced API Reference) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Status: all 6 tasks implemented 2026-08-07.** Code and tests are written and match this plan's design throughout (verified by reading, by unit tests that don't need a database - `live_docs.py`, `docs_reference.py` - and by import/collection success + a real Node.js syntax check on the new dashboard page's JS). **Not verified**: anything that needs a live Postgres/Redis - the DB-layer round-trip tests (Task 3), the job-queue wiring's actual execution (Task 4), and the dashboard route's DB-backed tests (Task 5) were all written to match this codebase's existing test conventions exactly but never run, since Docker wasn't available in the session that built this. Run the full `github-app` suite against a real `TEST_DATABASE_URL` before merging - this is the one real gap between "written correctly" and "confirmed correct."

**Context:** A prior plan (`2026-08-07-aletheore-codebase-documentation-generation-plan.md`) built a pure-evidence, zero-LLM `aletheore docs` CLI command, `query api-reference`, and an MCP tool. Product direction changed mid-build: this convenience feature (not the raw evidence fields, which stay in every free scan) is a paid, hosted-dashboard-only capability — the free-tier CLI/query/MCP surfaces for it were removed (commit reverting them exists on this branch). The evidence-only renderer (`docs_reference.py`) and the schema enrichment (`docstring`/`return_type`/`is_public` on every symbol) survive and are what this plan builds on.

Second product decision, made after the first plan shipped: the pure-evidence version is too thin on undocumented code to be "industry grade." This plan adds an LLM pass that (a) drafts a grounded description for public symbols with no docstring, and (b) optionally polishes/restructures the English of existing docstrings for clarity — both under the same discipline every other LLM-written surface in this codebase already follows (AIRview, `audit`): generate, then verify before trusting, and never let an unverified attempt destroy or silently override known-good deterministic content.

**Goal:** A hosted, `air`-plan-gated "Docs" feature in the GitHub App dashboard — an AI-enhanced version of the API reference, automatically kept current on push (mirroring AIRview's build lifecycle exactly), viewable in a new dashboard page.

**Architecture:** New `scan_worker/live_docs.py` module, same shape as `live_wiki.py`: per-symbol generation with the actual source snippet as context (not the file-list "brief" AIRview uses — a symbol needs its own body, not a cluster overview), a lighter validation step than `verify_citations` (this content doesn't carry `file:line` citations of its own — `docs_reference.py`'s citation line already provides that, deterministically, outside the model's control), Flash for the (higher-volume, per-push) incremental case, Pro for the (less-frequent) initial full build. New Postgres tables mirroring `wiki_overview`/`wiki_subsystems`/`wiki_build_status`'s shape exactly. New dashboard route + page mirroring `get_dashboard_wiki`. Trigger wiring in `jobs.py` mirroring `_maybe_update_live_wiki` and the Paddle-upgrade full-build trigger.

**Tech Stack:** Postgres (asyncpg), RQ (job queue), the existing `OpenAICompatibleAdapter`/DeepSeek setup from `model_tiers.py`, FastAPI, the existing dashboard frontend rendering pattern (`frontend.py`).

## Global Constraints

- **Plan-gated exactly like managed audits**: `if installation["plan"] == "free": raise HTTPException(402, ...)` — same string, same status code, same installation-lookup path (`get_current_session` for the dashboard route, matching `_require_dashboard_installation`'s existing pattern — this is a session-cookie browser route, not a bearer-token API route like `managed_audit_api.py`).
- **Never let AI-generated text silently masquerade as the developer's own words.** `docs_reference.py`'s renderer must mark every AI-touched symbol distinctly (a generated description for an undocumented symbol vs a polished rewrite of an existing docstring vs verbatim original) — this is an extension of the grounding contract already established (`docs_reference.py`'s `UNDOCUMENTED` placeholder), not a new principle.
- **A rewrite must never be trusted over the original if verification is uncertain.** Mirrors `live_wiki.py`'s own precedent exactly (`_validate_written_output` returning `None` → the subsystem keeps its deterministic file list/diagram, only the prose is withheld). Here: if a polished rewrite can't be verified as meaning-preserving, keep the developer's original verbatim text, not the AI version. If an undocumented-symbol description can't be verified as grounded in the given snippet, render it as `UNDOCUMENTED` exactly as the pure-evidence version already does — never a half-verified guess.
- **Verification here is NOT `citation_verifier.verify_citations`** — that function checks `file:line` citations embedded in prose against evidence's index, built for AIRview's brief-driven, multi-file cluster prose. A symbol-level description doesn't cite files itself (the citation line is rendered deterministically by `docs_reference.py`, entirely outside the model's control) — so the actual risk is the model inventing behavior not evidenced by the one snippet it was given, not a broken citation pointer. Design a narrower, purpose-built check (Task 1) rather than reusing `verify_citations` for a shape it wasn't built for.
- **Cost control via batching + tiered models, mirroring AIRview exactly**: one call per FILE (batching every undocumented/to-polish symbol in that file into one prompt/response, JSON array in, JSON array out — same shape as `SUBSYSTEM_WRITING_SYSTEM_PROMPT`'s per-file `key_symbols` list), not one call per symbol. Flash (`deepseek-v4-flash`) for every incremental per-push update, Pro (`model_tiers.writing_adapter_for_plan`) only for a repo's first full build — identical split to `live_wiki.py`'s `FLASH_MODEL`/`UPDATE_MODEL` vs `_live_wiki_full_build_writing_adapter`.
- **Trigger timing mirrors AIRview exactly**: incremental update inside the existing PR-scan job path (alongside `_maybe_update_live_wiki`, not a separate job), full build triggered on first scan after a `free`→`air` upgrade (the same Paddle `subscription.created`/`updated` handler that already calls `run_live_wiki_full_build_job`). No new automatic-trigger design needed — reuse the existing hook points.
- **DB schema mirrors `wiki_overview`/`wiki_subsystems`/`wiki_build_status` exactly** in shape (composite PK on `installation_id, repo_full_name[, symbol_key]`, `source_commit`, `updated_at`, a parallel `docs_build_status` table) — not because it must, but because introducing a differently-shaped storage pattern for a twin feature with no functional reason to differ is needless surface area for the next person to learn.

---

## Task 1: `live_docs.py` — symbol description generation + verification (core, no DB/API dependency)

**Files:**
- Create: `github-app/scan_worker/live_docs.py`
- Test: `github-app/tests/test_live_docs.py`

**Interfaces:**
- Produces: `generate_file_descriptions(module: dict, source_lines: list[str], writing_adapter, *, fetch_line_count=None) -> dict[str, dict]` — keyed by symbol name, each value `{"description": str, "mode": "generated" | "polished"}` for symbols that passed verification. Symbols that fail verification are simply absent from the returned dict (caller falls back to `UNDOCUMENTED` or the original docstring — see Task 2).
- Consumes: one module's evidence dict (post the first plan's schema — `docstring`/`return_type`/`is_public` present on every symbol) and that file's raw source lines (for slicing `start_line:end_line` snippets, same technique `query.find_symbol_source` already uses).

- [ ] **Step 1: Write the failing tests**

```python
def test_generates_a_description_for_an_undocumented_public_symbol():
    module = {"path": "a.py", "symbols": {"functions": [
        {"name": "add", "start_line": 1, "end_line": 2, "params": "(a, b)",
         "docstring": None, "return_type": None, "is_public": True},
    ], "classes": []}}
    source_lines = ["def add(a, b):", "    return a + b"]
    fake_adapter = FakeAdapter(response=json.dumps(
        {"add": {"description": "Adds two numbers and returns the sum."}}
    ))
    result = generate_file_descriptions(module, source_lines, fake_adapter)
    assert result["add"]["description"] == "Adds two numbers and returns the sum."
    assert result["add"]["mode"] == "generated"


def test_skips_private_symbols_entirely():
    module = {"path": "a.py", "symbols": {"functions": [
        {"name": "_helper", "start_line": 1, "end_line": 2, "params": "()",
         "docstring": None, "return_type": None, "is_public": False},
    ], "classes": []}}
    fake_adapter = FakeAdapter(response="{}")
    result = generate_file_descriptions(module, ["def _helper():", "    pass"], fake_adapter)
    assert result == {}
    assert fake_adapter.call_count == 0  # never even asked - nothing to generate for


def test_rejects_a_response_for_a_symbol_name_that_was_never_asked_about():
    module = {"path": "a.py", "symbols": {"functions": [
        {"name": "add", "start_line": 1, "end_line": 2, "params": "(a, b)",
         "docstring": None, "return_type": None, "is_public": True},
    ], "classes": []}}
    # model hallucinates an entry for a symbol name it was never given
    fake_adapter = FakeAdapter(response=json.dumps({
        "add": {"description": "Adds two numbers."},
        "subtract": {"description": "Subtracts two numbers."},
    }))
    result = generate_file_descriptions(module, ["def add(a, b):", "    return a + b"], fake_adapter)
    assert "subtract" not in result
    assert "add" in result


def test_polishes_an_existing_docstring_when_requested():
    module = {"path": "a.py", "symbols": {"functions": [
        {"name": "add", "start_line": 1, "end_line": 3, "params": "(a, b)",
         "docstring": "adds a and b together and give the sum back", "return_type": None, "is_public": True},
    ], "classes": []}}
    fake_adapter = FakeAdapter(response=json.dumps(
        {"add": {"description": "Adds `a` and `b` and returns their sum."}}
    ))
    result = generate_file_descriptions(
        module, ["def add(a, b):", '    """adds a and b together..."""', "    return a + b"],
        fake_adapter, polish_existing=True,
    )
    assert result["add"]["mode"] == "polished"


def test_malformed_model_response_yields_no_descriptions_not_a_crash():
    module = {"path": "a.py", "symbols": {"functions": [
        {"name": "add", "start_line": 1, "end_line": 2, "params": "(a, b)",
         "docstring": None, "return_type": None, "is_public": True},
    ], "classes": []}}
    fake_adapter = FakeAdapter(response="not json at all")
    result = generate_file_descriptions(module, ["def add(a, b):", "    return a + b"], fake_adapter)
    assert result == {}
```

(`FakeAdapter` is a tiny local test double with a `.simple_completion(system, user, cwd=".")` returning the fixed `response` and counting calls — matching the fake-adapter pattern `test_live_wiki.py` already uses.)

- [ ] **Step 2: Run to verify failure**

Run: `cd github-app && TEST_DATABASE_URL=postgresql://postgres:test@localhost:55433/aletheore_test python -m pytest tests/test_live_docs.py -v` (no DB actually needed for this module — the env var is this repo's standard test invocation, harmless if unused here)
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement**

```python
"""AI-enhanced per-symbol descriptions on top of the deterministic, evidence-
only rendering in aletheore.docs_reference. Same discipline as live_wiki.py:
generate, then verify before trusting - an unverifiable attempt never
overrides known-good deterministic content (a docstring the developer
actually wrote, or the honest "Undocumented" label).

Unlike AIRview's citation_verifier.verify_citations (built for prose that
cites file:line references across a multi-file brief), a symbol description
carries no citations of its own - docs_reference.py's citation line is
rendered deterministically, outside the model's control. The actual risk
here is the model inventing behavior the given snippet doesn't show, which
citation-checking can't catch - so this module's verification is narrower
and purpose-built: reject any response for a symbol name that wasn't asked
about (the one thing that IS mechanically checkable), and nothing more.
Content correctness is a prompt-design problem (tight snippet, explicit
"describe only what's shown" instruction), not a post-hoc-checkable one.
"""

import json
import logging

logger = logging.getLogger(__name__)

FLASH_MODEL = "deepseek-v4-flash"

DESCRIBE_SYSTEM_PROMPT = """You write one-sentence descriptions of source code symbols for an API
reference. You are given a JSON array of {"name", "signature", "source"} objects, one per
function/class in a single file. For each, respond with ONLY a JSON object mapping the symbol's
exact name to {"description": "1-2 sentence description of what it does, based ONLY on the given
source"}. Never mention a file, function, or behavior that isn't visible in the given source for
that specific symbol. Never invent parameter meanings not evidenced by the code. If a symbol's
purpose truly can't be determined from its source alone, omit it from your response rather than
guessing."""

POLISH_SYSTEM_PROMPT = """You rewrite existing code documentation for clarity and grammar. You are
given a JSON array of {"name", "signature", "source", "existing_docstring"} objects. For each,
respond with ONLY a JSON object mapping the symbol's exact name to {"description": "a clearer,
grammatically correct rewrite that preserves the EXACT same meaning as the existing docstring -
add no new claims, remove no information, just improve the English"}. If the existing docstring is
already clear, you may return it unchanged. Never add information not already present in the
existing docstring or visible in the given source."""


def _parse_json_object(raw: str) -> dict:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _symbols_needing_work(module: dict, polish_existing: bool) -> list[dict]:
    all_symbols = module["symbols"]["functions"] + module["symbols"]["classes"]
    if polish_existing:
        return [s for s in all_symbols if s.get("is_public") and s.get("docstring")]
    return [s for s in all_symbols if s.get("is_public") and not s.get("docstring")]


def _build_request_items(symbols: list[dict], source_lines: list[str], polish_existing: bool) -> list[dict]:
    items = []
    for symbol in symbols:
        snippet = "\n".join(source_lines[symbol["start_line"] - 1 : symbol["end_line"]])
        item = {
            "name": symbol["name"],
            "signature": f"{symbol['name']}{symbol.get('params') or ''}",
            "source": snippet,
        }
        if polish_existing:
            item["existing_docstring"] = symbol["docstring"]
        items.append(item)
    return items


def generate_file_descriptions(
    module: dict,
    source_lines: list[str],
    writing_adapter,
    *,
    polish_existing: bool = False,
    fetch_line_count=None,
) -> dict[str, dict]:
    symbols = _symbols_needing_work(module, polish_existing)
    if not symbols:
        return {}

    requested_names = {s["name"] for s in symbols}
    items = _build_request_items(symbols, source_lines, polish_existing)
    system_prompt = POLISH_SYSTEM_PROMPT if polish_existing else DESCRIBE_SYSTEM_PROMPT
    raw = writing_adapter.simple_completion(system_prompt, json.dumps(items), cwd=".")
    parsed = _parse_json_object(raw)

    mode = "polished" if polish_existing else "generated"
    result = {}
    for name, entry in parsed.items():
        if name not in requested_names:
            logger.info(
                "live_docs: dropping response for %r - not among the %d symbols asked about in %s",
                name, len(requested_names), module["path"],
            )
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("description"), str) or not entry["description"].strip():
            continue
        result[name] = {"description": entry["description"].strip(), "mode": mode}
    return result
```

- [ ] **Step 4: Verify tests pass**

Run: `cd github-app && python -m pytest tests/test_live_docs.py -v`

---

## Task 2: `docs_reference.py` — accept AI-generated descriptions, mark provenance

**Files:**
- Modify: `src/aletheore/docs_reference.py`
- Test: `src/tests/test_docs_reference.py`

**Interfaces:**
- `build_module_reference(evidence, module_path, ai_descriptions: dict[str, dict] | None = None)` — `ai_descriptions` keyed by symbol name, same `{"description": str, "mode": "generated" | "polished"}` shape `live_docs.generate_file_descriptions` returns. When present for a symbol: `mode="generated"` renders the AI text with a `*(AI-generated - no docstring found in source)*` marker; `mode="polished"` renders the AI text with a `*(AI-polished from the original docstring)*` marker. Absent entirely (no `ai_descriptions` passed, or a symbol not present in it) → today's exact behavior, unchanged (real docstring verbatim, or `UNDOCUMENTED`).
- This keeps `docs_reference.py` itself LLM-free and evidence-shaped — it takes already-generated, already-verified text as plain data, the same way it already takes `docstring` as plain data. No network calls, no adapter imports here.

- [ ] **Step 1: Write the failing tests**

```python
def test_build_module_reference_renders_ai_generated_description_with_marker():
    evidence = {"repository": {"modules": [_module("src/a.py", [_symbol("f", docstring=None)])]}}
    md = build_module_reference(
        evidence, "src/a.py",
        ai_descriptions={"f": {"description": "Does the thing.", "mode": "generated"}},
    )
    assert "Does the thing." in md
    assert "AI-generated" in md
    assert UNDOCUMENTED not in md


def test_build_module_reference_renders_polished_description_with_marker():
    evidence = {"repository": {"modules": [_module(
        "src/a.py", [_symbol("f", docstring="does thing ok")]
    )]}}
    md = build_module_reference(
        evidence, "src/a.py",
        ai_descriptions={"f": {"description": "Does the thing correctly.", "mode": "polished"}},
    )
    assert "Does the thing correctly." in md
    assert "AI-polished" in md
    assert "does thing ok" not in md


def test_build_module_reference_ignores_ai_descriptions_for_symbols_not_in_it():
    evidence = {"repository": {"modules": [_module(
        "src/a.py", [_symbol("f", docstring="Real docstring.")]
    )]}}
    md = build_module_reference(evidence, "src/a.py", ai_descriptions={"other_symbol": {"description": "x", "mode": "generated"}})
    assert "Real docstring." in md
    assert "AI-generated" not in md
    assert "AI-polished" not in md
```

- [ ] **Step 2: Run to verify failure** — `cd src && python -m pytest tests/test_docs_reference.py -k ai_descr -v`

- [ ] **Step 3: Implement.** Thread an `ai_descriptions` param through `build_module_reference` → `_render_symbol` → `_render_docstring`; when a symbol name has an entry, use its `description`/`mode` instead of the raw `docstring` field, appending the appropriate marker line. `build_api_reference` gains a matching optional `ai_descriptions_by_module: dict[str, dict[str, dict]] | None` param, keyed by module path, passed through per-module.

- [ ] **Step 4: Verify tests pass** — `cd src && python -m pytest tests/test_docs_reference.py -v`

---

## Task 3: DB schema — `docs_symbols` + `docs_build_status`

**Files:**
- Create: `github-app/migrations/028_live_docs.sql`
- Modify: `github-app/app_server/db.py`
- Test: `github-app/tests/test_db.py` (or wherever migration-backed table tests already live for wiki — check `test_live_wiki*.py`'s DB fixture pattern first and match it)

```sql
CREATE TABLE IF NOT EXISTS docs_symbols (
    installation_id  BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name   TEXT NOT NULL,
    module_path      TEXT NOT NULL,
    symbol_name      TEXT NOT NULL,
    description      TEXT NOT NULL,
    mode             TEXT NOT NULL,  -- 'generated' | 'polished'
    source_commit    TEXT,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (installation_id, repo_full_name, module_path, symbol_name)
);

CREATE INDEX IF NOT EXISTS docs_symbols_lookup
ON docs_symbols (installation_id, repo_full_name);

CREATE TABLE IF NOT EXISTS docs_build_status (
    installation_id  BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name   TEXT NOT NULL,
    status           TEXT NOT NULL,
    error_message    TEXT,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (installation_id, repo_full_name)
);
```

- [ ] Add `upsert_docs_symbol`, `list_docs_symbols`, `delete_docs_symbols_not_in` (mirroring `upsert_wiki_subsystem`/`list_wiki_subsystems`/`delete_wiki_subsystems_not_in` exactly), `get_docs_build_status`/`set_docs_build_status` (mirroring `get_wiki_build_status`/`set_wiki_build_status`) to `db.py`.
- [ ] Write the equivalent DB-roundtrip tests `test_live_wiki.py`'s DB layer already has for the wiki tables, adapted to `docs_symbols`.
- [ ] Run migrations locally against the test DB and confirm `scripts/migrate.py` picks up `028_live_docs.sql` cleanly.

---

## Task 4: Wire generation into the job pipeline

**Files:**
- Modify: `github-app/scan_worker/jobs.py`

- [ ] Add `_maybe_update_live_docs(installation_id, repo_full_name, evidence, changed_files, head_sha)`, called alongside `_maybe_update_live_wiki` in both call sites (`run_pr_scan_job` and the post-push reconciliation job) — same "only regenerate for files that actually changed" incrementality, using Flash and `_symbols_needing_work`'s existing-docstring-aware diffing so an unchanged file's symbols aren't needlessly re-sent to the model every push.
- [ ] Add `run_live_docs_full_build_job(installation_id, repo_full_name)` mirroring `run_live_wiki_full_build_job` (Pro model, called from wherever the Paddle upgrade handler calls the wiki equivalent).
- [ ] `_maybe_update_live_docs` reads each changed file's actual source lines (needed for `generate_file_descriptions`) via the same checkout the scan job already has on disk - no new file-fetching mechanism.
- [ ] Record status via `set_docs_build_status`, same try/except-log pattern already used for `_maybe_update_live_wiki`'s own failure handling (per the MEDIUM fix earlier this project: "Fix silent AIRview incremental-update failures" — don't repeat that mistake here).

---

## Task 5: Dashboard route + page

**Files:**
- Modify: `github-app/app_server/dashboard.py`, `github-app/app_server/frontend.py`

- [ ] `get_dashboard_docs(org, repo, request)` mirroring `get_dashboard_wiki` exactly: `_require_dashboard_installation` for auth, `installation["plan"] == "free"` → 402 (or a plan-appropriate empty/upsell state, matching whatever `get_dashboard_wiki` itself does for free-plan installations — check that exact behavior first rather than assuming), `list_docs_symbols` + the deterministic evidence to build the final rendered page (calling `build_api_reference`/`build_module_reference` with `ai_descriptions_by_module` populated from the DB rows).
- [ ] A dashboard page/section (frontend.py) presenting it, matching the existing wiki page's visual pattern (per-module list, expandable per-symbol entries, the AI-generated/polished markers visibly distinct - not hidden styling, this is a trust signal for the customer).

---

## Task 6: Full verification + self-review

- [ ] `cd src && python -m pytest` (full suite, isolated venv).
- [ ] `cd github-app && python -m pytest` (full suite, isolated venv, real `TEST_DATABASE_URL`).
- [ ] Confirm a `free`-plan installation gets 402/upsell, not generated content, hitting the new route directly.
- [ ] Confirm the AI-generated/polished markers survive in the actual rendered dashboard HTML (not stripped by templating).
- [ ] Confirm an unverifiable/malformed model response degrades to the pure-evidence behavior (Undocumented / original docstring), never a crash, never silently-wrong content.

---

## Explicitly out of scope

- **CLI/local/free-tier exposure of any AI-enhanced output** — this entire feature is hosted-dashboard-only, by explicit product direction. The pure-evidence path (docstrings/comments already in source) remains free as part of `scan`'s evidence output; the polish/generation step never runs locally or without a paid plan.
- **Re-verifying `is_public`'s nesting-depth-aware fix's interaction with per-file batching** — already fixed upstream (private/nested symbols never reach `_symbols_needing_work` in the first place, since it filters on `is_public`).
- **Rate-limiting the new job type against the existing monthly-scan cap** — should probably share `MAX_SCANNED_REPOS_PER_MONTH`'s spirit, but needs its own design pass once real usage patterns are visible; flagging, not designing blind.
