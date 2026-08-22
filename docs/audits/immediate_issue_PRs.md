# Immediate issues — ready to turn into PRs

Extracted from `Claude_Audit.md`. These are the items I would open PRs for now:
each is live, each has a reproduction, and each has a fix that follows a pattern
already in the codebase.

Ordered by what I would do first.

| # | area | issue | why now |
|---|---|---|---|
| A | PR mechanism | deleted `-- x ---` line breaks the diff parser | silent false negatives in the paid product |
| B | PR mechanism | `installation_spend_lock` held across LLM work | fires on every push and PR; live incident already attributed to it |
| C | scanner | C# usings are declared in MSBuild, not source | root cause behind #250; complementary fix is cheap |
| D | scanner | 19 full-tree traversals per scan, pruning only after descending | pure win, no behaviour change |

---

## A. A deleted `--` comment line breaks the diff parser, silently dropping findings

**`github-app/scan_worker/flash_review.py:353`** (the pattern)
**`github-app/scan_worker/github_api.py:97`** (the marker it collides with)
**Severity: medium.** Silent false negatives, paid surface.

### What is wrong

`fetch_pr_diff` flattens GitHub's structured response into text with a custom
per-file marker:

```python
parts.append(f"--- {file['filename']} ---\n{patch}")
```

`_diff_valid_lines` recovers those markers with `_FILE_MARKER_RE = r"^--- (.+) ---$"`.

In a unified diff a **deleted line is prefixed with `-`**. So a deleted line whose
content is `-- X ---` arrives as `--- X ---` and matches the file marker exactly.
The marker and the diff body share a namespace.

### Reproduction (run against the real function)

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

Actual:

```
file='db/schema.sql'   valid_lines=[10]     <- should cover ~10-15
file='users table'     valid_lines=[]       <- phantom file invented
```

Everything after the deleted comment is attributed to a phantom file, and because
the marker branch sets `current_line = None`, nothing further is recorded at all.
The real change (`id INT` -> `id BIGINT`) never enters the valid set, so a correct
finding about it is classified `out_of_diff` by `_validate_findings` and dropped -
surfacing to the customer as **"No issues found in this diff."**

### Why it matters more than the trigger suggests

The trigger is not exotic: **any deleted line matching `--`...` ---`**. That covers
SQL, Lua, Haskell and Ada comments, and `--- section ---` dividers in any language.
Deleting a commented section header during a refactor is ordinary, and SQL
migrations are a very plausible PR for a code-review product to be reviewing.

This is the exact failure class `_diff_valid_lines`'s own docstring was written
about - it records a prior incident where a deletion-only hunk "silently
suppressed a whole class of true positives" - reached by a different route.

### Fix

Two options, weaker first:

1. **Only accept a file marker at a file boundary.** `fetch_pr_diff` joins parts
   with `"\n\n"`, so a genuine marker is always preceded by a blank line or starts
   the text. Requiring that makes a mid-hunk collision impossible.
2. **Do not round-trip through text at all.** GitHub's response is already JSON
   with `filename` and `patch` as separate fields; `fetch_pr_diff` flattens them
   and `_diff_valid_lines` re-parses the result. Passing the structure through
   removes the whole class of bug rather than this one instance.

Option 2 is the real fix. Option 1 is the safe minimal change if the text form is
depended on elsewhere (it is also what the LLM prompt consumes, so the text
format itself must stay - only the *parsing* needs to stop guessing).

### Tests to add

- the reproduction above, asserting `db/schema.sql` covers the changed lines and
  no phantom file is created
- a deleted `---- x ---` line (also matches, greedy `(.+)`)
- a *context* line ` -- x ---` (leading space) must still parse normally
- a genuine two-file diff must still split correctly

---

## B. `installation_spend_lock` held across LLM work at five sites

**`github-app/scan_worker/jobs.py`** — five blocks.
**Severity: high.** Live in production.

### What is wrong

`installation_spend_lock` (`scan_worker/db.py:317`) is a Postgres advisory lock
with a **5-second** `ADVISORY_LOCK_TIMEOUT`, intended only to make the spend
check-then-record cycle atomic. A caller that cannot acquire it in 5s does not
queue - it fails with `psycopg.errors.LockNotAvailable`.

Five blocks hold it across work that takes minutes:

| line | function | block | expensive work inside | fires |
|---|---|---|---|---|
| 3074 | `_maybe_update_live_wiki` | 59 lines | `generate_subsystems`, `_attach_wiki_file_pages`, `_store_wiki_generation` | **every push and PR** |
| 3501 | `_maybe_update_live_docs` | 40 lines | `_run_docs_build_for_modules` | **every push and PR** |
| 2936 | `run_live_wiki_full_build_job` | 57 lines | `generate_subsystems`, file pages | on upgrade, per repo |
| 3360 | `run_live_docs_full_build_job` | 42 lines | `_run_docs_build_for_modules` | on upgrade, per repo |
| 1841 | `_fix_suggestion_attachment` | 37 lines | `fetch_file_content` + `simple_completion` | up to `RUNTIME_EVENT_RATE_LIMIT`/hour |

Correct sites for comparison: `run_flash_review_job:1406` (9 lines, check only)
and `_run_flash_review:1526` (6 lines, record only).

### Why this is the PR mechanism's problem too

`_maybe_update_live_wiki` and `_maybe_update_live_docs` are called from
`run_pr_scan_job` (`jobs.py:885`, `895`) and `run_push_scan_job`
(`jobs.py:1052`, `1059`).

The flash-review fix's own comment records the incident as *"confirmed in
production logs while opening 25 PRs on one installation in quick succession."*
Opening a PR enqueues **both** a scan job and a Flash Review. The scan job holds
the lock across a full wiki+docs update; Flash Review only needs it briefly.

**That fix narrowed the victim's hold, not the holder's.** The party actually
occupying the lock for minutes was never touched, so the same burst should still
starve everything else for that installation.

Comparable generation measured on a 513-module repository took roughly 50 minutes,
against a 5-second lock timeout.

### Secondary

- `run_live_wiki_full_build_for_installation_job` (`jobs.py:3005-3018`) fans out
  one build per repo, and scan-worker runs **two replicas** (#242): replica B
  takes repo 2 while replica A holds the lock for repo 1, so repo 2 fails. The
  fan-out is self-defeating.
- At 2936 and 3074 the `except` path and its `set_wiki_build_status(..., "failed")`
  are also inside the lock.

### Fix

The pattern already in the file: hold the lock around the
`_llm_spend_cap_reached` check, release, run the generation, re-acquire briefly
around `record_llm_spend`. `monthly_cap` is read inside the locked block and used
at the record call - the flash-review fix threads that value out, and the same
applies at each site.

**Prioritise 3074 and 3501** (every push) over 2936 and 3360 (upgrade only).

### Tests to add

Mirror `test_flash_review_job_releases_lock_during_the_review_itself`
(`github-app/tests/test_jobs.py:1604`) with a tracking fake lock, one per site,
asserting the lock is not held while the generation callable runs.

---

## C. Scanner: C# usings are declared in MSBuild, not in source

**Complementary to PR #250 (merged).** Not a regression - a gap the merged fix
does not cover.

### What #250 got right, and what it recorded wrong

#250 fixed the empty C# dependency graph by deriving edges from **type
references** (2% -> 77% of files with dependencies, 187 -> 2,140 edges, clusters
474 -> 120). That fix is correct and should stay.

Its commit message attributes the cause to "C# needs no import in the same
namespace." That is true but not the actual reason, and the real one is more
actionable.

### The real cause

`AutoMapper/AutoMapper` has 512 `.cs` files and **230 `using` directives in
total**, 156 of them `System.*`. Its `Directory.Build.props` sets, repo-wide:

```xml
<ImplicitUsings>enable</ImplicitUsings>
...
<ItemGroup>
  <Using Include="System.Reflection"/>
  <Using Include="System.Diagnostics"/>
</ItemGroup>
```

**The usings are declared in the build file, not in source files.** The .NET SDK
injects them at compile time, so source legitimately does not contain them and a
source-only parser sees nothing to resolve. This is the default for `net6.0`+
project templates, so it is the common case for modern C# repositories rather
than an AutoMapper quirk.

### Fix

Parse `<Using Include="..."/>` items out of `Directory.Build.props` and
`*.csproj`, and treat them as file-level imports for every file in that project's
scope. Deterministic, cheap, and it recovers the framework-level edges that type
references cannot see.

### Checked and NOT affected — do not "fix" these

- **Java is healthy.** The obvious follow-up hypothesis ("Java has the same
  implicit-same-package property") was tested against `google/gson` and
  disconfirmed: **75%** of files carry imports at **4.11** edges/module, against
  Python's 90%/3.80. No equivalent change is needed.
- **Namespace flatness is not the cause.** Also tested: AutoMapper has **58
  distinct namespaces** against gson's **23 packages** - more spread out, not
  less. Java simply has no MSBuild-equivalent way to declare imports outside
  source.

| corpus | language | files with imports | edges/module |
|---|---|---|---|
| flask | Python | 90% | 3.80 |
| gson | Java | 75% | 4.11 |
| jq | C | 65% | 1.87 |
| AutoMapper (before #250) | C# | 2% | 0.36 |
| AutoMapper (after #250) | C# | 77% | 4.18 |

---

## D. Scanner walks the whole tree 19 times per scan

**`src/aletheore/scanner/detect.py`** — `_nested_git_roots` (218-234) and six
`rglob` detectors.
**Severity: low (performance).** No behaviour change; pure win.

### What is wrong

Every detector filters `IGNORED_DIRS` **after** the traversal has already
descended into them, and `_nested_git_roots` does not prune at all - despite being
called from `_iter_source_files`, which prunes carefully three lines later.

### Measured on this repository

| traversal | count | cost |
|---|---|---|
| `_detect_docker_compose_services` | 4 rglob | 255 ms |
| `_detect_declared_env_vars` | 4 rglob | 233 ms |
| `_detect_kubernetes_manifests` | 2 rglob (+ YAML-parses every hit) | 203 ms |
| `_detect_migration_directories` | 1 rglob | 137 ms |
| `_detect_terraform_files` | 1 rglob | 59 ms |
| `_detect_helm_charts` | 1 rglob | 58 ms |
| `_nested_git_roots` | 6 unpruned `os.walk` | 232 ms |
| **total** | **19 full-tree traversals** | **~1.18 s** |

**91% of files in this repo sit inside `IGNORED_DIRS`** (8,113 of 8,879), and
`_nested_git_roots` alone is **55% of `_iter_source_files`'s** runtime while
returning zero roots - running six times per scan, once per call site
(`endpoints.py:1220,1228`, `detect.py:278`, `graph.py:2167,2191,2207`).

This scales with exactly the content that gets large. A JS repository with 100k
files under `node_modules` pays all 19 traversals over it.

### Fix

1. Prune inside `_nested_git_roots` - it is an `os.walk`, so
   `dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]` is the change.
2. Cache `_nested_git_roots` per `repo_path`; it cannot change during a scan.
3. Collapse the six `rglob` detectors into one pruned `os.walk` collecting all
   markers in a single pass - they search disjoint filenames in the same tree.

### Tests to add

- assert `_nested_git_roots` does not descend into a `node_modules/` containing a
  `.git` directory
- a regression test pinning that `rglob` does not follow symlinks out of the repo
  (verified true on Python 3.12; **3.13 made this configurable via
  `recurse_symlinks`**, so this should be asserted rather than assumed)
- existing detector outputs must be unchanged - this is a pure performance change

### Checked and NOT a problem

- **Reporting correctness**: every detector does filter `IGNORED_DIRS`, so nothing
  from `node_modules` reaches the evidence. The defect is cost, not output.
- **Symlink escape**: tested with a symlink pointing outside the repo at a
  directory containing `migrations/` - `rglob` returned `[]` on Python 3.12.
- **YAML**: `yaml.safe_load` throughout.

---

## Note on scanner coverage

`detect.py` (549 lines) is now **fully read** - items C and D come from it and
from investigating #250's root cause.

Still not covered: roughly 2,000 of `graph.py`'s 2,392 lines. Given `graph.py` has
produced two real defects in one day, it is where I would look next.
