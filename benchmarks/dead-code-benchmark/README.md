# Aletheore Dead-Code Detection Benchmark

Measures accuracy of `find_dead_code()` (`src/aletheore/dead_code.py`),
which detects two kinds of finding via a real `scan_repository()` run:
**unreachable modules** (no other file imports this one, and no
entry-point signal explains why) and **unused dependencies** (a package
declared in `requirements.txt`/`package.json` that no source file
imports). Never systematically benchmarked before — only reactive fixes
had touched it (the O(candidates×files)→O(files) perf fix and a
false-positive boundary fix, both PR #417/#420).

Fully deterministic (no LLM calls, no network) — like
`security-scanner-benchmark`, this has no paid-API cost and no LLM
judge.

## Result

10 pilot cases, run via a real `scan_repository()` call per case (not
mocked): **10/10 recall, 0/7 false positives.**

| case_id | finding_type | category | outcome |
|---|---|---|---|
| 001-unreachable-module | unreachable_module | true_positive | TP |
| 002-imported-module-not-dead | unreachable_module | true_negative | TN |
| 003-main-guard-entry-point | unreachable_module | true_negative | TN |
| 004-html-script-entry-point | unreachable_module | true_negative | TN |
| 005-dotted-string-reference-not-dead | unreachable_module | true_negative | TN |
| 006-test-file-not-flagged | unreachable_module | true_negative | TN |
| 007-unused-pip-dependency | unused_dependency | true_positive | TP |
| 008-used-pip-dependency | unused_dependency | true_negative | TN |
| 009-unused-npm-dependency | unused_dependency | true_positive | TP |
| 010-used-npm-dependency | unused_dependency | true_negative | TN |

## A real, severe bug found and fixed

Cases 008 and 010 (a real, genuinely-imported dependency) both failed on
the first run — every declared dependency was reported unused, even
ones the fixture's own source file plainly imported. Confirmed at real
scale before touching anything: scanning Flask's own repo reported
**all six of its actual runtime dependencies**
(werkzeug/jinja2/itsdangerous/click/blinker/importlib-metadata) as
unused.

**Root cause**: `module["imports"]` (`scanner/graph.py`'s
`resolved_imports`) only ever contains import targets that resolved to
*another file inside the repo* — an external package import that never
resolves to a repo-internal file is silently dropped there, by design
(that field also feeds the real import graph — edges, `imported_by`,
hotspots, MCP's `aletheore_imports` — where only real repo-internal
edges belong). `_import_roots()`, which the unused-dependency check
reads, was therefore checking a field that structurally can never
contain an external package name. Every existing unit test for this
check (`test_dead_code.py`) passed anyway, because every one of them
hand-sets `modules[0]["imports"] = ["flask"]` — a raw package name — a
shape the real scanner never produces; none of them exercised a real
scan.

**Fix**: `_raw_external_import_roots()` re-reads each Python/JS/TS
source file directly and regex-extracts raw import roots regardless of
whether they resolve internally, scoped entirely to `dead_code.py`
rather than changing what the resolvers keep in the graph-wide `imports`
field (a much larger, riskier change). Verified against the exact repo
that surfaced the bug: Flask now reports zero unused dependencies.
Regression tests: `test_unused_dependency_check_reflects_a_real_scan_not_a_hand_built_modules_list`
(a real `scan_repository()` call — the class of test that would have
caught this), plus direct unit tests for the new function.

## A note on what "unused" real repos still report

Re-run against 4 more real repos (already downloaded for
`security-scanner-benchmark`'s real-repo validation) after the fix:
`requests` and `click` report zero unused dependencies; `express`
reports `eslint`/`hbs`/`mocha`/`nyc`, and `axios` reports a longer list
(babel/rollup/eslint/prettier/typescript/vitest/etc.). These are real,
plausible findings, not a bug — they're build/lint/test-runner tooling
declared in `package.json` and invoked via npm scripts or a CLI, never
`import`ed by any source file, so "no source file imports this" is
literally true. Whether treating CLI-only dev tooling as "unused" is the
right default (versus a lower severity, or excluding `devDependencies`
entirely) is a real product/precision question, not addressed by this
benchmark — flagged here for visibility, not fixed.

## Running

```bash
python3 scripts/run_benchmark.py
```

## Contents

| path | what |
|---|---|
| `cases/` | 10-case pilot corpus, one `repo/` fixture tree + `ground_truth.yaml` each |
| `scripts/run_benchmark.py` | runs `scan_repository()` against every case, scores it |
