# Aletheore AST-Pattern Search Benchmark

`search_ast_pattern()` (`src/aletheore/ast_pattern.py`, the `aletheore
query ast-pattern` command and `aletheore_ast_pattern` MCP tool) matches
a raw tree-sitter S-expression query against every parsed file of one
language in a repo. Unlike the other overnight benchmarks in this
directory (`dead-code-benchmark/`, `security-scanner-benchmark/`), this
one didn't need a new synthetic pilot corpus — `src/tests/test_ast_pattern.py`
already has 12 tests exercising the real function end-to-end (unknown
language, invalid query, TypeScript's dual .ts/.tsx grammars, line
numbering, size cap, unreadable-file resilience, match cap, char budget,
and the single-oversized-match fix from an earlier PR) — all 12 pass.

What that suite couldn't have caught: it uses single-digit-file
fixtures, and this module's one previously-known failure mode (a
tree-sitter Query/QueryCursor segfault, `requires-python = "<3.14"` in
`pyproject.toml`) was explicitly documented as needing "enough real
files/matches" to accumulate before it triggers. So instead of a
synthetic corpus, this ran real structural queries against the large
real repos already downloaded for `security-scanner-benchmark`'s
real-repo validation.

## Result: a real, reproducible segfault on Python 3.12 — not 3.14-only

`pyproject.toml`'s own comment claimed "the exact same code against the
exact same repo runs clean on 3.12." That claim was only ever verified
against this project's own ~116 source files. Confirmed false at real
scale, reproduced twice in a row (exit code 139 both times):

```python
from pathlib import Path
from aletheore.ast_pattern import search_ast_pattern
search_ast_pattern(Path("<a real Django checkout>"), "python", "(try_statement) @try")
# SIGSEGV, Python 3.12.10, tree-sitter 0.26.0
```

Django's real tree has ~2,930 Python files. A **higher-match-density**
query against the same repo (`(function_definition) @fn`, which hits
`_AST_PATTERN_TOTAL_CHAR_BUDGET` after only ~126 matches and stops
early) completed cleanly — and two other large real repos, react (JS)
and client-go (Go), both completed cleanly too, because their test
queries happened to hit `_AST_PATTERN_MATCH_CAP` (200) within the first
few files.

**The crash correlates with how many files get fully processed before
any cap triggers, not language, match count, or Python version alone.**
A query selective enough to run long against a big enough repo can hit
this on 3.11/3.12 too — inside the officially supported, CI-tested
range — not only on the already-excluded 3.14. The two prior fixes
this codebase's own comments say were tried and failed (`del` each
iteration, `gc.disable()`/forced `gc.collect()`) evidently only ever got
verified at the ~116-file scale where the crash doesn't yet manifest.

## What's fixed here, what isn't

**Fixed**: the false "runs clean on 3.12" documentation claim, in both
`pyproject.toml`'s `requires-python` comment and
`src/aletheore/ast_pattern.py`'s module docstring — corrected to
describe the real, file-count-correlated trigger, with full repro
details, so the next person doesn't trust a claim that was never
actually stress-tested. Pure documentation change, zero behavior
change — all 12 existing tests still pass.

**Not fixed**: the crash itself. The two most obvious untried
mitigations both carry real design trade-offs that deserve human
sign-off rather than a speculative overnight change to already-fragile
code that two prior fix attempts already failed on:

- **Subprocess isolation** (explicitly named as a possible fix in the
  existing `pyproject.toml` comment) — contains the blast radius (a
  crash kills a child process, not the caller), but every call now pays
  process-spawn overhead to protect the rare large/low-match-density
  case, and a mid-run crash still loses whatever wasn't returned yet
  unless results are streamed back incrementally (a bigger design than
  "just wrap it in a subprocess").
- **A file-count-based pre-emptive truncation** — cap files processed,
  not just matches/chars — needs a threshold chosen without a clean way
  to predict where the real crash boundary sits for an arbitrary query/
  language/repo combination.

Recommend: the user picks a direction (or accepts the documented risk
for now) before this is attempted — flagged here as this benchmark's
one open, unresolved finding, not shipped speculatively.

## Reproducing

```bash
# Needs a large, real Python repo checked out locally - Django is what found this.
python3 -c "
from pathlib import Path
from aletheore.ast_pattern import search_ast_pattern
search_ast_pattern(Path('/path/to/django'), 'python', '(try_statement) @try')
"
echo \$?  # 139 = SIGSEGV
```
