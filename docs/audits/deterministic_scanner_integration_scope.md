# Scoping: integrating SonarQube / Semgrep / Bearer into Aletheore

Follows `deterministic_scanner_evaluation.md` (all three tools verified live,
working, real). This is the concrete engineering scope for actually wiring
them into `aletheore scan` (→ AIRview, MCP) and Flash Review (PR review).
Grounded in the real code, not a generic plan — every file/function named
below exists today and was read directly, not assumed.

## The three existing integration points (all real, already there)

1. **Evidence schema + orchestration** (`src/aletheore/air_schema.py`,
   `src/aletheore/evidence.py::scan_repository`). Every existing external
   check (secrets, OSV.dev dependency vulnerabilities, dependency licenses)
   follows the identical shape: a dedicated module
   (`aletheore/secrets.py`, `aletheore/vulnerabilities.py`,
   `aletheore/licenses.py`) exposing a `check_X(repo_path) -> dict`
   function, called from `scan_repository()` behind a `check_X: bool` flag,
   `{"checked": False, "reason": "skipped (--no-check-X)", "findings": []}`
   on skip, merged into the returned evidence dict under `security.X`. New
   scanners follow this exact pattern — no new orchestration mechanism to
   invent.
2. **MCP tools** (`src/aletheore/mcp_server.py::_register_query_wrapper_tools`).
   Fully data-driven: a tool name maps to a `kind`, `kind` maps to a
   `(func, requires_target)` pair in `QUERY_FUNCTIONS`, `func(evidence,
   target)` reads a slice of the evidence dict. Adding
   `aletheore_static_analysis` is a query-function + two dict-entry
   registration, not new plumbing.
3. **PR review merge** (`github-app/scan_worker/flash_review.py`,
   `find_semantic_regressions` from `semantic_checks.py`). Deterministic,
   pre-computed findings get merged with LLM findings via
   `_merge_semantic_findings` inside `review_diff()`, tagged
   `source: "semantic"` vs `source: "llm"` so downstream code (verification
   gating, caching) can treat them differently. Same merge point for
   scanner-sourced findings.

## Schema addition

`air_schema.py`, new sibling to `security.secrets`/`dependency_vulnerabilities`:

```python
_STATIC_ANALYSIS_FINDING = _obj({
    "tool": _STR,          # "sonarqube" | "semgrep" | "bearer"
    "rule_id": _STR,
    "severity": _STR,      # normalized: "blocker"|"critical"|"major"|"minor"|"info"
    "type": _STR,          # "bug" | "vulnerability" | "code_smell" | "privacy"
    "path": _STR,
    "line": _INT,
    "message": _STR,
})
# security.static_analysis
"static_analysis": _obj({
    "tools_run": _ANY_LIST,          # which of the 3 actually ran
    "findings": _arr(_STATIC_ANALYSIS_FINDING),
})
```

One normalized `Finding` shape across all three tools, not three
tool-specific sub-schemas — every downstream consumer (MCP tool, PR-review
merge, AIRview) reads one shape regardless of which tool produced it.
`severity`/`type` normalization mapping (SonarQube's
BLOCKER/CRITICAL/MAJOR/MINOR/INFO + BUG/VULNERABILITY/CODE_SMELL, Semgrep's
ERROR/WARNING/INFO, Bearer's critical/high/medium/low + its data-type
categories) is real, non-trivial mapping work — not a detail to hand-wave.

## Per-tool deployment reality (the part that actually matters)

The three tools are **not** operationally equivalent, and treating them the
same would be a real mistake:

| | Semgrep | Bearer | SonarQube |
|---|---|---|---|
| Invocation | subprocess, stateless | subprocess, stateless | requires a **running server** (Docker, ~2GB+ RAM, Postgres-backed) + separate scanner CLI + async poll for results |
| Per-scan cost | seconds | seconds (git-tracked files only — real requirement hit live tonight) | minutes, plus the server's own uptime/ops burden |
| Fits `check_X(repo_path) -> dict` directly | yes | yes | **no** — needs a live server dependency injected, not just a subprocess call |

**This forces a real architectural decision, not a detail**: Semgrep and
Bearer slot directly into `scan_repository()` as two more `check_X` modules,
same as OSV.dev. SonarQube does not, without first answering:

- Run one shared, persistent SonarQube server as new production
  infrastructure (another service in the real deploy stack, real hosting
  cost, real maintenance surface, matching-scale question to the existing
  Redis/Postgres/jina-embed services already running) — or
- Treat SonarQube as **local/opt-in only** (a user runs their own instance,
  points `aletheore scan` at it via `SONARQUBE_HOST_URL`, `checked: False`
  otherwise) — no new production infrastructure, but no SonarQube coverage
  for hosted scans (the actual paying-customer surface) either.

I'm not picking this for you — it's a real infra-cost-vs-coverage tradeoff,
not an engineering detail, and needs your call before any SonarQube code gets
written.

## PR review (Flash Review) timing — a second real fork

Flash Review runs per-PR, in the cost/latency budget a paid review already
has (see `flash_review.py`'s own extensive cost-tuning history). Running a
full scanner pass on every PR is a different cost shape than running it once
at `aletheore scan` time:

- **Semgrep/Bearer**: cheap enough (seconds, stateless CLI) to run **fresh,
  scoped to just the diff's changed files**, per PR — always current, no
  staleness. Real precedent: this is exactly what `find_semantic_regressions`
  already does today, just regex/AST-based instead of a subprocess.
- **SonarQube**: too slow/heavy to run per-PR under any realistic review
  latency budget. Its findings can only realistically feed PR review as
  **pre-computed, from the last full `aletheore scan`**, filtered to the
  diff's touched lines — same staleness tradeoff `referenced_symbol_context`
  already accepts (built from the last scan, not live).

Recommended split: Semgrep + Bearer run diff-scoped per PR (new
`find_static_analysis_regressions(diff_text, diff_patches)` in
`semantic_checks.py`, merged the same way `find_semantic_regressions` is).
SonarQube feeds AIRview/MCP only, via the full-repo-scan path, not live PR
review — this also sidesteps the server-availability question for the
review path specifically, even if the server-hosting question above still
needs an answer for AIRview/MCP.

## Concrete file list

New:
- `src/aletheore/static_analysis/semgrep_scanner.py` — `check_semgrep(repo_path) -> dict`, subprocess call, `--config=auto`, normalize output.
- `src/aletheore/static_analysis/bearer_scanner.py` — `check_bearer(repo_path) -> dict`, subprocess call, normalize output. Must handle the git-tracked-files requirement (confirmed live tonight — untracked working trees return zero findings silently, not an error worth surfacing as a false "clean").
- `src/aletheore/static_analysis/sonarqube_scanner.py` — `check_sonarqube(repo_path, host_url) -> dict`, scanner CLI invocation + async task-poll + `/api/issues/search` pull, `checked: False` when `host_url` unset/unreachable.
- `src/aletheore/static_analysis/__init__.py` — `check_static_analysis(repo_path, tools, sonarqube_host_url=None) -> dict`, orchestrates the three, normalizes severity/type into the shared `Finding` shape.

Modified:
- `src/aletheore/air_schema.py` — add `security.static_analysis` (above).
- `src/aletheore/evidence.py::scan_repository` — new `check_static_analysis: bool` param, same call/skip pattern as `check_vulnerabilities`.
- `src/aletheore/cli.py` — new `--no-check-static-analysis` flag (or equivalent opt-out), mirroring existing flags.
- `src/aletheore/mcp_server.py` — new `aletheore_static_analysis` query tool + `_QUERY_TOOL_DESCRIPTIONS` entry.
- `github-app/scan_worker/semantic_checks.py` — new `find_static_analysis_regressions` (Semgrep/Bearer, diff-scoped).
- `github-app/scan_worker/flash_review.py::review_diff` — call the new function alongside `find_semantic_regressions`, merge into the same `semantic_findings` list already being built.
- `src/aletheore/report.py` / `openai_compatible.py`'s `EVIDENCE_SCHEMA_MAP` — add the new section so AIRview's real tool-calling report generation can read it (zero new code beyond the schema-map entry — `invoke()`'s existing `read_evidence_section` tool already generalizes over whatever sections the schema map lists).

## Sequencing

1. Semgrep + Bearer first (no infra decision blocking them, real value proven live tonight, lowest-risk path to shipping something).
2. SonarQube only after the hosting decision above is made — it's gated on you, not on engineering readiness.
3. PR-review merge (Semgrep/Bearer diff-scoped) can land alongside step 1, independent of AIRview/MCP wiring.

## Open decisions requiring your call

1. SonarQube: shared persistent server (new infra) vs. opt-in/local-only (no hosted coverage). Blocks all SonarQube work, not Semgrep/Bearer.
2. Severity/type normalization mapping across the three tools' different taxonomies — I can draft this, but it's a real judgment call worth a second look before it's load-bearing for anything downstream.
3. Whether `aletheore scan`'s default behavior changes (static analysis on by default vs. opt-in like `--check-licenses` already is) — cost/time impact on every scan, same category of decision as the licenses/vulnerabilities flags already went through.
