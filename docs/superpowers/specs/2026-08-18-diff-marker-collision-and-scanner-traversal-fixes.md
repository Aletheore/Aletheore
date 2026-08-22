# Spec: diff file-marker collision fix + scanner traversal consolidation

Two independent, unrelated bugs from `Claude_Audit.md` / `immediate_issue_PRs.md`
(items A and D — B and C from that same audit are already fixed, in commits
`1d490bb` and `21f6283`). Bundled into one spec only because they're both
small, well-bounded, and going to the same implementer. Treat them as two
separate PRs/commits, not one — they touch unrelated files and either can
land without the other.

Both fixes below were re-verified against the actual current code on
2026-08-18 (the audit is from 2026-08-15) — one detail in the audit is
already stale, noted in Part 2.

---

## Part 1: diff file-marker collision (`github-app/scan_worker/flash_review.py`)

### The bug

`github-app/scan_worker/github_api.py:107` (`fetch_pr_diff`) flattens GitHub's
structured per-file diff into one text blob using a custom marker:

```python
parts.append(f"--- {file['filename']} ---\n{patch}")
...
return PRDiff("\n\n".join(parts), tuple(patches))
```

`github-app/scan_worker/flash_review.py:611` recovers those markers:

```python
_FILE_MARKER_RE = re.compile(r"^--- (.+) ---$")
```

used inside `_diff_valid_lines` (`flash_review.py:633`), the function that
determines which line numbers are legitimately "in the diff" — anything
outside this set gets a finding discarded as `out_of_diff` by
`_validate_findings`, silently reported to the customer as "No issues found
in this diff."

In a unified diff, a **deleted line is prefixed with `-`**. So a deleted
line whose content is `-- x ---` arrives in the patch text as `-- x ---`
prefixed with `-`, i.e. literally the text `--- x ---` — which matches
`_FILE_MARKER_RE` exactly. The regex has no way to tell "this is a real
file-boundary marker `fetch_pr_diff` inserted" from "this is diff content
that happens to look like one."

This isn't exotic: any deleted line matching `--...---` triggers it. That
covers SQL/Lua/Haskell/Ada comment syntax and `--- section ---` dividers in
any language. A SQL migration deleting a commented section header is an
entirely ordinary PR for a code-review product to be reviewing.

### Reproduction

```python
from scan_worker.flash_review import _diff_valid_lines

diff = """--- db/schema.sql ---
@@ -10,6 +10,6 @@
 CREATE TABLE users (
--- users table ---
-  id INT,
+  id BIGINT,
   name TEXT
 );"""
print(_diff_valid_lines(diff))
```

Actual (wrong) output: `{'db/schema.sql': {10}, 'users table': set()}` — a
phantom file `'users table'` gets invented from the deleted comment line,
`current_file`/`current_line` get reset onto it, and everything after —
including the real `id INT` → `id BIGINT` change — is attributed to a file
that doesn't exist. The real change never enters `db/schema.sql`'s valid
line set.

### The fix (do this one, not the alternative)

`_diff_valid_lines`'s existing docstring already documents a related prior
incident (a deletion-only hunk suppressing true positives) — this is the
same failure class reached a different way.

The audit lists two options. **Implement the first one — do not attempt the
second:**

1. **Only accept a file marker at a file boundary.** `fetch_pr_diff` joins
   parts with `"\n\n"`, so a genuine marker is always either the very first
   line of the text, or immediately preceded by a blank line. Requiring that
   makes a mid-hunk collision structurally impossible, because a diff hunk
   body is never itself preceded by a blank line inside a real patch (blank
   lines only ever appear where `fetch_pr_diff` put them, between files).
2. ~~Stop round-tripping through text; pass GitHub's `filename`/`patch`
   structure straight through instead of re-parsing flattened text.~~ This
   is the deeper fix, but it's a bigger structural change with wider blast
   radius (the flattened text form is also what the LLM prompt itself
   consumes, so text form has to stay regardless — only the *parsing side*
   needs to change). **Out of scope for this spec.** Do not attempt it.

Implementation for option 1, inside `_diff_valid_lines`
(`flash_review.py:633`): track whether the previous non-consumed line was
blank (or this is the first line), and only treat a `_FILE_MARKER_RE` match
as a real file-boundary marker when that's true. A match on a line that
isn't at a boundary should fall through to the normal content-line handling
(i.e. get added to the current file's valid-line set like any other line,
not treated as a new file marker).

Concretely: `_diff_valid_lines` currently does, per line, in this order —
check `_FILE_MARKER_RE`, then `_HUNK_HEADER_RE`, then blank-line skip, then
"is this a real content line" via `current_file is None or current_line is
None`. You need one more piece of state carried across loop iterations: was
the previous line blank (or are we at the start)? Only branch into the
file-marker case when that's true AND the regex matches. When the regex
matches but we're *not* at a boundary, treat the line exactly like any
other content line (advance `current_line` unless it starts with `-`, like
the existing bottom branch already does) — don't invent special handling
for it.

**Do not touch `_patch_valid_lines`** (the sibling function used when
`patches` — the structured `(filename, patch)` tuples — are available). It
has no marker-collision problem at all, because it never scans for file
markers in the first place; it's called once per already-known filename.
This bug only exists in the fallback branch that parses flattened
`diff_text` when `patches` is `None`.

### Tests to add (`github-app/tests/test_flash_review.py` — check the exact
existing test module/class this file's other `_diff_valid_lines` tests live
in, and match that file)

- The reproduction above, as a real test: assert `db/schema.sql`'s valid
  lines cover the changed line and **no `'users table'` key exists** in the
  result.
- A deleted line reading `---- x ---` (four leading dashes — the regex's
  `(.+)` is greedy, so this also matches `_FILE_MARKER_RE` and must be
  covered too).
- A **context** line (no `+`/`-` prefix) reading ` -- x ---` — note the
  leading space, this one is NOT prefixed with `-`, it's an unchanged
  context line whose real content starts with a space then `-- x ---`. Confirm
  this still parses as ordinary content (this case was already probably fine
  before your change, since it doesn't start with exactly `--- `, but add it
  explicitly to lock the boundary logic in place).
- A genuine two-file diff (two real `--- file ---` markers, each correctly
  preceded by blank line or start-of-text) must still split into two
  separate files with correct line sets — this is the regression check that
  your boundary condition didn't break the normal case.

### Verification (mandatory — do this for real, don't just say tests pass)

Run the new tests plus the full existing `test_flash_review.py` file, and
paste the actual pass/fail counts in your status report. Then also run the
reproduction snippet above directly (`python3 -c "..."` or a scratch script)
against your changed code and show the actual printed output, not just "it
works" — this file has been the site of subtle regex/parsing bugs multiple
times this project's history and a claim without pasted output will not be
trusted as-is.

---

## Part 2: scanner walks the repo tree far more times than it needs to
(`src/aletheore/scanner/detect.py`)

### Correction to the audit first

The audit (2026-08-15) claimed `_nested_git_roots` "does not prune at all."
**That's now false** — as of the current code, `_nested_git_roots`
(`detect.py:220`) already does:

```python
has_nested_git = ".git" in dirnames or ".git" in filenames
dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
if current_dir != repo_path and has_nested_git:
    roots.add(current_dir)
    dirnames[:] = []
```

This already prunes `IGNORED_DIRS` correctly (checking for `.git` before
pruning, exactly so pruning doesn't remove `.git` from `dirnames` before the
check sees it — read the comment above that line in the file for why).
**Do not touch `_nested_git_roots`. It's already fixed. Leave it alone.**

### What's actually still wrong

Six detector functions in `detect.py` each call `repo_path.rglob(...)`
independently, once each, then filter `IGNORED_DIRS` out of the results
*after* the traversal already visited every file:

- `_detect_migration_directories` (`detect.py:401`) — `rglob(name)` per
  entry in `MIGRATION_DIR_NAME_MARKERS = ("migrations",)`
- `_detect_docker_compose_services` (`detect.py:441`) — `rglob(filename)`
  per entry in `COMPOSE_FILE_NAMES = ("docker-compose.yml",
  "docker-compose.yaml", "compose.yml", "compose.yaml")`
- `_detect_kubernetes_manifests` (`detect.py:465`) — `rglob(f"*{ext}")` per
  entry in `YAML_EXTENSIONS = (".yaml", ".yml")`
- `_detect_terraform_files` (`detect.py:491`) — `rglob("*.tf")`
- `_detect_helm_charts` (`detect.py:503`) — `rglob("Chart.yaml")`
- `_detect_declared_env_vars` (`detect.py:515`) — `rglob(marker)` per entry
  in `ENV_FILE_MARKERS = (".env.example", ".env.sample", ".env.template",
  "env.example")`

Every one of these does `rel_parts = candidate.relative_to(repo_path).parts;
if any(part in IGNORED_DIRS for part in rel_parts): continue` — i.e. it
already *walks into* every ignored directory (`node_modules`, `.git`,
`vendor`, whatever `IGNORED_DIRS` contains) and only discards the result
afterward. `Path.rglob()` cannot be pruned mid-traversal — that's a hard
limitation of the API, not a bug in how it's called — so this class of
detector can only be fixed by not using `rglob()` at all.

Measured on this repo: ~1.18s across these traversals combined, with 91% of
files sitting inside `IGNORED_DIRS`. This scales with repo size — a JS repo
with 100k files under `node_modules` pays all of this on every scan.

**Reporting correctness is not affected** — every detector already filters
`IGNORED_DIRS` from its results, so nothing wrong currently reaches the
evidence output. This is a pure performance fix. **The exact same set of
results each detector currently returns must still be returned, byte-for-
byte, after your change.** This is not a rewrite of what each detector
looks for — only of how it walks the filesystem to find candidates.

### The fix

Add one new function, e.g. `_iter_pruned_tree(repo_path: Path)`, that does a
single `os.walk(repo_path, followlinks=False)` pruning `IGNORED_DIRS` from
`dirnames` exactly the way `_iter_source_files` (`detect.py:247`) already
does it — same pattern, same `dirnames[:] = [d for d in dirnames if d not in
IGNORED_DIRS]` line, same `followlinks=False` reasoning (a symlinked
directory shouldn't be descended into). It should yield something each of
the six detectors can consume — e.g. `(Path, is_dir: bool)` for every
file *and* directory entry under the pruned tree, or two separate yields;
pick whichever shape makes each detector's existing per-candidate logic the
least changed.

Then change each of the six detectors to iterate over **one shared call**
to this new function (called once per scan, not once per detector) instead
of calling `repo_path.rglob(...)` itself. Each detector keeps its own
existing matching condition (filename equality, extension check, YAML
parsing, `is_dir()` check, etc.) and its own existing result-building logic
completely unchanged — only the *source of candidate paths* changes, from
"rglob call self-manages its own walk" to "check this path (already
guaranteed to be outside IGNORED_DIRS) against my marker."

Do not try to merge the six detectors' matching *logic* into one giant
function — that's a bigger, riskier rewrite than this bug needs. Keep them
as six separate functions with six separate matching conditions; only make
them share one walk instead of doing six.

The `rel_parts`/`IGNORED_DIRS` check inside each detector becomes dead code
once candidates only ever come from the pruned walk — remove it from each
of the six, since keeping it would silently mask a bug in the shared walk
function instead of surfacing it.

**Do not touch:** `_nested_git_roots` (already fixed, see above),
`_iter_source_files` (already prunes correctly, unrelated to this bug),
`_detect_schema_files` (`detect.py:434`, doesn't walk anything — checks
fixed paths directly, not part of this problem).

### Tests to add

- For each of the six detectors: a test asserting its output on a small
  synthetic repo fixture is **identical before and after** your change —
  literally run the old and new code against the same fixture and diff the
  results, don't just eyeball it. If existing tests for these six functions
  already exist, they should all still pass unmodified; if they don't exist
  yet, add at least one case per detector matching what's already covered
  informally in `detect.py`'s docstrings/comments.
- A case confirming a file inside an `IGNORED_DIRS` directory (e.g. a
  `docker-compose.yml` sitting inside `node_modules/`) is correctly excluded
  by the new shared walk — this is the actual bug's regression test, since
  today's code would have visited it and filtered it after the fact; the
  new code should never visit it at all. (You can verify "never visited" by
  instrumenting/counting, not just by checking the final result excludes
  it — the whole point of this fix is fewer filesystem operations, so the
  test should demonstrate that, not just demonstrate unchanged output.)
- A regression test pinning that the walk does not follow symlinks out of
  the repo (verified true on Python 3.12 for `os.walk(followlinks=False)`;
  the underlying rglob-based code was already confirmed safe here per the
  audit, and `followlinks=False` on `os.walk` is the same guarantee — assert
  it, don't just assume it carries over).

### Verification (mandatory)

Run the new tests plus the full existing scanner test suite, paste actual
pass/fail counts. Then run a real before/after timing comparison on this
actual repo (Veridion) — call the six detectors both ways (old rglob-based
vs new shared-walk-based; you can keep the old implementations around
temporarily under different names just for this one measurement, then
delete them) and report real wall-clock numbers, not an assumption that
it's faster. The audit's ~1.18s baseline was measured on this exact repo —
your after-number should be directly comparable to it.

---

## Scope boundaries (both parts)

- Two separate commits/PRs. Don't combine them into one.
- Don't touch anything not explicitly named above as in-scope.
- Don't attempt option 2 from Part 1 (the structural GitHub-JSON-passthrough
  fix) — out of scope, bigger change, not what's being asked for.
- Don't touch `_nested_git_roots` in Part 2 — already fixed.
- If either fix reveals something else that looks broken while you're in
  there, note it in your status report rather than fixing it inline — flag,
  don't scope-creep.
