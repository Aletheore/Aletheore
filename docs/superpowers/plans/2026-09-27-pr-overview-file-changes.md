# PR overview: standalone "what changed" section (Piece B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fully deterministic, per-changed-file "what changed" section to the top of `run_pr_scan_job`'s existing "Aletheore evidence diff" PR comment, so every PR (every tier, whether or not Flash Review ran or found anything) gets a fast, honest, file-level summary before the findings list.

**Architecture:** Three small, independently testable pure functions feed one wiring change: `github_api.fetch_pr_changed_files_detailed` gets GitHub's own per-file status/line-count/rename data (a sibling of the existing `fetch_pr_changed_files`, not a replacement); `history.summarize_file_changes` diffs `symbols.functions`/`symbols.classes` between the base and head evidence already sitting in memory, keyed by file instead of by finding (the same identity-set-diff idiom `_new_and_resolved` already uses); `blast_radius_summary.compute_blast_radius` is `blast_radius_summary()`'s existing per-target BFS pulled out from behind its markdown rendering, so a second caller can read per-file dependents counts without a second BFS pass. `pr_comment.format_file_overview` renders the three together, and `format_diff_comment` gains one new optional parameter to prepend it. No new LLM call, no new comment, no new job.

**Tech Stack:** Python 3.13/3.14, pytest, httpx (existing GitHub API client), no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-27-pr-comment-presentation-design.md` (committed only on the unmerged `docs/pr-presentation-spec` branch, e7af071f - this plan travels with a copy of its Piece B section in the PR description / commit message since the branch itself won't be merged; executors should still read the full spec there for Piece A's already-shipped context). Piece A (rank badge, top-issue callout, hidden TOON block) is already implemented and merged - PR #843 and #844. This plan is Piece B only.

## Global Constraints

- No change to what Flash Review finds, grounds, verifies, or posts (spec section 1, hard constraint) - this plan touches `run_pr_scan_job` and its comment only, never `run_flash_review_job`.
- Fully deterministic - no new LLM call (spec section 3, "Decided").
- Posted every run, regardless of tier, regardless of whether Flash Review ran, ran cleanly, or ran at all (spec section 3).
- One new leading section on the *existing* "Aletheore evidence diff" comment (`COMMENT_MARKER = "<!-- aletheore-diff -->"` in `src/aletheore/pr_comment.py`) - not a new comment, not a new marker (spec section 3, "Decided" - supersedes an earlier draft's `PR_OVERVIEW_MARKER` idea).
- Truncate past a cap (20 files shown, "+N more" beyond that) using the same honest-truncation idiom `blast_radius_summary.MAX_TARGETS_SHOWN` already uses - never a silent cut (spec section 3, "Rendering").
- A rename must be diffed against its *previous* path in the base commit's evidence, not its current path - GitHub reports a rename as one file object with a `previous_filename` field (spec section 6, explicit risk called out).
- Any failure building this section must never cost the PR its existing findings-diff comment - same fail-open contract every other side computation in `run_pr_scan_job` already follows (verified live in the current source: `_maybe_send_slack_alert`, all three `_maybe_create_*_check_run` calls, and the wiki/docs enqueue block all wrap their own body in `try/except Exception` and log a warning rather than raise).

## Review Focus

1. **Renamed file, no other content change** - GitHub reports `status: "renamed"` with a `previous_filename`; looking it up at its *current* path in the base evidence finds nothing, so a naive diff would report every function in the file as newly added. Task 2's test pins this with an explicit rename fixture.
2. **Changed file that was never a scanned code module in either commit** (a `README.md`, a `.json` config, a binary asset) - must render with no symbol-change phrase at all, never a fabricated "no symbol changes" or a false "removed". Task 2's `has_module_data` field and Task 4's renderer both handle this; a test in each pins it.
3. **`fetch_pr_changed_files_detailed` (or anything downstream of it) raises** - a GitHub API hiccup, a malformed compare response - must degrade to an empty overview section, not crash `run_pr_scan_job` or skip posting the real evidence-diff comment underneath it. Task 5's test pins this.
4. **More than 20 changed files** - must show an honest "+N more changed file(s)" line, never silently truncate without saying so. Task 4's test pins this.
5. **A PR with real changed files but zero new findings** - `format_diff_comment`'s existing "No new secrets, vulnerabilities, or layer violations. ✅" fallback message currently fires on a hardcoded `len(body) <= 3` check; prepending a non-empty file-overview section unconditionally breaks that check (the body is never `<= 3` long again once Piece B ships, silently killing the fallback message on every clean PR). Task 4 replaces the magic number with a length snapshot taken after the file-overview and caveats are already in place, and a test pins that the fallback message still appears alongside a real file overview.

---

### Task 1: `fetch_pr_changed_files_detailed` - per-file status, line counts, rename data

**Files:**
- Modify: `github-app/scan_worker/github_api.py` (new function, placed directly after the existing `fetch_pr_changed_files` at line 539-572)
- Test: `github-app/tests/test_github_api.py` (new tests, placed directly after the existing `fetch_pr_changed_files` tests at line ~711-780)

**Interfaces:**
- Consumes: nothing new - same `httpx.Client`/token/`repo_full_name`/`base_ref`/`head_ref` shape every other `fetch_pr_*` function in this file already takes, and the same `/repos/{repo_full_name}/compare/{base_ref}...{head_ref}` endpoint `fetch_pr_changed_files` and `fetch_pr_diff` already call independently (this file's established convention is a dedicated function per need, each making its own call - see `fetch_pr_context` vs `fetch_pr_title`, both separately hitting `/pulls/{pr_number}` - not a shared low-level helper).
- Produces: `fetch_pr_changed_files_detailed(client, token, repo_full_name, base_ref, head_ref, ignored_paths=()) -> list[dict]`, each dict `{"filename": str, "status": str, "additions": int, "deletions": int, "previous_filename": str | None}` - consumed by Task 2's `summarize_file_changes` and wired in by Task 5.

- [ ] **Step 1: Write the failing tests**

```python
# github-app/tests/test_github_api.py, placed after test_fetch_pr_changed_files_logs_when_compare_api_hits_the_300_file_cap

def test_fetch_pr_changed_files_detailed_returns_status_and_line_counts(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "files": [
                    {"filename": "app.py", "status": "modified", "additions": 5, "deletions": 2},
                    {"filename": "new_module.py", "status": "added", "additions": 40, "deletions": 0},
                ]
            }

    class FakeClient:
        def get(self, url, headers=None):
            return FakeResponse()

    result = fetch_pr_changed_files_detailed(FakeClient(), "tok", "octocat/hello-world", "base", "head")

    assert result == [
        {
            "filename": "app.py",
            "status": "modified",
            "additions": 5,
            "deletions": 2,
            "previous_filename": None,
        },
        {
            "filename": "new_module.py",
            "status": "added",
            "additions": 40,
            "deletions": 0,
            "previous_filename": None,
        },
    ]


def test_fetch_pr_changed_files_detailed_carries_previous_filename_for_a_rename(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "files": [
                    {
                        "filename": "src/new_name.py",
                        "status": "renamed",
                        "additions": 1,
                        "deletions": 1,
                        "previous_filename": "src/old_name.py",
                    }
                ]
            }

    class FakeClient:
        def get(self, url, headers=None):
            return FakeResponse()

    result = fetch_pr_changed_files_detailed(FakeClient(), "tok", "octocat/hello-world", "base", "head")

    assert result[0]["previous_filename"] == "src/old_name.py"


def test_fetch_pr_changed_files_detailed_excludes_ignored_paths(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "files": [
                    {"filename": "app.py", "status": "modified", "additions": 1, "deletions": 1},
                    {"filename": "vendor/lib.js", "status": "modified", "additions": 1, "deletions": 1},
                ]
            }

    class FakeClient:
        def get(self, url, headers=None):
            return FakeResponse()

    result = fetch_pr_changed_files_detailed(
        FakeClient(), "tok", "octocat/hello-world", "base", "head", ignored_paths=["vendor/"]
    )

    assert [f["filename"] for f in result] == ["app.py"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_github_api.py -k fetch_pr_changed_files_detailed -v`
Expected: FAIL with `ImportError` / `NameError: name 'fetch_pr_changed_files_detailed' is not defined`

- [ ] **Step 3: Add the import in the test file and implement the function**

Add `fetch_pr_changed_files_detailed` to the existing import line for `fetch_pr_changed_files` at the top of `test_github_api.py`.

In `github_api.py`, directly after the existing `fetch_pr_changed_files` function (ends at line 572):

```python
def fetch_pr_changed_files_detailed(
    client: httpx.Client,
    token: str,
    repo_full_name: str,
    base_ref: str,
    head_ref: str,
    ignored_paths: list[str] = (),
) -> list[dict]:
    """Like `fetch_pr_changed_files`, but keeps GitHub's own per-file status,
    added/deleted line counts, and (for a rename) `previous_filename` instead
    of discarding everything but the filename. A separate function rather
    than widening `fetch_pr_changed_files`'s own return shape - that
    function has three existing call sites all expecting a plain
    `list[str]` (see `app_server/webhooks/pull_request.py` and two call
    sites in `scan_worker/jobs.py`); this one is for a fourth, new caller
    (the PR file-overview section) that needs the richer shape, matching
    this file's own established pattern of a dedicated function per need
    rather than reshaping a function other callers already depend on.
    """
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
    }
    response = client.get(
        f"/repos/{repo_full_name}/compare/{base_ref}...{head_ref}",
        headers=headers,
    )
    response.raise_for_status()
    compare_files = response.json().get("files", [])
    if len(compare_files) >= GITHUB_COMPARE_FILES_HARD_CAP:
        logger.warning(
            "fetch_pr_changed_files_detailed: compare %s...%s for %s hit the compare API's "
            "%d-file cap; changed files beyond this are invisible to this review",
            base_ref, head_ref, repo_full_name, GITHUB_COMPARE_FILES_HARD_CAP,
        )
    results = [
        {
            "filename": file["filename"],
            "status": file.get("status", "modified"),
            "additions": file.get("additions", 0),
            "deletions": file.get("deletions", 0),
            "previous_filename": file.get("previous_filename"),
        }
        for file in compare_files
    ]
    if ignored_paths:
        results = [r for r in results if not is_ignored(r["filename"], ignored_paths)]
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_github_api.py -k fetch_pr_changed_files_detailed -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add github-app/scan_worker/github_api.py github-app/tests/test_github_api.py
git commit -m "feat: fetch per-file status/line-counts/rename data for the PR file overview"
```

---

### Task 2: `summarize_file_changes` - per-file symbol diff

**Files:**
- Modify: `src/aletheore/history.py` (new function, placed directly after `_new_and_resolved` at line 92-99, before `_endpoint_block`)
- Test: `src/tests/test_history.py` (new tests, placed after the existing `compute_diff` tests, before `test_to_sarif_has_valid_top_level_shape_with_no_findings` at line 523)

**Interfaces:**
- Consumes: `old`/`new` evidence dicts (already in memory in `run_pr_scan_job` - the same two dicts already passed to `compute_diff`), and `changed_files: list[dict]` in Task 1's exact `fetch_pr_changed_files_detailed` shape (`filename`/`status`/`additions`/`deletions`/`previous_filename`).
- Produces: `summarize_file_changes(old, new, changed_files) -> list[dict]`, one dict per input file, in the same order: `{"path": str, "status": str, "additions": int, "deletions": int, "previous_path": str | None, "functions_added": list[str], "functions_removed": list[str], "classes_added": list[str], "classes_removed": list[str], "has_module_data": bool}` - consumed by Task 4's `format_file_overview` (after Task 5 merges in a `"dependents_count"` key) and wired in by Task 5.

- [ ] **Step 1: Write the failing tests**

```python
# src/tests/test_history.py, add near the top alongside base_evidence()

def _module(path, functions=(), classes=()):
    return {
        "path": path,
        "symbols": {
            "functions": [{"name": n, "start_line": 1, "end_line": 2} for n in functions],
            "classes": [{"name": n, "start_line": 1, "end_line": 2} for n in classes],
        },
    }


def _evidence_with_modules(modules):
    return {"repository": {"modules": modules}}


# --- placed after the existing compute_diff tests, before the SARIF tests ---

def test_summarize_file_changes_detects_added_and_removed_functions():
    old = _evidence_with_modules([_module("app.py", functions=["a", "b"])])
    new = _evidence_with_modules([_module("app.py", functions=["a", "c"])])
    changed_files = [{"filename": "app.py", "status": "modified", "additions": 3, "deletions": 1}]

    rows = summarize_file_changes(old, new, changed_files)

    assert rows == [{
        "path": "app.py",
        "status": "modified",
        "additions": 3,
        "deletions": 1,
        "previous_path": None,
        "functions_added": ["c"],
        "functions_removed": ["b"],
        "classes_added": [],
        "classes_removed": [],
        "has_module_data": True,
    }]


def test_summarize_file_changes_new_file_reports_only_additions():
    old = _evidence_with_modules([])
    new = _evidence_with_modules([_module("new_mod.py", functions=["f1", "f2"])])
    changed_files = [{"filename": "new_mod.py", "status": "added", "additions": 20, "deletions": 0}]

    rows = summarize_file_changes(old, new, changed_files)

    assert rows[0]["functions_added"] == ["f1", "f2"]
    assert rows[0]["functions_removed"] == []
    assert rows[0]["has_module_data"] is True


def test_summarize_file_changes_removed_file_reports_only_removals():
    old = _evidence_with_modules([_module("gone.py", functions=["f1"])])
    new = _evidence_with_modules([])
    changed_files = [{"filename": "gone.py", "status": "removed", "additions": 0, "deletions": 15}]

    rows = summarize_file_changes(old, new, changed_files)

    assert rows[0]["functions_removed"] == ["f1"]
    assert rows[0]["functions_added"] == []


def test_summarize_file_changes_no_change_reports_empty_diffs():
    old = _evidence_with_modules([_module("stable.py", functions=["f1"])])
    new = _evidence_with_modules([_module("stable.py", functions=["f1"])])
    changed_files = [{"filename": "stable.py", "status": "modified", "additions": 1, "deletions": 1}]

    rows = summarize_file_changes(old, new, changed_files)

    assert rows[0]["functions_added"] == []
    assert rows[0]["functions_removed"] == []
    assert rows[0]["has_module_data"] is True


def test_summarize_file_changes_rename_diffs_against_the_previous_path():
    # The real bug this pins: looking up a renamed file at its NEW path in
    # `old` evidence finds nothing there, and would misreport the whole
    # function as freshly added even though only the filename changed.
    old = _evidence_with_modules([_module("src/old_name.py", functions=["f1", "f2"])])
    new = _evidence_with_modules([_module("src/new_name.py", functions=["f1", "f2"])])
    changed_files = [{
        "filename": "src/new_name.py",
        "status": "renamed",
        "additions": 0,
        "deletions": 0,
        "previous_filename": "src/old_name.py",
    }]

    rows = summarize_file_changes(old, new, changed_files)

    assert rows[0]["functions_added"] == []
    assert rows[0]["functions_removed"] == []
    assert rows[0]["previous_path"] == "src/old_name.py"


def test_summarize_file_changes_non_code_file_has_no_module_data():
    old = _evidence_with_modules([])
    new = _evidence_with_modules([])
    changed_files = [{"filename": "README.md", "status": "modified", "additions": 4, "deletions": 1}]

    rows = summarize_file_changes(old, new, changed_files)

    assert rows[0]["has_module_data"] is False
    assert rows[0]["functions_added"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src && python -m pytest tests/test_history.py -k summarize_file_changes -v`
Expected: FAIL with `ImportError: cannot import name 'summarize_file_changes'`

- [ ] **Step 3: Add the import and implement the function**

Add `summarize_file_changes` to `test_history.py`'s existing `from aletheore.history import ...` line at the top of the file.

In `history.py`, directly after `_new_and_resolved` (line 99), before `_endpoint_block`:

```python
def _module_symbol_names(module: dict | None) -> tuple[set[str], set[str]]:
    """(function_names, class_names) declared in `module`'s scan evidence,
    or two empty sets when `module` is None - the file didn't exist as a
    scanned code module at this commit, either because it never existed
    or because aletheore's scanner doesn't parse it as code (docs,
    config, binary assets)."""
    if module is None:
        return set(), set()
    symbols = module.get("symbols", {})
    functions = {e["name"] for e in symbols.get("functions", []) if e.get("name")}
    classes = {e["name"] for e in symbols.get("classes", []) if e.get("name")}
    return functions, classes


def summarize_file_changes(old: dict, new: dict, changed_files: list[dict]) -> list[dict]:
    """Per-file function/class-level summary for every file GitHub's
    compare API reports as changed between the commits `old` and `new`
    were scanned at.

    `changed_files` is `github_api.fetch_pr_changed_files_detailed`'s own
    shape: each dict needs "filename", "status", "additions", "deletions",
    and (for a rename) "previous_filename". A renamed file is looked up at
    its *previous* path in `old` and its current path in `new` - looking
    both up at the current path would misreport a pure rename's whole
    function/class set as freshly added, since `old`'s module list was
    never indexed at the new path.

    Returns one dict per input file, in the same order: {"path", "status",
    "additions", "deletions", "previous_path", "functions_added",
    "functions_removed", "classes_added", "classes_removed",
    "has_module_data"}. `has_module_data` is False only when neither
    commit's evidence scanned this path as a code module at all (a
    non-code file, or one aletheore's scanner doesn't parse) - a renderer
    needs this to tell "nothing changed" apart from "never had symbols to
    diff in the first place" instead of reporting both identically.
    """
    old_modules = {m["path"]: m for m in old.get("repository", {}).get("modules", [])}
    new_modules = {m["path"]: m for m in new.get("repository", {}).get("modules", [])}

    rows = []
    for file in changed_files:
        path = file["filename"]
        previous_path = file.get("previous_filename")
        old_lookup_path = previous_path if previous_path else path
        old_module = old_modules.get(old_lookup_path)
        new_module = new_modules.get(path)

        old_functions, old_classes = _module_symbol_names(old_module)
        new_functions, new_classes = _module_symbol_names(new_module)

        rows.append({
            "path": path,
            "status": file.get("status", "modified"),
            "additions": file.get("additions", 0),
            "deletions": file.get("deletions", 0),
            "previous_path": previous_path,
            "functions_added": sorted(new_functions - old_functions),
            "functions_removed": sorted(old_functions - new_functions),
            "classes_added": sorted(new_classes - old_classes),
            "classes_removed": sorted(old_classes - new_classes),
            "has_module_data": old_module is not None or new_module is not None,
        })
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src && python -m pytest tests/test_history.py -k summarize_file_changes -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/aletheore/history.py src/tests/test_history.py
git commit -m "feat: per-file function/class diff for the PR file overview"
```

---

### Task 3: Expose `blast_radius_summary`'s per-file computation

**Files:**
- Modify: `github-app/scan_worker/blast_radius_summary.py` (extract the existing loop into a new `compute_blast_radius` function; `blast_radius_summary` calls it and renders exactly as before)
- Test: `github-app/tests/test_blast_radius_summary.py` (new test for `compute_blast_radius`; every existing test in this file must still pass unchanged, since this is a pure refactor of `blast_radius_summary`'s internals)

**Interfaces:**
- Consumes: the same `evidence: dict` / `changed_files: list[str]` `blast_radius_summary` already takes.
- Produces: `compute_blast_radius(evidence, changed_files) -> dict`: `{"per_target": dict[str, list[str]], "direct": set[str], "indirect": set[str], "truncated": bool, "analysed": int}` - consumed by Task 5 (reads `per_target` to get each changed file's direct-dependents count without a second BFS pass).

- [ ] **Step 1: Write the failing test**

This file's own `_evidence(edges)` helper (already at the top of `test_blast_radius_summary.py`) maps a module to the modules that import it - reuse it exactly as every existing test in this file does:

```python
def test_compute_blast_radius_exposes_per_file_direct_and_indirect_dependents():
    evidence = _evidence({"core.py": ["svc.py", "cli.py"], "svc.py": ["api.py"]})
    result = compute_blast_radius(evidence, ["core.py"])

    assert result["analysed"] == 1
    assert result["per_target"] == {"core.py": ["cli.py", "svc.py"]}
    assert result["direct"] == {"cli.py", "svc.py"}
    assert result["indirect"] == {"api.py"}
    assert result["truncated"] is False


def test_compute_blast_radius_excludes_files_already_in_the_pr():
    evidence = _evidence({"core.py": ["svc.py", "cli.py"]})
    result = compute_blast_radius(evidence, ["core.py", "svc.py"])

    assert result["per_target"] == {"core.py": ["cli.py"]}
    assert "svc.py" not in result["direct"]


def test_compute_blast_radius_skips_an_unknown_changed_file_and_reports_zero_analysed():
    evidence = _evidence({"lib.py": ["app.py"]})
    result = compute_blast_radius(evidence, ["README.md"])

    assert result["analysed"] == 0
    assert result["per_target"] == {}
```

Add the import for `compute_blast_radius` to this test file's existing `from scan_worker.blast_radius_summary import blast_radius_summary` line.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd github-app && python -m pytest tests/test_blast_radius_summary.py -k compute_blast_radius -v`
Expected: FAIL with `ImportError: cannot import name 'compute_blast_radius'`

- [ ] **Step 3: Extract the computation, keep rendering identical**

Replace the body of `blast_radius_summary.py` from `def blast_radius_summary` onward with:

```python
def compute_blast_radius(evidence: dict, changed_files: list[str]) -> dict:
    """Direct/transitive dependents of each of `changed_files`, from the
    import graph of `evidence` (must be scanned at the commit being
    described - see this module's own docstring). Pulled out of
    `blast_radius_summary` so a second caller (the PR file-overview
    section, `pr_comment.format_file_overview` via `jobs.py`) can read
    each file's own direct-dependents count without re-running this same
    BFS a second time.

    Returns {"per_target": {path: [direct dependent paths]}, "direct":
    set[str], "indirect": set[str], "truncated": bool, "analysed": int}.
    `per_target` only carries a path when it has at least one direct
    dependent NOT already in `changed_files` (dependents already under
    review don't need calling out); `direct`/`indirect` are the pooled
    sets across every analysed target, matching what `blast_radius_
    summary`'s own rendering already reported before this refactor.
    """
    modules = evidence.get("repository", {}).get("modules") or []
    known = {m.get("path") for m in modules if m.get("path")}
    changed = set(changed_files)

    per_target: dict[str, list[str]] = {}
    direct: set[str] = set()
    indirect: set[str] = set()
    truncated = False
    analysed = 0
    for path in changed_files:
        if path not in known:
            continue
        try:
            radius = find_blast_radius(evidence, Path("."), path)
        except Exception:  # noqa: BLE001 - a malformed module entry must never block the review
            logger.debug("blast radius skipped %s: malformed module entry", path, exc_info=True)
            continue
        analysed += 1
        direct_here = [p for p in radius["direct_dependents"] if p not in changed]
        indirect_here = [p for p in radius["transitive_dependents"] if p not in changed]
        direct.update(direct_here)
        indirect.update(indirect_here)
        truncated = truncated or radius["direct_dependents_truncated"] or radius["transitive_dependents_truncated"]
        if direct_here:
            per_target[path] = sorted(direct_here)
    indirect -= direct
    return {
        "per_target": per_target,
        "direct": direct,
        "indirect": indirect,
        "truncated": truncated,
        "analysed": analysed,
    }


def blast_radius_summary(evidence: dict | None, changed_files: list[str]) -> str:
    if not evidence:
        return ""
    result = compute_blast_radius(evidence, changed_files)
    if not result["analysed"]:
        return ""

    direct, indirect = result["direct"], result["indirect"]
    if not direct and not indirect:
        return (
            "\n\n_Blast radius: no other file in the repo imports the changed file(s), "
            "per this commit's import graph._"
        )

    per_target, truncated = result["per_target"], result["truncated"]
    total = len(direct) + len(indirect)
    lines = [
        f"\n\n<details><summary>Blast radius: {total} other file(s) depend on what this PR changes "
        f"({len(direct)} directly, {len(indirect)} indirectly)</summary>\n",
        "Computed from the import graph of this exact commit, not guessed by a model. "
        "It sees imports only, so dynamic imports, reflection and non-code references are not counted.\n",
    ]
    ordered = sorted(per_target.items(), key=lambda item: -len(item[1]))
    for path, dependents in ordered[:MAX_TARGETS_SHOWN]:
        lines.append(f"- `{path}` is imported by {_names(dependents, MAX_DEPENDENTS_PER_TARGET)}")
    if len(ordered) > MAX_TARGETS_SHOWN:
        lines.append(f"- ...and {len(ordered) - MAX_TARGETS_SHOWN} more changed file(s) with dependents")
    if indirect:
        lines.append(f"\nIndirectly affected: {_names(sorted(indirect), MAX_INDIRECT_SHOWN)}")
    if truncated:
        lines.append("\n_The graph is large; this list is capped and not exhaustive._")
    lines.append("\n</details>")
    return "\n".join(lines)
```

This is a pure extraction: `blast_radius_summary`'s rendering logic and output are byte-for-byte identical to before, just reading its inputs from `compute_blast_radius`'s return value instead of local loop variables.

- [ ] **Step 4: Run tests to verify everything passes**

Run: `cd github-app && python -m pytest tests/test_blast_radius_summary.py -v`
Expected: PASS, every existing test plus the new `compute_blast_radius` test (this confirms the extraction changed nothing observable about `blast_radius_summary` itself)

- [ ] **Step 5: Commit**

```bash
git add github-app/scan_worker/blast_radius_summary.py github-app/tests/test_blast_radius_summary.py
git commit -m "refactor: expose blast_radius_summary's per-file computation"
```

---

### Task 4: Render the file overview and wire it into `format_diff_comment`

**Files:**
- Modify: `src/aletheore/pr_comment.py` (new `format_file_overview` function; `format_diff_comment` gains a `file_overview` parameter)
- Test: `src/tests/test_pr_comment.py` (new tests)

**Interfaces:**
- Consumes: Task 2's `summarize_file_changes` rows, each with an additional `"dependents_count": int` key merged in by Task 5 (this task's renderer reads `row.get("dependents_count", 0)`, so it works whether or not that key is present - keeps this function testable on its own without Task 5's wiring).
- Produces: `format_file_overview(rows: list[dict]) -> str` (empty string when `rows` is empty); `format_diff_comment(diff: dict, file_overview: str = "") -> str` - both consumed by Task 5.

- [ ] **Step 1: Write the failing tests**

```python
# src/tests/test_pr_comment.py, add after the existing imports

from aletheore.pr_comment import format_file_overview


def _row(path, status="modified", additions=0, deletions=0, previous_path=None,
         functions_added=(), functions_removed=(), classes_added=(), classes_removed=(),
         has_module_data=True, dependents_count=0):
    return {
        "path": path,
        "status": status,
        "additions": additions,
        "deletions": deletions,
        "previous_path": previous_path,
        "functions_added": list(functions_added),
        "functions_removed": list(functions_removed),
        "classes_added": list(classes_added),
        "classes_removed": list(classes_removed),
        "has_module_data": has_module_data,
        "dependents_count": dependents_count,
    }


def test_format_file_overview_empty_rows_returns_empty_string():
    assert format_file_overview([]) == ""


def test_format_file_overview_shows_path_status_and_line_counts():
    body = format_file_overview([_row("app.py", additions=5, deletions=2)])
    assert "`app.py`" in body
    assert "modified" in body
    assert "+5/-2" in body


def test_format_file_overview_shows_function_symbol_changes():
    body = format_file_overview([_row("app.py", functions_added=["c"], functions_removed=["b"])])
    assert "+1 function" in body
    assert "-1 function" in body


def test_format_file_overview_new_file_says_new_file_not_a_diff():
    body = format_file_overview([_row("new.py", status="added", functions_added=["f1", "f2"])])
    assert "new file, 2 symbols" in body


def test_format_file_overview_removed_file_says_removed():
    body = format_file_overview([_row("gone.py", status="removed", functions_removed=["f1"])])
    lines = [line for line in body.splitlines() if "gone.py" in line]
    assert lines and "removed" in lines[0]


def test_format_file_overview_non_code_file_has_no_symbol_phrase():
    body = format_file_overview([_row("README.md", has_module_data=False)])
    line = next(line for line in body.splitlines() if "README.md" in line)
    assert "function" not in line and "class" not in line


def test_format_file_overview_shows_renamed_from():
    body = format_file_overview([_row("src/new_name.py", status="renamed", previous_path="src/old_name.py")])
    assert "renamed from `src/old_name.py`" in body


def test_format_file_overview_shows_dependents_count():
    body = format_file_overview([_row("lib.py", dependents_count=3)])
    assert "3 dependents" in body


def test_format_file_overview_truncates_past_20_files_honestly():
    rows = [_row(f"file_{i}.py") for i in range(25)]
    body = format_file_overview(rows)
    assert "file_19.py" in body
    assert "file_20.py" not in body
    assert "+5 more changed file(s)" in body


def test_format_file_overview_header_states_deterministic_and_always_posted():
    body = format_file_overview([_row("app.py")])
    assert "Deterministic" in body


# --- format_diff_comment integration ---

def test_format_diff_comment_prepends_file_overview_right_after_the_header():
    diff = _empty_diff()
    overview = format_file_overview([_row("app.py")])
    body = format_diff_comment(diff, file_overview=overview)
    lines = body.splitlines()
    assert lines[0] == COMMENT_MARKER
    assert lines[1] == "### 🔍 Aletheore evidence diff"
    assert any("What changed" in line for line in lines[2:5])
    assert body.index("What changed") < body.index("No new secrets")


def test_format_diff_comment_still_says_nothing_to_report_alongside_a_file_overview():
    # The real bug this pins: prepending a non-empty file_overview must not
    # silently break the "nothing new" fallback message by changing what
    # body's length looks like at the point that check runs.
    diff = _empty_diff()
    overview = format_file_overview([_row("app.py")])
    body = format_diff_comment(diff, file_overview=overview)
    assert "No new secrets, vulnerabilities, or layer violations" in body


def test_format_diff_comment_with_no_file_overview_is_unchanged():
    body = format_diff_comment(_empty_diff())
    assert "What changed" not in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src && python -m pytest tests/test_pr_comment.py -v`
Expected: FAIL with `ImportError: cannot import name 'format_file_overview'`

- [ ] **Step 3: Implement the renderer and wire the new parameter**

In `pr_comment.py`, add after the module-level `COMMENT_MARKER`:

```python
FILE_OVERVIEW_TRUNCATION_CAP = 20


def _symbol_change_phrase(row: dict) -> str:
    if row["status"] == "removed":
        return "removed"
    if not row["has_module_data"]:
        return ""
    if row["status"] == "added":
        added = len(row["functions_added"]) + len(row["classes_added"])
        if not added:
            return "new file"
        return f"new file, {added} symbol{'s' if added != 1 else ''}"

    parts = []
    if row["functions_added"]:
        n = len(row["functions_added"])
        parts.append(f"+{n} function{'s' if n != 1 else ''}")
    if row["functions_removed"]:
        n = len(row["functions_removed"])
        parts.append(f"-{n} function{'s' if n != 1 else ''}")
    if row["classes_added"]:
        n = len(row["classes_added"])
        parts.append(f"+{n} class{'es' if n != 1 else ''}")
    if row["classes_removed"]:
        n = len(row["classes_removed"])
        parts.append(f"-{n} class{'es' if n != 1 else ''}")
    return ", ".join(parts)


def format_file_overview(rows: list[dict]) -> str:
    """Render `history.summarize_file_changes`'s per-file rows (with a
    caller-merged "dependents_count" key - see `blast_radius_summary.
    compute_blast_radius`) as the leading section of the PR evidence-diff
    comment. Empty string when `rows` is empty (nothing GitHub reports as
    changed - `run_pr_scan_job` never calls this with an empty list in
    practice, but an empty result must never fabricate a section header
    over nothing). Otherwise always non-empty: this section is fully
    deterministic and posts on every run, regardless of tier or whether
    Flash Review ran at all.
    """
    if not rows:
        return ""
    lines = [
        "**What changed**",
        "_Deterministic, computed from this commit's real scan and import graph - "
        "posted on every run, whether or not Flash Review found anything._",
        "",
    ]
    shown = rows[:FILE_OVERVIEW_TRUNCATION_CAP]
    for row in shown:
        parts = [f"`{row['path']}`"]
        if row.get("previous_path"):
            parts.append(f"(renamed from `{row['previous_path']}`)")
        parts.append(row["status"])
        if row["additions"] or row["deletions"]:
            parts.append(f"+{row['additions']}/-{row['deletions']}")
        phrase = _symbol_change_phrase(row)
        if phrase:
            parts.append(phrase)
        dependents = row.get("dependents_count", 0)
        if dependents:
            parts.append(f"{dependents} dependent{'s' if dependents != 1 else ''}")
        lines.append("- " + " · ".join(parts))
    if len(rows) > FILE_OVERVIEW_TRUNCATION_CAP:
        lines.append(f"- +{len(rows) - FILE_OVERVIEW_TRUNCATION_CAP} more changed file(s)")
    lines.append("")
    return "\n".join(lines)
```

Then change `format_diff_comment`'s signature and the "nothing new" check:

```python
def format_diff_comment(diff: dict, file_overview: str = "") -> str:
    """Return the markdown body for an ``aletheore.history.compute_diff`` result.

    `file_overview` is Piece B's per-file "what changed" section (see
    `format_file_overview`) - prepended, when non-empty, right after the
    header and before everything else, per the PR-comment-presentation
    design's "Decided" note: one leading section on this same comment,
    not a new comment type.
    """

    body = [COMMENT_MARKER, "### 🔍 Aletheore evidence diff", ""]

    if file_overview:
        body.append(file_overview)

    for caveat in diff.get("caveats", []):
        body.append(f"> ⚠️ {caveat}")
    if diff.get("caveats"):
        body.append("")

    # Snapshot taken here, not a hardcoded "3" - the file-overview section
    # above (and caveats, just above this line) are real content that
    # existed before Piece B too, and must not count toward "nothing new
    # to report" below. A magic-number length check would silently stop
    # firing the "No new secrets..." fallback on every PR once the file
    # overview became unconditional.
    pre_findings_len = len(body)

    body += _bullets(
        "Secrets",
        diff.get("secrets", {}),
        lambda f: f"`{f.get('path')}:{f.get('line')}` ({f.get('pattern')})"
        + _secret_suffix(f),
    )
    body += _bullets(
        "Secrets in git history",
        diff.get("history_secrets", {}),
        lambda f: f"`{f.get('path')}` in {str(f.get('commit'))[:8]} ({f.get('pattern')})"
        + _secret_suffix(f),
    )
    body += _bullets(
        "Dependency vulnerabilities",
        diff.get("vulnerabilities", {}),
        lambda f: (
            f"{f.get('package')} {f.get('installed_version')} - "
            f"{f.get('advisory_id')} ({f.get('ecosystem')})"
        ),
    )
    body += _bullets(
        "Layer violations",
        diff.get("layer_violations", {}),
        lambda f: f"`{f.get('from')}` -> `{f.get('to')}`: {f.get('reason')}",
    )

    deltas = diff.get("aggregate_deltas", {})
    if any(deltas.get(k, 0) for k in ("module_count", "dependency_graph_edge_count", "total_commits")):
        body.append("**Aggregate deltas**")
        body.append(f"- Modules: {deltas.get('module_count', 0):+d}")
        body.append(f"- Dependency graph edges: {deltas.get('dependency_graph_edge_count', 0):+d}")
        body.append(f"- Commits: {deltas.get('total_commits', 0)}")
        body.append("")

    if len(body) <= pre_findings_len:
        body.append("No new secrets, vulnerabilities, or layer violations. ✅")

    return "\n".join(body)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src && python -m pytest tests/test_pr_comment.py -v`
Expected: PASS, every new test plus all pre-existing `test_pr_comment.py` tests still green

- [ ] **Step 5: Commit**

```bash
git add src/aletheore/pr_comment.py src/tests/test_pr_comment.py
git commit -m "feat: render the PR file-overview section, prepend it to the evidence-diff comment"
```

---

### Task 5: Wire it into `run_pr_scan_job`

**Files:**
- Modify: `github-app/scan_worker/jobs.py` (imports at lines 36-37, 63, 146; `run_pr_scan_job` body around line 1349-1352)
- Test: `github-app/tests/test_jobs.py` (new tests, placed after `test_run_pr_scan_job_excludes_a_dismissed_secret_from_the_pr_comment` at line 936-982)

**Interfaces:**
- Consumes: Task 1's `fetch_pr_changed_files_detailed`, Task 2's `summarize_file_changes`, Task 3's `compute_blast_radius`, Task 4's `format_file_overview` and `format_diff_comment(diff, file_overview=...)`.
- Produces: nothing new for later tasks - this is the last task.

- [ ] **Step 1: Write the failing tests**

```python
# github-app/tests/test_jobs.py, placed after test_run_pr_scan_job_excludes_a_dismissed_secret_from_the_pr_comment

def test_run_pr_scan_job_posts_a_file_overview_section(bare_repo_with_two_commits, monkeypatch):
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    posted = {}

    def fake_upsert(client, token, repo_full_name, pr_number, body):
        posted["body"] = body

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"secret": set(), "vulnerability": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", fake_upsert)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_send_slack_alert", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_create_check_run", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.fetch_pr_changed_files_detailed",
        lambda *a, **k: [{
            "filename": "app.py", "status": "modified", "additions": 1, "deletions": 1,
            "previous_filename": None,
        }],
    )

    run_pr_scan_job(
        installation_id=1, repo_full_name="octocat/hello-world", pr_number=7,
        base_sha=base_sha, head_sha=head_sha,
    )

    assert "What changed" in posted["body"]
    assert "`app.py`" in posted["body"]


def test_run_pr_scan_job_posts_a_file_overview_even_with_no_new_findings(bare_repo_with_two_commits, monkeypatch):
    # Piece B's whole point: this section must post even when Flash Review
    # (a completely separate job) found nothing, or the diff comment would
    # otherwise have nothing but "No new secrets..." to show.
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    posted = {}

    def fake_upsert(client, token, repo_full_name, pr_number, body):
        posted["body"] = body

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        # Dismiss the fixture's own planted secret so this run really has
        # zero new findings, exercising the "nothing new" + file-overview
        # combination end to end.
        lambda *a, **k: {"secret": {"dismiss-everything"}, "vulnerability": set()},
    )
    monkeypatch.setattr(
        "scan_worker.jobs.filter_dismissed",
        lambda findings, finding_type, dismissed_keys: (
            [] if finding_type == "secret" and dismissed_keys == {"dismiss-everything"} else findings
        ),
    )
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", fake_upsert)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_send_slack_alert", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_create_check_run", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.fetch_pr_changed_files_detailed",
        lambda *a, **k: [{
            "filename": "app.py", "status": "modified", "additions": 1, "deletions": 1,
            "previous_filename": None,
        }],
    )

    run_pr_scan_job(
        installation_id=1, repo_full_name="octocat/hello-world", pr_number=7,
        base_sha=base_sha, head_sha=head_sha,
    )

    assert "What changed" in posted["body"]
    assert "No new secrets, vulnerabilities, or layer violations" in posted["body"]


def test_run_pr_scan_job_still_posts_the_diff_comment_when_the_file_overview_fetch_fails(
    bare_repo_with_two_commits, monkeypatch, caplog
):
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    posted = {}

    def fake_upsert(client, token, repo_full_name, pr_number, body):
        posted["body"] = body

    def raise_error(*a, **k):
        raise RuntimeError("GitHub compare API is down")

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setattr("scan_worker.jobs.get_installation_row", lambda *a, **k: None)
    monkeypatch.setattr(
        "scan_worker.jobs.get_dismissed_identity_keys",
        lambda *a, **k: {"secret": set(), "vulnerability": set()},
    )
    monkeypatch.setattr("scan_worker.jobs.upsert_pr_comment", fake_upsert)
    monkeypatch.setattr("scan_worker.jobs._clone_url", lambda repo_full_name, token: bare_path)
    monkeypatch.setattr("scan_worker.jobs.get_installation_token", lambda *a, **k: "fake-token")
    monkeypatch.setattr("scan_worker.jobs.generate_app_jwt", lambda *a, **k: "fake-jwt")
    monkeypatch.setattr("scan_worker.jobs._insert_history", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_send_slack_alert", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs._maybe_create_check_run", lambda *a, **k: None)
    monkeypatch.setattr("scan_worker.jobs.fetch_pr_changed_files_detailed", raise_error)

    with caplog.at_level("WARNING", logger="scan_worker.jobs"):
        run_pr_scan_job(
            installation_id=1, repo_full_name="octocat/hello-world", pr_number=7,
            base_sha=base_sha, head_sha=head_sha,
        )

    assert "Secrets" in posted["body"]
    assert "What changed" not in posted["body"]
    assert any("file-overview section" in record.message for record in caplog.records)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd github-app && python -m pytest tests/test_jobs.py -k "file_overview" -v`
Expected: FAIL - `fetch_pr_changed_files_detailed` is not yet imported/wired in `jobs.py`, so the first test's monkeypatch target doesn't exist yet and/or "What changed" never appears in `posted["body"]`

- [ ] **Step 3: Wire the imports and the new block into `run_pr_scan_job`**

Update the four import sites:

```python
# line 36-37
from aletheore.history import compute_diff, summarize_file_changes
from aletheore.pr_comment import COMMENT_MARKER, format_diff_comment, format_file_overview
```

```python
# line 63
from scan_worker.blast_radius_summary import blast_radius_summary, compute_blast_radius
```

Add `fetch_pr_changed_files_detailed` to the existing multi-line import at line 146 (alongside `fetch_pr_changed_files`).

In `run_pr_scan_job`, replace:

```python
            diff["vulnerabilities"]["new"] = filter_dismissed(
                diff["vulnerabilities"]["new"], "vulnerability", dismissed["vulnerability"]
            )

            client = get_github_api_client()
            upsert_pr_comment(client, token, repo_full_name, pr_number, format_diff_comment(diff))
```

with:

```python
            diff["vulnerabilities"]["new"] = filter_dismissed(
                diff["vulnerabilities"]["new"], "vulnerability", dismissed["vulnerability"]
            )

            # Piece B of the PR-comment-presentation redesign: a fully
            # deterministic per-file "what changed" section, leading this
            # same comment, posted every run regardless of tier or whether
            # Flash Review ran at all - see docs/superpowers/specs/
            # 2026-09-27-pr-comment-presentation-design.md section 3.
            # Failure here must never cost the PR its findings comment
            # (posted right below, unconditionally) - same fail-open
            # contract as every other side computation in this function
            # (see _maybe_send_slack_alert and the three _maybe_create_
            # *_check_run calls further down).
            file_overview = ""
            try:
                changed_files_detailed = fetch_pr_changed_files_detailed(
                    get_github_api_client(), token, repo_full_name, base_sha, head_sha
                )
                overview_rows = summarize_file_changes(old, new, changed_files_detailed)
                per_target = compute_blast_radius(
                    new, [row["path"] for row in overview_rows]
                )["per_target"]
                for row in overview_rows:
                    row["dependents_count"] = len(per_target.get(row["path"], []))
                file_overview = format_file_overview(overview_rows)
            except Exception:  # noqa: BLE001
                logging.getLogger("scan_worker.jobs").warning(
                    "could not build the PR file-overview section for installation=%s repo=%s",
                    installation_id, repo_full_name, exc_info=True,
                )

            client = get_github_api_client()
            upsert_pr_comment(
                client, token, repo_full_name, pr_number,
                format_diff_comment(diff, file_overview=file_overview),
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd github-app && python -m pytest tests/test_jobs.py -k "file_overview or dismissed_secret or check_run_failure" -v`
Expected: PASS - the 3 new tests, plus the 2 pre-existing tests in the same area (`test_run_pr_scan_job_excludes_a_dismissed_secret_from_the_pr_comment`, `test_check_run_failure_does_not_overwrite_diff_comment`) still green, confirming the new block doesn't disturb the existing dismissal-filtering or check-run-failure paths it now sits next to.

- [ ] **Step 5: Commit**

```bash
git add github-app/scan_worker/jobs.py github-app/tests/test_jobs.py
git commit -m "feat: post the PR file-overview section from run_pr_scan_job"
```

---

### Task 6: Full test suite and dogfood check

**Files:** none new - verification only.

- [ ] **Step 1: Run the full `github-app` suite once, alone**

Run: `cd github-app && python -m pytest -q` (per this project's own established lesson: never run this alongside another full suite against the same shared local Postgres - run it alone)
Expected: all tests pass, no new failures anywhere outside the files this plan touched

- [ ] **Step 2: Run the full `src` suite once, alone**

Run: `cd src && python -m pytest -q`
Expected: all tests pass

- [ ] **Step 3: Mutation-check the rename fix**

Temporarily revert Task 2's `old_lookup_path = previous_path if previous_path else path` to just `path` (i.e., always look up the current path in `old`, ignoring a rename), re-run `test_summarize_file_changes_rename_diffs_against_the_previous_path`, confirm it now FAILS for the right reason (reports `functions_added: ["f1", "f2"]` instead of `[]`), then restore the fix and confirm it passes again. This is the one piece of new logic in this plan with no existing precedent to copy verbatim (spec section 6) - worth confirming the test actually catches the bug it claims to.

- [ ] **Step 4: Dogfood on a real PR after deploy**

After this ships and is deployed (by hand over SSH, per the existing runbook - not a PyPI release), open a real PR against this repo and read the actual posted comment, the same live check already done for Piece A's rank+severity predecessor on PR #841. Confirm: the file-overview section appears above the findings list, renamed files (if any real PR in this repo renames something) show "renamed from", and the truncation line appears if a PR touches more than 20 files.
