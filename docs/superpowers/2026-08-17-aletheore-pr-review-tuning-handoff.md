# Aletheore PR Review Tuning Handoff

**Purpose:** Record the current state of the PR-review benchmark and deterministic review hardening.

**Status:** In progress; implementation changes are uncommitted.

**Owner:** Aletheore maintainers (placeholder)

**Related Documents:** `benchmarks/pr-review-benchmark/README.md`, `benchmarks/pr-review-benchmark/METHODOLOGY.md`, `STARTUP_AUDIT_REPORT.md`, `Claude_Audit.md`, `immediate_issue_PRs.md`

**Last Updated:** 2026-08-17

## Current Position

Aletheore's Flash Review currently combines:

- the hosted model review;
- changed-file content and citation grounding;
- pull-request title/body context when available;
- deterministic change-impact signals;
- referenced definitions for imported symbols;
- deterministic semantic regression checks; and
- scanner-derived dependency impact context.

The current repository is on `master` at `ff0c6fc`, matching `origin/master`. The tuning work below has not been committed or pushed.

## Benchmark Progress

The local PR-review corpus contains 50 cases:

- 40 real bug fixes;
- 6 injected regressions; and
- 4 clean changes.

The earlier eight-case Ollama experiment used the xref2 PR set with `llama3.1:8b`. At 512 output tokens, the Aletheore-context arm produced grounded findings for all eight cases but correctly identified only 3/8 expected regressions. The baseline also produced false positives. A 1024-token smoke case showed the treatment correctly identifying the removed exception handler, but this is not sufficient evidence of broad recall.

The experiment now has committed ground truth in the separate benchmark repository:

- repository: `/Users/arihantkaul/Documents/GitHub/aletheore-benchmarks`;
- commit: `30b4240 bench: add xref2 PR review ground truth`;
- expected regressions: exception handling, mutation, retries, wrong exception type, iterator reuse, concurrency, double scaling, and ordering.

The benchmark repository also contains uncommitted runner/results work. Those files are separate from the Aletheore implementation and must not be mixed into this handoff's code changes.

## Implemented Changes

### Review context

In `github-app/scan_worker/flash_review.py` and `github-app/scan_worker/jobs.py`:

- deleted imports and guards can contribute referenced-symbol context;
- PR title/body text is included as untrusted context;
- test files are labeled separately in the prompt;
- deterministic signals cover mutation, exceptions, iterator consumption, retries, concurrency, scaling, and ordering;
- referenced definitions include observable contract markers such as raises, yields, mutation, concurrency, retry, and scaling markers;
- dependency impact context includes only scanner-derived imports and direct dependents;
- contributor identity and repository history are deliberately excluded from the external model prompt;
- semantic findings are merged before model findings at the same file/line location;
- semantic findings survive an empty or malformed model response; and
- cached review results are re-merged with current deterministic findings.

### Deterministic semantic checker

`github-app/scan_worker/semantic_checks.py` is evidence-only and fails closed when the required evidence is unavailable. As of Codex's handoff it checked 8 patterns anchored on a resolved cross-file `referenced_symbol_context` (removed exception handling, wrong exception type, one-shot iterator reuse, removed defensive copy, double scaling by 100, shared mutable state under concurrency, repeated retry-loop mutation, a fallible call moved before a log/record side effect). See the Continuation section below for 5 more checks added since, none of which need `referenced_symbol_context` at all.

The implementation is intentionally not a general static analyzer. It must only emit a finding when the diff and current source directly support it.

## Validation

Focused validation currently passes:

```text
PYTHONPATH=. pytest -q tests/test_flash_review.py
90 passed
```

`git diff --check` is clean. A broader worker/API test run was attempted but did not produce a completion summary in the current environment; it must be rerun before merge. Python bytecode compilation was also blocked by an existing local `__pycache__` permission issue, while the focused test import path succeeded.

Current Aletheore implementation changes (as of Codex's handoff):

- modified: `github-app/scan_worker/flash_review.py`;
- modified: `github-app/scan_worker/github_api.py`;
- modified: `github-app/scan_worker/jobs.py`;
- modified: `github-app/tests/test_flash_review.py`;
- untracked implementation: `github-app/scan_worker/semantic_checks.py`.

Additional changes from the Continuation section below (same files, further edited, plus one new
benchmark script):

- `github-app/scan_worker/semantic_checks.py`: rewritten for hunk-level scoping, plus 5 new checks;
- `github-app/tests/test_flash_review.py`: 14 new tests for the scoping fix and the 5 new checks;
- untracked: `benchmarks/pr-review-benchmark/scripts/evaluate_semantic_checks.py` (the corpus
  evaluator itself - durable tooling, not a benchmark result, so it belongs with the implementation
  change it verifies).

Pre-existing user files that must remain untouched:

- `Claude_Audit.md`;
- `immediate_issue_PRs.md`.

## RepoWise Comparison

RepoWise currently emphasizes a two-tier file/symbol graph, confidence-aware call resolution, change-risk queries, co-change history, code health, generated documentation, and task-shaped MCP tools. Aletheore already has scanner evidence, dependency graphs, git intelligence, MCP tools, evidence packets, security findings, and hosted review infrastructure.

The next useful RepoWise-inspired improvements are:

- confidence-bearing symbol/call resolution for review context;
- a deterministic changed-symbol blast-radius view;
- changed-test and historically co-changed-file context, clearly labeled as context rather than proof;
- a benchmarked `get_change_risk`-style query backed by existing evidence; and
- a corpus-calibrated evaluation of which deterministic checks improve recall without increasing clean-case false positives.

Do not copy opaque composite scores or make ownership/history claims in an external model prompt without an explicit data-handling decision.

## Known Limitations (as of Codex's handoff)

- The semantic checker is regex-based and currently covers only proven regression families.
- It does not yet evaluate the full 50-case corpus automatically against reconstructed source checkouts.
- The 512-token xref2 result is diagnostic, not a publishable product benchmark.
- The full GitHub App suite has not been confirmed green in this work session.
- The current implementation changes are not committed, pushed, or deployed.
- Model output variance remains a major confounder; repeated identical runs varied materially.

## Continuation (Claude, 2026-08-17, same session)

Picked this up per the handoff above. Summary of what changed on top of it, in order:

**1. Verified, didn't just trust, the handed-off state.** Focused suite really is 90/90. Ran the
full suite for real (throwaway local Postgres+Redis, since dev-env config wasn't set up locally):
1342 passed, 8 skipped, 13m29s — closes the "not confirmed green" limitation above.

**2. Found and fixed a real precision gap in `semantic_checks.py` before committing anything.**
Every check operated at whole-file scope: `file_contents` holds the entire current file and
`referenced_symbol_context` bundles up to 8 unrelated referenced symbols, so a whole-file scan
means any matching keyword *anywhere* in the file satisfies a check's condition regardless of
whether it has anything to do with the call being examined. Two checks (shared-state+concurrency,
retry-loop-mutation) were loose enough to false-positive on ordinary PRs that touch more than one
thing in the same file; the removed-exception-handler check had the same flaw in reverse (an
unrelated handler elsewhere in the file could mask a real regression at the actual call site).
Rewrote every check to be scoped to the diff hunk nearest the call site (`DIFF_HUNK_TOLERANCE`,
matching `flash_review.py`'s own `DIFF_LINE_TOLERANCE` convention), not the whole file. Verified
the false-positive tests have real discriminating power by confirming they fail against the
original code before the fix (not just pass trivially against the new code). All 94 tests green
(90 original + 4 new adversarial tests), full suite reconfirmed at 1346 passed / 8 skipped.

**3. Built the corpus evaluator the "Next Steps" below called for**
(`benchmarks/pr-review-benchmark/scripts/evaluate_semantic_checks.py`): materializes each case at
its pinned base commit via the existing `prepare_case_checkout`/`load_case` machinery, converts
the real `git diff` into Aletheore's internal review format, runs the real `scan_repository` +
`build_referenced_symbol_context` + `find_semantic_regressions` pipeline against it, and compares
findings against `ground_truth.yaml`. Local and offline — no live PR, no hosted tools. Excludes the
25 `swebench-*` cases by default (a separate citation-grounding effort against large real repos,
mixed into the same `cases/` directory but unrelated to this corpus).

**First run found 0/18 recall, 0/4 false positives.** Before trusting that number: caught a real
bug in the evaluator itself first (`_changed_files_from_diff` used `^`/`$` without `re.MULTILINE`
against the whole multi-line diff via `.finditer()`, so it always returned `[]`, silently zeroing
`file_contents` and `changed_files` everywhere downstream). Fixed it, verified `referenced_symbol_context`
really does populate when the diff's code actually calls an in-repo symbol (6 of 22 successfully-run
cases did), and reran. **Real, verified result: still 0/18** — but now an honest one. Checked the
specific cases with real referenced-symbol context (e.g. case 004: an entire fallback branch
deleted) against the actual diffs: their bug shapes don't match any of the 8 check patterns. Also
confirmed 3 cases (`002`, `003`, `021`, all `requests`) error on an unrelated, pre-existing
`scan_repository` bug parsing a malformed real git-history timestamp
(`'2011-09-08T02:38:50+518:00'`, an invalid UTC offset) — flagged, not fixed here, out of scope.

**4. Added five new checks, informed by the real failure shapes, not guessed.** Read every one of
the 18 real bug diffs before designing anything (two initial hypotheses turned out wrong on actual
inspection — case 007's real bug is a lost boolean disjunct, not a defensive-copy removal; case
018's bug is a missing guard in *newly added* code, not a removed one — good thing both were
checked against the real diff before building). Also verified case 020 (hardcoded secret) doesn't
need a new check at all - Aletheore already has a separate `secrets.py`/`find_secrets` mechanism
for exactly that bug class, outside `semantic_checks.py` entirely. Five shapes were genuinely
tractable, each verified against its real target case via the corpus evaluator (ground truth), not
just a synthetic unit test:

- `_resource_leak_findings`: a `close()`/`defer X.Close()` call was removed with nothing nearby
  still closing that variable. Matches gin-gonic/gin#4422 (case 010) exactly.
- `_copy_to_alias_findings`: Go's `make()+copy()` defensive-copy idiom replaced with a bare
  re-slice/alias. Matches spf13/cobra#2257 (case 009) exactly. Scoped to Go's `copy()` builtin
  specifically, not generalized to other languages' copy idioms, because case 009 is the only real
  evidence this check is built from.
- `_removed_bounds_clamp_findings`: a `max(0, ...)`/`Math.max(0, ...)` bound wrapping an
  assignment's whole right-hand side disappeared, with the same variable still assigned the
  unwrapped inner expression in the same hunk. Matches axios#6807 (case 005) exactly; the call
  syntax matched (`Math.max`, `math.max`, bare `max`) spans JS/Java/Python/Go, since it's the call
  *shape* being matched, not one language's syntax.
- `_off_by_one_loop_findings`: a counted loop bounded by `<=` against a collection's length/size,
  indexing that same collection with the loop variable - the classic off-by-one. Matches
  apache/commons-lang#1247 (case 017) exactly. Two real language shapes covered on real evidence: C-style
  `for (i = 0; i <= x.length; i++)` (Java/JS/C#/C/C++, sharing that loop syntax) and Go's `for i :=
  0; i <= len(x); i++` (paren-less `for`, function-call `len()` - a real bug caught and fixed while
  writing this check's own Go test: the first version assumed C-style `for(...)` parens
  unconditionally and silently matched nothing on Go's actual syntax).
- `_sql_injection_findings`: a SQL statement keyword pair (`SELECT...FROM`, `INSERT INTO`,
  `UPDATE...SET`, `DELETE FROM`) inside a string literal, concatenated directly with a bare
  variable via `+`. Matches flask's `build_user_lookup_query` (case 016) exactly. Deliberately
  requires the keyword *pair*, not a single keyword - "select" and "update" are also ordinary
  English words, and a single-keyword match would false-positive on log/UI strings ("Update your
  settings"); verified with a dedicated negative test.

None of the five need `referenced_symbol_context` at all - all five read only the diff and the
current file. This forced a real architectural fix: `find_semantic_regressions`'s early-exit
required `referenced_symbol_context` to be non-empty, which would have silently blocked every one
of them from ever running on the (large majority of) real diffs that call no in-repo symbol.
Changed the early-exit to require only `file_contents`.

Considered and deliberately rejected as too risky to generalize (real false-positive exposure on
ordinary code, checked against the actual diffs before deciding, not assumed from the bug_type
label alone): case 004's removed fallback branch, cases 006/007's narrowed boolean conditions
(boolean-condition changes are extremely common in ordinary refactors), case 011's control-flow
restructuring, case 012's `continue`→`break` change, case 019's unsynchronized counter (would need
real concurrency analysis, not a regex), and case 021's `except Exception: pass` (the diff's own
docstring explicitly documents it as intentional best-effort cleanup - flagging bare excepts
broadly would hit exactly this kind of legitimate code constantly).

**Reran the corpus evaluator after each addition: final result 5/18 recall (up from 0), 0/4 false
positives (unchanged throughout).** `test_flash_review.py` now 108/108 (14 new tests across the
five checks: one positive real-shape match and at least one negative/false-positive guard per
check, plus the Go-syntax variant and the architectural no-`referenced_symbol_context` proof).

## Known Limitations (current, after the continuation above)

- Recall on the real 18-case corpus is 5/18. The remaining 13 bugs' shapes (string-literal typos,
  regex-semantics bugs, HTTP-spec violations, type-inconsistency bugs, bit-manipulation bugs,
  equals/hashCode contract violations, race conditions, and the several patterns explicitly
  rejected above for false-positive risk) mostly aren't tractable for this checker's narrow,
  evidence-anchored design without a materially different approach (real static analysis, type
  information, or genuine call-graph reasoning) - not a tuning gap this session left unclosed, an
  honest ceiling on what regex-based, diff-scoped checks alone can catch without a much higher
  false-positive budget than this project has been willing to accept anywhere else.
- `002`/`003`/`021` (all `requests`) error in the corpus evaluator on an unrelated, pre-existing
  `scan_repository` bug (malformed git-history timestamp). Not fixed in this session.
- The 50-case corpus (as opposed to the 25 real-bug ones) and the separate 25 `swebench-*` cases
  still haven't been run through this evaluator together in one pass with the swebench inclusion
  flag - deliberately deferred, since they're a different effort with a different purpose.
- The RepoWise-inspired blast-radius/confidence-aware symbol resolution work below is still not
  started - deliberately deferred until real recall data existed to inform it, rather than building
  it blind.
- The 512-token xref2 result is still diagnostic, not a publishable product benchmark.
- Model output variance remains a major confounder for the live-model side of Flash Review (not the
  deterministic checks, which are exercised directly and deterministically by the evaluator above).

## Next Steps

1. ~~Rerun the focused and full GitHub App suites in a writable test environment.~~ Done above.
2. ~~Add a corpus evaluator...~~ Done above (`scripts/evaluate_semantic_checks.py`).
3. ~~Measure precision on all clean cases before adding more check families.~~ Done: 0/4 held
   before and after the two new checks.
4. Add confidence-aware symbol resolution and changed-symbol blast-radius context - still open,
   now informed by real data: `build_referenced_symbol_context` only resolves a usable cross-file
   symbol for ~27% of real diffs (6/22 in this corpus), which is the actual ceiling any
   reference-anchored check family is working against until this is built.
5. Rerun the xref2 experiment with the production model budget, fixed model/configuration,
   committed ground truth, and recorded raw outputs.
6. Commit the Aletheore implementation changes (separately from `benchmarks/pr-review-benchmark`'s
   own results/scripts, which track their own history), then deploy.

## Evidence Links

- RepoWise dependency graph: <https://docs.repowise.dev/intelligence/dependency-graph>
- RepoWise architecture and intelligence layers: <https://docs.repowise.dev/getting-started/what-is-repowise>
