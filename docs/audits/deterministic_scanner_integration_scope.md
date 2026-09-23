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

1. ~~SonarQube: shared persistent server (new infra) vs. opt-in/local-only (no hosted coverage).~~ Decided and shipped 2026-09-21: opt-in/local-only. `checked: False` unless `SONARQUBE_HOST_URL` is set, no new production infra, no hosted-scan coverage until a shared server is actually stood up. Real-tested against an actual local SonarQube Community Edition server (Docker) tonight, including a full analysis submit plus Compute Engine poll plus issue fetch cycle, not just written against docs.
2. ~~Severity/type normalization mapping~~ Drafted and shipped, see the per-tool `_SEVERITY_MAP`/`_TYPE_MAP` (or `_CATEGORY_TYPE_MAP` for Semgrep) constants in each `static_analysis/*_scanner.py` module, each with a comment explaining the mapping's reasoning. Still a judgment call worth a second look once real findings start flowing through PR review, not a settled taxonomy.
3. ~~Whether `aletheore scan`'s default behavior changes~~ Decided and shipped: on by default for Semgrep/gosec/Bandit (`--no-check-static-analysis` to opt out), matching `check_vulnerabilities`/`check_licenses`'s existing convention. Each scanner self-skips gracefully (`checked: False`, no error) when its binary isn't installed, so this costs nothing extra in an environment missing a tool. Bearer and SonarQube are the two exceptions - SonarQube gated by decision 1 above; Bearer made opt-in after real testing found its full-repo runtime doesn't scale cleanly with repo size (18.7s on a 241-file subtree, still running past 300s on this repo's own ~3,331-file tree) - `--check-bearer`/`--no-check-bearer`, or an interactive ask-and-warn prompt on a real terminal when neither is passed, defaulting to skipped non-interactively (CI, scripts, the hosted worker's own subprocess invocation).

## What actually shipped (2026-09-21)

All six tools are wired in, real-tested, not just written:

- New package `src/aletheore/static_analysis/`: `semgrep_scanner.py`, `bearer_scanner.py`, `gosec_scanner.py`, `bandit_scanner.py`, `sonarqube_scanner.py`, `joern_scanner.py` (+ `joern_queries/asymmetric_cache_trust_go.sc`, the real CFG-based query), `_exclusions.py` (shared ignore-dir logic, see below), `__init__.py` (`check_static_analysis` orchestrator).
- Schema: `security.static_analysis` added to `air_schema.py` (`EVIDENCE_VERSION` bumped to `0.6.0`, see `docs/AIR-SCHEMA.md`'s changelog).
- `evidence.py::scan_repository`: new `check_static_analysis`/`sonarqube_host_url` params, same call/skip pattern as every other check. `watch.py`'s incremental rebuild skips it (too slow for a save-triggered loop) and carries forward the last real result, same as vulnerabilities/licenses.
- CLI: `--check-static-analysis`/`--no-check-static-analysis` on `scan` and `audit`, plus `.aletheore.json`'s `disabled_checks` now accepts `"static_analysis"`.
- MCP: `aletheore_static_analysis` query tool.
- AIRview: `security.static_analysis` added to `EVIDENCE_SCHEMA_MAP` in `openai_compatible.py`, zero new code beyond the schema-map entry, exactly as this doc predicted.
- PR review: `find_static_analysis_regressions` in `semantic_checks.py` (Semgrep+Bearer, diff-scoped, materializes just the changed files into a throwaway git-tracked temp checkout, runs both scanners, filters to the diff's actual changed-line ranges), merged into `flash_review.py::review_diff` alongside `find_semantic_regressions`. SonarQube deliberately excluded from this path per this doc's own "PR review timing" section above.
- Real bugs found and fixed during real-testing, not just written and assumed correct: an unfiltered `bandit -r .` against this repo's own working tree returned 78,244 findings before exclusion handling existed (1,376 of them duplicates under `.claude/worktrees/<agent-id>/` alone, a nested checkout of this same repo); `--config=auto` combined with `--metrics=off` is a hard Semgrep error that would have silently reported "checked: True, zero findings" for a completely failed scan; bandit's own path-normalization resolved a relative `./app.py`-shaped filename against the wrong process's cwd; a locally-loaded custom Semgrep rule's `check_id` came back as a mangled absolute-path-derived string; `.repowise`'s 51MB local index cache wasn't excluded and alone accounted for the difference between an 18.7s scoped Bearer scan and a 10+ minute full-repo one. Each has a real regression test in `src/tests/test_*_scanner.py` / `test_static_analysis_*.py` / `github-app/tests/test_semantic_checks.py`.
- Real, separate gaps found but not fixed here (flagged, not silently patched into shared code as a side effect of this change): `scanner/detect.py`'s `IGNORED_DIRS` doesn't include `.worktrees` or `.repowise`, so language detection/dead-code/secrets-scanning/hotspots likely have the same double-counting exposure this package's own `_exclusions.py` had to work around locally.
- Follow-up the same night: excluding `.repowise` alone did not close the gap - a re-test against this repo's full ~3,331-file tree still hadn't finished at 300s. That, plus the earlier `github-app/`-subtree number (18.7s for 241 files - not a linear extrapolation to the full repo either), is real evidence Bearer's full-repo runtime doesn't scale cleanly with repo size, not fully root-caused (the process showed almost no CPU time across those 5 minutes - I/O-bound or otherwise inefficient, not confirmed which). Decided with the user: Bearer moved from "on by default" to opt-in specifically, with a repo-size-scaled timeout (`_scaled_timeout` in `bearer_scanner.py`, 120s base + 0.5s/file up to a 1800s ceiling - a first-pass calibration from these two data points, not a tuned constant) and an interactive ask-and-warn prompt in `cli.py`'s `scan`/`audit` commands when no explicit `--check-bearer`/`--no-check-bearer` flag is given on a real terminal (defaults to skipped non-interactively). Semgrep/gosec/Bandit's measured runtimes were fine at this repo's scale (12.8s, 0.5s, and near-instant respectively against ~3,331 real files) - see the later entry below for where that assumption broke on a genuinely bigger repo.
- Also decided with the user: third-party tool/rule identity (`tool`, `rule_id`) stays in `air.json`'s `security.static_analysis` for internal attribution, but PR-review comment text (`find_static_analysis_regressions` in `semantic_checks.py`) no longer prefixes a finding with `[tool/rule-id]` - every finding presents as Aletheore's own, matching how `find_semantic_regressions`' other checks already read.
- Joern added the same night, once the above five were real-tested end to end. Real infra work this required, not just a wrapper: Joern wasn't installed at all (the earlier scratch validation and its `/tmp` artifacts were gone) - reinstalled via Joern's own official installer (`~/bin/joern`, no sudo, user-space only) plus GNU coreutils (`greadlink`, a real hard dependency of Joern's own shell scripts on macOS that isn't installed by default). `gosrc2cpg` needs a `go.mod` at the parse target for module resolution - pointing it at a bare subdirectory without one fails immediately, confirmed live; the real fix was calling `gosrc2cpg` directly rather than the `joern-parse` wrapper, which hit an unrelated `NoSuchElementException` in this Joern version. The query itself (`joern_queries/asymmetric_cache_trust_go.sc`) was rebuilt from scratch (the original was deleted with the rest of that night's `/tmp` scratch work) using real interactive exploration of the actual CPG structure - confirmed live that Go's `if stmt; cond {}` desugars to the guard call and its guarding `if` as CFG siblings, not nested, which is why the query walks forward along CFG edges from the guard call to find the `if` rather than searching its AST subtree. Validated exactly like every other check tonight: fires on the real target (grafana/grafana#103633's `Service.Check`, `permDenialCache.Get` vs. `getCachedIdentityPermissions`), zero false positives across the same three other real Go repos this session already had checkouts for (grafana-76186, -79265, -80329). Wired in as a third opt-in scanner alongside Bearer (`run_joern`/`--check-joern`, no prompt - Joern requires a whole separate toolchain almost no install will have, so `check_joern`'s own not-installed self-skip already covers the common case), not added to the PR-review diff-scoped path (a CPG build is real per-invocation JVM-startup-plus-parsing cost, and a diff-scoped temp checkout has no `go.mod` of its own either) - real cost measured live: ~5s end to end (two JVM startups: `gosrc2cpg` then `joern --script`) for one small real Go package, not remotely comparable to Semgrep/gosec/Bandit's fast stateless subprocess calls.
- Independently re-tested the same night by a second agent (a full deterministic-scanner recall pass against the 44-golden-bug corpus, run separately from this integration work): confirmed the Joern query works exactly as designed - it caught the one golden bug it was built for (grafana-103633) and nothing else, 1/1 on its actual job. It also surfaced a real bug in this integration: **Semgrep and gosec both timed out at their old flat 180s against a real full-scan pass on grafana/grafana** (a genuinely monorepo-scale repo, not just "big" - this repo's own ~3,331-file tree, the only real data point behind the original 180s default, is far smaller). Same root cause as Bearer's earlier gap (fast on a moderate real repo, not fast on a huge one), fixed the same way: `count_real_files` factored out of `bearer_scanner.py` into the shared `_exclusions.py`, and both Semgrep (60s base + 0.05s/file) and gosec (same shape, weaker proxy - gosec's real bottleneck is module/dependency-graph resolution via `go/importer`, not raw file count, but no better signal is available) now scale the same way, capped at the same 1800s ceiling. Semgrep/gosec stayed on-by-default rather than moving to opt-in like Bearer/Joern - their measured per-file rate is still much cheaper - but a real, now-confirmed cost profile worth knowing: on a sufficiently large monorepo, an on-by-default check can now take up to 30 minutes rather than silently truncating at 180s. Whether that changes the on-by-default calculus is a real product question, not decided here.
- The same benchmark run also surfaced that a fully-noise-free deterministic layer is further off than "6 scanners wired in" implies: gosec's `sql_concat_sqli` rule fired on fully-parameterized xorm session/statement-builder queries (a real, known-noisy rule, not fixed here), and the diff-scoped PR-review path (Semgrep+Bearer only, what actually runs on a live PR) contributed zero true positives on the 13-case corpus it was tested against, with some real low-signal noise from logger-leak rules on test fixtures. Flagged, not fixed - a candidate for a later pass at rule-level suppression/tuning once there's a broader real-usage signal to tune against, not something to guess at from one 13-case run.
