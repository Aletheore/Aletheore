# Aletheore Deterministic Evidence Enrichment Design

**Status:** Draft, pending review
**Date:** 2026-07-17

## Problem

A competitive gap analysis against RepoWise (a comparable MCP-based codebase-intelligence
tool) surfaced three real capabilities Aletheore doesn't have yet, all of which turn out to be
small, natural extensions of infrastructure Aletheore already built for other reasons — not new
subsystems:

1. **Exact symbol locations.** `repository.modules[].symbols.functions`/`classes` currently
   stores plain name strings. An agent that wants to actually read a specific function has to
   fall back to a raw file `Read` plus manual offset math — slower, more token-expensive, and
   error-prone compared to a tool that returns exactly the right lines.
2. **Dead code.** Aletheore already computes the full `imports`/`imported_by` graph for every
   module (used today for architecture clustering and layer-violation detection) but never
   surfaces the obvious question that graph can already answer: which modules does nothing
   import, and which declared dependencies does nothing in the repo actually use?
3. **Churn/hotspot signal.** `git_intel/analyzer.py` already parses full git log history for
   ownership and commit cadence, but never surfaces per-file churn or which files tend to
   change together — a well-established, cheap, deterministic risk signal (files that change
   together but live far apart in the dependency graph are a classic coupling smell).

All three are pure deterministic analysis over data Aletheore already parses (tree-sitter ASTs,
the import graph, git log) — no LLM involvement, consistent with every other evidence block in
`evidence.json` today.

## Goals

- Add exact `start_line`/`end_line` to every function and class already captured in
  `repository.modules[].symbols`, plus a new on-demand query (`aletheore query symbol-source`
  / `aletheore_symbol_source` MCP tool) that reads the live file and returns the exact source
  text for one named symbol — no manual offset arithmetic required by the caller.
- Add a new `repository.dead_code` evidence block: modules nothing imports (excluding
  recognized entry points and test files), and declared dependencies nothing in the repo
  actually imports.
- Add a new `git.hotspots` evidence block: per-file commit churn, ranked, with each file's most
  frequent co-change partners (files that tend to change in the same commits) and its existing
  `dependents_count` (reusing `imported_by`).
- Every new block is queryable the same way every existing evidence block already is: a
  `QUERY_FUNCTIONS` entry, a CLI `aletheore query <kind>` subcommand, and an MCP tool — zero new
  patterns, just more entries in patterns that already exist.

## Non-Goals

- **No composite "risk score" or "health score."** Every existing evidence block in Aletheore
  reports raw, citable facts and lets the reader (human or LLM writing the `audit` report) draw
  conclusions — numeric scorecards were explicitly ruled out earlier in this project's history
  specifically because they require subjective judgment a deterministic scanner can't ground.
  `git.hotspots` reports raw churn counts and co-change counts, sorted, not an opaque blended
  score. This directly mirrors the reasoning that ruled out a RepoWise-style `get_health()`
  equivalent entirely (endpoint liveness is already binary and already shipped as
  `aletheore healthcheck` — nothing new needed there).
- **No fine-grained per-symbol unused-export detection.** True unused-export analysis is
  language-specific (Python has no formal export keyword; JS/TS `export`; Go capitalization;
  etc.) and fuzzy in ways that don't fit Aletheore's "cite the exact fact" bar. Scoped down to
  what's actually achievable deterministically: a module nothing imports has, by definition,
  none of its exports used — module-level unreachability already captures the useful case
  without inventing a new fuzzy per-symbol heuristic.
- **No semantic/NL search.** That's a separate, larger design (embeddings, a vector store, an
  LLM-backed Q&A layer) — see the companion spec,
  `2026-07-17-aletheore-semantic-search-design.md`. This spec is deliberately scoped to what's
  achievable with zero new dependencies.

## Architecture

### 1. Symbol line bounds

**Current shape** (`aletheore/scanner/graph.py`): every one of the 9 per-language extractor
functions (`_extract_python`, `_extract_javascript` — covers JS/JSX/TS/TSX — `_extract_go`,
`_extract_rust`, `_extract_java`, `_extract_ruby`, `_extract_php`, `_extract_c_family` — covers
C/C++/headers — `_extract_csharp`) walks the tree-sitter AST and appends a bare name string to a
`functions: list[str]` / `classes: list[str]` accumulator whenever it hits a
`function_definition`/`class_definition`-shaped node (exact node type name varies per
language's grammar). tree-sitter `Node` objects already expose `.start_point` and `.end_point`
as `(row, column)` tuples, 0-indexed — this data already exists in every extractor, it's just
never captured.

**New shape**: `functions`/`classes` become `list[dict]`, each
`{"name": str, "start_line": int, "end_line": int}` (1-indexed, inclusive — `start_line` is
`node.start_point[0] + 1`, `end_line` is `node.end_point[0] + 1`, where `node` is the enclosing
`function_definition`/`class_definition`-equivalent node, not just the name identifier node —
the goal is the whole symbol's span, not the line its name appears on).

This is a **breaking change to `evidence.json`'s schema** (`functions`/`classes` change from
`list[str]` to `list[dict]`). Justified here specifically because Aletheore has no external
users yet (per Arihant's own framing: "when we have no users isn't it good to take that
architecture decision") — this is the cheapest point this will ever be to make this change.
`aletheore/query.py`'s `find_symbols` needs no change (it returns the whole `symbols` dict
verbatim regardless of shape); anything downstream that assumed `functions`/`classes` were bare
strings (the `audit` command's `EVIDENCE_SCHEMA_MAP` documentation string, any test fixtures)
needs updating alongside.

**New on-demand tool**: `find_symbol_source(evidence, module_path, symbol_name)` in
`aletheore/query.py` — looks up the symbol's `start_line`/`end_line` in the module's `symbols`
entry (checking both `functions` and `classes`), then reads the *live* file (not evidence —
this is the one query that needs filesystem access, same category as `aletheore_search` which
already reads live files today) and returns the exact 1-indexed line range as text, plus the
resolved `start_line`/`end_line` so the caller doesn't have to trust its own arithmetic. Raises
a new `SymbolNotFoundInEvidenceError` if the name isn't in either list. This is a `requires_target`-style
query needing two arguments (module path + symbol name) rather than one — `QUERY_FUNCTIONS`'s
existing `(Callable, bool)` shape only encodes single-target-or-not, so this one is wired as its
own dedicated CLI subcommand and MCP tool (mirroring how `aletheore_neighborhood` and
`aletheore_search` are already hand-registered outside the generic `QUERY_FUNCTIONS`
auto-registration loop for the same reason — they need more than one argument).

### 2. Dead code

**New evidence block**: `repository.dead_code`:

```
{
  "unreachable_modules": [{"path": str, "reason": str}],
  "unused_dependencies": [{"ecosystem": str, "package": str}],
  "entry_points_detected": [str]
}
```

**Unreachable modules**: a module qualifies if `imported_by == []` AND it isn't a recognized
entry point AND it isn't a test file. Three exclusion rules, each necessary — without them this
block would be mostly noise:

- **Entry points**: filename patterns (`main.py`, `__main__.py`, `app.py`, `manage.py`,
  `wsgi.py`, `asgi.py`, `index.js`, `index.ts`, `index.jsx`, `index.tsx`) plus anything already
  parseable from build config Aletheore has already detected (`package.json`'s `"main"`/`"bin"`
  fields, `pyproject.toml`'s `[project.scripts]` targets) plus an explicit override in
  `.aletheore.json` (new key `dead_code_entry_points: list[str]`, following the exact same
  load/validate/default pattern `load_architecture_config` in `aletheore/architecture.py`
  already establishes for `layer_markers`/`cluster_resolution` — extends the same config file,
  not a new one).
- **Test files**: path matches `test_*.py`, `*_test.py`, `*.test.js`, `*.test.ts`, `*.spec.js`,
  `*.spec.ts`, or lives under a `tests/`, `test/`, or `__tests__/` directory component. Test
  files are *supposed* to have zero inbound imports from application code — flagging them as
  dead code would be actively wrong, not just noisy.
- Every excluded/included module gets an explicit reason so the block is self-explanatory
  without cross-referencing code: `"reason": "no other module imports this file"`.

**Unused dependencies**: cross-reference the repo's own declared dependencies (the same parsed
list `aletheore/licenses.py` and `aletheore/vulnerabilities.py` already build for their own
checks — the exact helper name needs confirming against that code at implementation time, not
guessed here) against the set of top-level package names actually imported anywhere in
`repository.modules[].imports`. A declared dependency whose package name never appears in any
module's import list is reported. Only computed for ecosystems Aletheore already has a declared-
dependency parser for (matches whatever `licenses.py`/`vulnerabilities.py` already support -
today that's Python/PyPI and JS/npm).

### 3. Hotspots (churn + co-change)

**New evidence block**: `git.hotspots`, a list ranked by `churn_count` descending, capped at the
top 30 files (keeps evidence size bounded on large repos, matches the existing pattern of
`security.secrets`/`vulnerabilities` findings lists not being unbounded):

```
{"path": str, "churn_count": int, "co_change_partners": [{"path": str, "co_occurrences": int}], "dependents_count": int}
```

**Churn**: a new function in `aletheore/git_intel/analyzer.py`, following the exact
`_run_git`/parse-stdout pattern every other function there already uses:
`git log --format=%H --name-only HEAD`, split on commit-hash lines, count file occurrences
across all commits.

**Co-change**: reuse the same per-commit file lists from the churn pass (one git log call
serves both, not two separate ones). For each commit, for every pair of files that both
appear in that commit's file list, increment a pairwise co-occurrence counter. **Scalability
guard, stated explicitly because it's a real risk, not a hypothetical one**: a single commit
touching many files (a mass reformat, a dependency lockfile regeneration, a rename sweep)
produces a combinatorial number of pairs — a commit touching 500 files produces ~125,000 pairs
from that commit alone. Commits touching more than a fixed threshold (50 files) are excluded
from co-change counting entirely (they still count toward each file's own churn) rather than
silently producing a slow scan or a co-change table dominated by one mechanical commit's noise.
Report each file's top 5 co-change partners by count, not the full pairwise table.

**Dependents count**: `len(module["imported_by"])`, already computed, just surfaced alongside
the git-derived numbers so a caller gets coupling + churn together in one place instead of two
separate lookups.

## Wiring (same shape as every existing evidence block)

- `scan_repository()` in `aletheore/evidence.py` gains two more report-progress phases
  ("Detecting dead code", "Computing hotspots") and two more result assignments into the
  returned dict, following the exact structure every existing phase already uses.
- `aletheore/query.py`: `find_dead_code`, `find_hotspots`, `find_symbol_source` join
  `QUERY_FUNCTIONS` (the first two; `find_symbol_source` is hand-registered per the two-argument
  reasoning above) - same as every existing query kind.
- `aletheore/cli.py`: new `QUERY_KIND_CHOICES` entries, no new subcommand structure.
- `aletheore/mcp_server.py`: `dead-code`/`hotspots` ride the existing auto-registration loop via
  `_TOOL_NAME_TO_QUERY_KIND`; `symbol-source` gets its own hand-written registration function
  mirroring `_register_neighborhood_tool`/`_register_search_tool`.
- `audit`'s `EVIDENCE_SCHEMA_MAP` (in `aletheore/adapters/openai_compatible.py`) gets three new
  lines documenting the new fields, and the `symbols[].functions`/`classes` line gets corrected
  to reflect the new shape.

## Testing Strategy

- Unit tests per new extractor behavior (real tree-sitter parses of small fixture snippets,
  asserting exact `start_line`/`end_line` against hand-counted line numbers in the fixture -
  same style every existing extractor test already uses).
- Dead-code: fixture repos exercising each exclusion rule independently (an entry point that
  would otherwise look dead, a test file that would otherwise look dead, a genuinely dead
  module, an `.aletheore.json` override), plus a fixture with a declared-but-unimported
  dependency.
- Hotspots: a real git fixture repo (commits constructed via real `git commit`, not mocked) with
  a known churn distribution and a known co-changing file pair, plus a fixture exercising the
  50-file-commit exclusion guard.
- Real verification (not just unit tests, matching this project's standing discipline): run a
  real scan against Aletheore's own repository (real tree-sitter parses across all the language
  extractors that apply, real git history) and manually spot-check a handful of
  `start_line`/`end_line` values against the actual file, and sanity-check the hotspots list
  against `git log --stat` output for the same repo.

## Success Criteria

1. `aletheore scan` on a real repo produces `start_line`/`end_line` for every function/class
   across every supported language, verified by spot-check against the real file.
2. `aletheore query symbol-source <module> <name>` returns exactly the right source text for a
   real symbol in a real repo, with no off-by-one errors.
3. `repository.dead_code.unreachable_modules` on a real repo excludes every real entry point and
   every real test file, and includes at least one genuinely-dead fixture module in a
   purpose-built test repo.
4. `git.hotspots` on a real repo with known history ranks files by churn correctly and surfaces
   at least one real co-change pair, with the 50-file-commit guard verified not to blow up
   runtime on a synthetic large commit.
5. All three blocks are reachable via CLI (`aletheore query <kind>`) and MCP tool, TOON-encoded
   like every other query result.
6. Zero new dependencies added.
