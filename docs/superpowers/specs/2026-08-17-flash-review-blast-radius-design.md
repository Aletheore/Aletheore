# Flash Review: confidence-aware blast-radius context

**Status:** Ready for implementation.
**Owner:** implementing agent (fast, less capable — follow this spec literally, do not improvise
architecture; every reuse pointer below names the exact existing function to call, not a pattern
to reinvent).
**Reviewer:** Claude (review pass after implementation, before merge).

## Why this exists

`benchmarks/pr-review-benchmark/scripts/evaluate_semantic_checks.py` (run this yourself to see
current numbers before you start) measured Flash Review's deterministic semantic checks against
46 real historical bugs across two corpora: 5/21 recall on this project's own PR-review corpus,
0/25 on a SWE-bench-derived corpus (django, sympy, scikit-learn, sphinx, matplotlib, astropy,
xarray). `build_referenced_symbol_context` (`github-app/scan_worker/flash_review.py`) — the
existing mechanism that resolves a changed file's imported symbols into real definitions — only
finds something in ~27% of real diffs (6/22 in the smaller corpus), because most diffs don't call
into a symbol imported *from within the changed file*. What's missing is the other direction:
**when you change a function, who else in the repo calls it?** That's blast radius, and nothing in
this codebase currently answers that question, at any confidence level.

This spec adds exactly that, as new deterministic **review context** (text handed to the model,
labeled "not conclusions" like every other deterministic context block in this file). It does
**not** add a new `semantic_checks.py` check — that's explicitly out of scope for this piece.

## What already exists — reuse these, do not reimplement them

Read `github-app/scan_worker/flash_review.py` in full before starting. Specifically:

1. **`_diff_valid_lines(diff_text, patches=None) -> dict[str, set[int]]`** (line ~545). Maps each
   file to the new-file line numbers its diff hunks actually touch. This is the *only* correct way
   to know which lines changed — do not write your own hunk parser. A hand-rolled hunk parser is
   exactly the mistake that had to be found and fixed twice already in this codebase's history
   (see `github-app/scan_worker/semantic_checks.py`'s module docstring for why whole-file/wrong-
   scope parsing is a real, shipped bug class here, not a hypothetical one).
2. **`evidence["repository"]["modules"]`** — each module (keyed by `path`) has:
   - `imports: list[str]` — paths of modules it imports (already resolved to real repo paths, not
     raw import strings — see how `build_referenced_symbol_context` uses `by_path.get(imported_path)`
     directly).
   - `imported_by: list[str]` — paths of modules that import *this* module (already resolved,
     already used by `build_dependency_impact_context`, line ~205 — read that function, it is the
     closest existing analog to what you're building).
   - `symbols: {"functions": [...], "classes": [...]}`, each entry has `name`, `start_line`,
     `end_line` (1-indexed, inclusive) — same shape `build_referenced_symbol_context` already reads.
3. **The existing labeling convention.** Every deterministic context block in this file starts with
   `"--- deterministic <name> (not conclusions) ---"` or similar and is *omitted entirely* (return
   `""`) when there's nothing real to say — never emit a block that just says "no callers found."
   Match this exactly; see `build_dependency_impact_context`'s `if len(facts) > 1` / `if not lines:
   return ""` pattern.
4. **The existing capping convention.** `MAX_REFERENCED_SYMBOLS = 8`, `imports[:8]` in
   `build_dependency_impact_context`. Everything you build must be similarly bounded — an
   unbounded scan of a "hub" file's `imported_by` list (a file imported by hundreds of others) is
   a real cost/context-size risk on a large real repo, not a hypothetical one.

## What to build

### 1. New function in `flash_review.py`

Place it directly after `build_dependency_impact_context` (before `MAX_REFERENCED_SYMBOLS`).

```python
MAX_BLAST_RADIUS_SYMBOLS = 5      # how many changed symbols to analyze per diff
MAX_BLAST_RADIUS_CANDIDATES = 20  # how many of a symbol's imported_by files to check
MAX_BLAST_RADIUS_CALLERS_SHOWN = 5  # how many confirmed callers to list per symbol

_CALL_SHAPE_RE_CACHE: dict[str, re.Pattern] = {}


def _call_shape_re(name: str) -> re.Pattern:
    # Cached per name within one call to build_blast_radius_context - the
    # same symbol name can be checked against many candidate files.
    pattern = _CALL_SHAPE_RE_CACHE.get(name)
    if pattern is None:
        pattern = re.compile(rf"\b{re.escape(name)}\s*\(")
        _CALL_SHAPE_RE_CACHE[name] = pattern
    return pattern


def build_blast_radius_context(
    evidence: dict | None,
    changed_files: list[str],
    diff_text: str,
    fetch_file_content: Callable[[str], str | None],
) -> str:
    """For each symbol this diff actually touches, who else in the repo
    calls it - confirmed by both a real import relationship (evidence's
    own imported_by) AND the symbol name actually appearing in a
    call-shaped position in that file's real content, not just "imports
    the file at all" (which says nothing about which of possibly many
    exported names is actually used).

    This is deliberately the high-confidence case only: "imported_by AND
    real call-shape match in real content" - not a bare repo-wide text
    search for the symbol name, which would be a real false-positive risk
    (name collisions between unrelated symbols in different modules are
    common, especially for short/generic names). A lower-confidence,
    name-only tier is explicitly out of scope for this pass.
    """
    if not evidence:
        return ""
    modules = evidence.get("repository", {}).get("modules", [])
    by_path = {m["path"]: m for m in modules if m.get("path")}
    valid_lines = _diff_valid_lines(diff_text)

    lines: list[str] = []
    symbols_analyzed = 0

    for file_path in changed_files:
        if symbols_analyzed >= MAX_BLAST_RADIUS_SYMBOLS:
            break
        module = by_path.get(file_path)
        if module is None:
            continue
        touched = valid_lines.get(file_path, set())
        if not touched:
            continue
        symbols = module.get("symbols", {})
        for entry in symbols.get("functions", []) + symbols.get("classes", []):
            if symbols_analyzed >= MAX_BLAST_RADIUS_SYMBOLS:
                break
            name = entry.get("name")
            start, end = entry.get("start_line"), entry.get("end_line")
            if not name or start is None or end is None:
                continue
            if not any(start <= line <= end for line in touched):
                continue  # this symbol's range wasn't actually touched by the diff

            symbols_analyzed += 1
            candidates = (module.get("imported_by") or [])[:MAX_BLAST_RADIUS_CANDIDATES]
            call_re = _call_shape_re(name)
            callers: list[str] = []
            for candidate_path in candidates:
                if len(callers) >= MAX_BLAST_RADIUS_CALLERS_SHOWN:
                    break
                content = fetch_file_content(candidate_path)
                if content is None:
                    continue
                if call_re.search(content):
                    callers.append(candidate_path)

            if callers:
                total = len(module.get("imported_by") or [])
                shown = f"{', '.join(callers)}" + (
                    f" (+{total - len(callers)} more importers not shown)"
                    if total > len(callers)
                    else ""
                )
                lines.append(f"{file_path}:{name} is called from: {shown}")

    if not lines:
        return ""
    return (
        "--- deterministic blast-radius context (confirmed import + real call-shape match, "
        "not conclusions) ---\n" + "\n".join(lines)
    )
```

The code above is a *reference implementation*, not pseudocode — implement it close to verbatim,
then adapt only where your read of the actual file shapes (see step 2 below) shows a real
mismatch. Do not restructure it "for clarity" — every design choice above (capping, the
`imported_by`-then-verify-by-content order, omitting symbols with zero confirmed callers) was
made for a specific reason stated in the comments.

### 2. Wire it into `jobs.py`

Find where `build_dependency_impact_context` and `build_change_impact_context` are already called
and folded into `code_evidence_context` (search `_run_flash_review`). Add a third block the same
way, immediately after the `change_impact_context` block:

```python
def _fetch_full_file(file_path: str) -> str | None:
    content = fetch_file_content(client, token, repo_full_name, file_path, head_sha)
    return content

blast_radius_context = build_blast_radius_context(evidence, changed_files, diff_text, _fetch_full_file)
if blast_radius_context:
    code_evidence_context = "\n\n".join(
        part for part in (code_evidence_context, blast_radius_context) if part
    )
```

`fetch_file_content` is already imported into `jobs.py` from `scan_worker.github_api` (check the
existing import block near the top of the file) — you are calling the same function
`_fetch_symbol_source` already wraps, just without slicing to a line range, since blast radius
needs to search a candidate file's *whole* content for the call-shape match, not one symbol's body.

### 3. Tests

Add to `github-app/tests/test_flash_review.py`, next to the existing
`test_build_dependency_impact_context_includes_raw_graph_facts` test — match its style exactly
(the fixture-building pattern, not a new one).

Required tests, minimum:
- **Finds a real confirmed caller**: a changed symbol whose containing module's `imported_by`
  includes a file, and a fake `fetch_file_content` returns content containing `f"{name}("` for
  that file → asserts the output contains `"is called from:"` and the caller's path.
- **Omits a symbol with no confirmed callers**: `imported_by` present, but the fake fetcher never
  returns content containing the call shape → asserts `context == ""` (not a block saying "no
  callers found" — omitted entirely, matching the existing convention).
- **Does not flag an untouched symbol**: a file has two functions, the diff only touches one (use
  a real diff string and `_diff_valid_lines` naturally, don't hand-roll the touched-line set) →
  asserts only the touched one appears in the output.
- **Caps candidates checked**: an `imported_by` list longer than `MAX_BLAST_RADIUS_CANDIDATES` →
  assert `fetch_file_content` was never called for anything past the cap (use a `MagicMock` and
  assert call count, matching how other tests in this file already assert call counts on mocked
  callables).
- **A caller importing the file but using a *different* symbol is not flagged** — this is the
  precision case that justifies requiring real content match, not just `imported_by` membership.
  Construct a candidate whose content contains some *other* identifier but not `f"{name}("`.

### 4. Real-data verification — do this before declaring the work done

Unit tests alone are not sufficient evidence this works on real code (see this project's own
`benchmarks/pr-review-benchmark/scripts/evaluate_semantic_checks.py` and the design notes at the
top of `github-app/scan_worker/semantic_checks.py` for *why* real-data verification is a hard
requirement in this codebase, not a nice-to-have — every check that skipped it shipped a real
scoping bug). Do this:

1. Pick a real case from `benchmarks/pr-review-benchmark/cases/` whose changed file's symbol is
   very likely called elsewhere in the same repo — a good candidate is a widely-used utility
   function in a large repo (try a `swebench-django-*` or `swebench-scikit-learn-*` case first;
   Django and scikit-learn have plenty of internally-reused functions, unlike a small single-file
   library).
2. Write a small throwaway script (like `evaluate_semantic_checks.py` does) that clones the case's
   repo at its base commit (`scripts.build_case_repo.prepare_case_checkout` — reuse it, don't
   reimplement cloning), runs `scan_repository`, builds `diff_text` via
   `evaluate_semantic_checks.git_diff_to_review_format`, and calls `build_blast_radius_context`
   with a real `fetch_file_content` reading from the actual checkout on disk.
3. Report back: for at least 3 real cases, what did `build_blast_radius_context` actually return?
   Paste the real output, not a description of it. If it returned `""` for all 3, that's a real
   finding to report honestly (matching this project's own precedent: a check with zero real hits
   is reported as zero, not massaged) — say so plainly rather than picking cases until one works.

## Explicitly out of scope for this pass (do not build these)

- A low-confidence, name-only (no confirmed import) tier. Real false-positive risk from name
  collisions; needs its own design and its own real-data verification before it's worth adding.
- Any new `semantic_checks.py` check using this data (e.g. "a changed function's signature changed
  and a caller wasn't updated"). This spec is context-only. A check built on top of this is a
  legitimate future step, but a separate one, reviewed separately.
- Cross-repo or cross-language symbol resolution. Same-repo, same-language only (matches how
  `build_referenced_symbol_context` already behaves).

## What the reviewer (Claude) will check

- Every reuse pointer above was actually followed — no hand-rolled hunk parsing, no reimplemented
  import resolution.
- The capping constants are actually enforced (test that asserts a call count, not just a
  plausible-looking `[:N]` slice that could silently not be reached).
- The "omit rather than emit an empty/negative block" convention is followed.
- Section 4's real-data verification actually ran, with real pasted output, not a claim of having
  run it.
- No changes to `semantic_checks.py`, no new check added — this PR is additive context only.
