# Veridion CI/PR Integration Design

**Status:** Draft, pending review
**Date:** 2026-07-15

## Problem

Direction 2 of the four-direction differentiation brainstorm (against CodeRabbit, Dependabot,
RepoWise, Obsidian — continuity/history was direction 1, spec/plan already written, currently
being implemented). CodeRabbit and Dependabot both live in CI, catching regressions on every
PR; Veridion today only runs when a developer manually invokes it. This closes that gap: a way
to compare two refs (a PR's base and head) and surface what changed, wired into GitHub Actions
so it's usable with no extra research — matching the original "obvious, no-second-thought
choice for any developer" goal this whole brainstorm started from.

## Goals

- A new `veridion diff <old.json> <new.json>` CLI command comparing two explicit evidence.json
  files, independent of the local `.veridion/history/` rolling-history mechanism (direction 1)
  — built on the same `compute_diff()` function, but a different input model (arbitrary files,
  not implicit "last two local runs").
- An opt-in `--fail-on-new-secrets` flag for the one CI-gating case that isn't a judgment call:
  a new, real (non-placeholder) secret finding.
- A real, publishable composite GitHub Action (`action.yml` at the repo root) wrapping the
  two-ref-checkout → scan-both → diff flow, so a consumer's workflow is a few lines referencing
  `uses: ArihantK15/Veridion@v1`, not a copy-pasted multi-step script.
- The action exposes its JSON result as a GitHub Actions output, so a consumer can pipe it into
  their own PR-comment step if they want one.

## Non-Goals

- No default/automatic fail policy beyond the opt-in `--fail-on-new-secrets` flag — layer
  violations and vulnerability severity remain CI-author judgment calls, buildable on top of
  the JSON output (e.g. via `jq`) without Veridion taking a position.
- Veridion itself never posts PR comments, opens issues, or calls the GitHub API to write
  anything — strictly diagnostic, matching the print-only default established for the CLI
  command. This deliberately does not reopen the diagnose-vs-act question (direction 4, saved
  for last in the original four-direction sequencing).
- No new `evidence.json` fields, no scanner changes — purely a new CLI entry point plus a new
  `action.yml`, both built on the `compute_diff()` function shipping as part of direction 1.
- No automated Marketplace publishing — cutting the release and clicking "Publish to
  Marketplace" is a manual, account-level action only the repo owner can do (same category as
  PyPI publishing and GitHub Sponsors activation earlier this project).
- No special-casing for "noisy" diffs across a long-lived base vs. a far-ahead feature branch —
  the existing curated-diff design (4 identity-keyed finding types + 3 aggregate counts, not a
  full module/dependency-graph diff by default) already keeps this readable; `--full` remains
  available for anyone who wants the raw picture.

## CLI Design: `veridion diff`

```
veridion diff <old.json> <new.json> [--full] [--fail-on-new-secrets]
```

- Reads both files, calls `compute_diff(old, new, full=<flag>)` (from direction 1's
  `veridion/history.py`), prints the result as indented JSON — identical output shape to
  `query changes`, since both call the same function.
- Missing or unreadable file: prints `error: <path> is not valid JSON` (or the file-not-found
  equivalent) and exits `1` — same "clean error, no traceback" discipline as the rest of the
  CLI (`_query`'s existing `evidence_path.exists()` check).
- `--fail-on-new-secrets`: after computing the diff, checks whether `secrets.new` or
  `history_secrets.new` contains any finding with `likely_placeholder: false`. If so, prints
  the diff as normal, then exits `1` (after printing, not instead of — the diff itself is
  still useful CI-log output even on failure). Otherwise exits `0`. Without the flag, exit code
  reflects only whether the command ran successfully, never a judgment about findings.

## `action.yml`: Composite GitHub Action

Lives at the repository root (chosen over a separate dedicated repo — one repo to maintain,
consistent with this repo already hosting many concerns at root; the Marketplace listing
inherits the main repo's visibility rather than starting from zero).

**Inputs:**
- `base-ref` (optional, defaults to `github.event.pull_request.base.sha`)
- `head-ref` (optional, defaults to `github.event.pull_request.head.sha`)
- `fail-on-new-secrets` (optional, `"true"`/`"false"`, default `"false"`)
- `full` (optional, `"true"`/`"false"`, default `"false"`)

**Output:**
- `diff-json` — the full JSON diff result, set via the `$GITHUB_OUTPUT` heredoc mechanism
  (`echo "diff-json<<VERIDION_EOF" ... >> "$GITHUB_OUTPUT"`) rather than a single-line variable,
  since JSON output can be arbitrarily long and contain characters that break naive
  `::set-output`-style assignment.

**Steps** (composite action, `using: "composite"`):
1. `actions/checkout@v4` with `ref: ${{ inputs.base-ref }}`, `path: veridion-base`
2. `actions/checkout@v4` with `ref: ${{ inputs.head-ref }}`, `path: veridion-head`
3. `actions/setup-python@v5`, Python 3.12 (matching this project's own floor)
4. Install: `pip install "${{ github.action_path }}/prototype"` — confirmed necessary (no PyPI
   package exists for Veridion; `pip index versions veridion` returns no match).
   **Correction found via live testing** (Task 3 Step 3 of the implementation plan): the
   original design used `git+https://github.com/ArihantK15/Veridion.git@${{ github.action_ref
   }}#subdirectory=prototype`, reasoning that `github.action_ref` would pin the install to the
   consumer's referenced tag. This broke immediately in the first live end-to-end test — GitHub
   only sets `action_ref` when the action is referenced by tag/branch/SHA, and is **empty**
   when referenced via a local path (`uses: ./`, exactly how same-repo testing before the first
   tag exists must work), producing an invalid pip URL with an empty revision after `@`.
   `github.action_path` (the path where the action's own source is already checked out) is set
   correctly regardless of reference style, requires no network git-fetch at all, and works
   identically for same-repo (`uses: ./`) and cross-repo (`uses: owner/repo@v1`) consumption.
5. `veridion scan veridion-base --no-check-vulnerabilities`
6. `veridion scan veridion-head --no-check-vulnerabilities`
   (Vulnerability checking is off by default in the action, since it calls OSV.dev twice per
   run and doubles run time for a signal not central to "what changed in this PR" — a consumer
   who wants it can pass through a future input if this turns out to matter; not building that
   toggle now, since nobody's asked for it yet.)
7. Run `veridion diff veridion-base/.veridion/evidence.json veridion-head/.veridion/evidence.json`
   with `--full`/`--fail-on-new-secrets` appended based on the corresponding inputs, capture
   stdout to a file, write it to `$GITHUB_OUTPUT` as the `diff-json` output, and propagate the
   command's own exit code as the step's exit code (so `--fail-on-new-secrets` actually fails
   the job when a consumer opts in).

**Branding**: `icon: shield`, matching Veridion's security-and-evidence framing.

## Versioning & Release

Tag `v1.0.0`, then a floating `v1` tag pointing at it — the standard GitHub Actions convention
(consumers pin `@v1` and get non-breaking updates as the maintainer moves the tag forward).
Publishing the release to the Marketplace via the GitHub UI is a manual step for the repo owner
to do once the action is verified working — not something to automate.

## Reproducibility

`veridion diff` inherits `compute_diff()`'s existing purity guarantee (direction 1) — same two
files in, same output out. The action's own behavior (which refs get checked out) depends on
GitHub's own PR event payload, which is out of Veridion's control by nature — this is the same
category of "input the tool doesn't control" as `.veridion.json` being part of a scanned repo's
own committed state, not a new reproducibility concern.

## Testing Strategy

Unit tests for `veridion diff`: two files with a real difference produce the expected diff,
`--full` produces the raw shape, `--fail-on-new-secrets` exits 1 only when a real (not
placeholder) new secret exists, missing/invalid file paths error cleanly. `action.yml` itself
has no unit-testable logic (it's declarative composite-step configuration) — verified instead
by live end-to-end testing (below).

## Success Criteria

1. `veridion diff` run against two real evidence.json files (e.g. two Procta snapshots from
   direction 1's history) produces correct, identical-to-`compute_diff()` output.
2. `--fail-on-new-secrets` exits 1 against a synthetic pair of evidence files where the second
   has one new real secret, and exits 0 when the only new secret is `likely_placeholder: true`.
3. A live end-to-end test: a real GitHub repository (a scratch/test repo, not Procta or
   Veridion itself) with a real pull request, using the action via a relative/local reference
   (`uses: ./` in a workflow within the same repo, GitHub's supported mechanism for testing an
   action before it's tagged/published) — confirms the action runs, checks out both refs
   correctly, and produces a correct diff as both console output and the `diff-json` output.
4. The action is tagged `v1.0.0`/`v1` and is functional when referenced via
   `uses: ArihantK15/Veridion@v1` from a separate scratch repository (proving the tag-pinned,
   cross-repo consumption path works, not just the same-repo `uses: ./` path).
