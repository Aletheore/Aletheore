# Veridion Continuity / History Design

**Status:** Draft, pending review
**Date:** 2026-07-15

## Problem

Every `veridion scan`/`audit` run overwrites `.veridion/evidence.json` in place. There is no
memory of any prior run — "what changed since I last audited this repo" is unanswerable today,
even though the underlying data (evidence.json) is already produced on every run. This is the
first of four differentiation directions identified against CodeRabbit (per-PR review),
Dependabot (continuous, acts on findings), RepoWise (persistent queryable knowledge), and
Obsidian (accumulates over time) — chosen to go first because it's the lowest-risk addition
(no new identity questions, just a time axis on data Veridion already produces) and because the
other three directions (CI/PR diffing, richer queryability, diagnose-vs-act) all benefit from
having a "before" snapshot to work against.

## Goals

- Automatically retain a rolling local history of past scans, so continuity works passively
  from normal usage without requiring the user to opt in or remember anything.
- Provide a deterministic way to answer "what changed since the last scan" via a new
  `veridion query changes` subcommand — consistent with the existing `query imports`/
  `query ownership` pattern of reading evidence without invoking an agent.
- Default output is curated and low-noise (real signal, not every field that happens to
  change on every run). A `--full` flag provides the complete raw diff for power users.
- Explicitly account for scan configuration changing between the two compared runs (e.g.
  `--no-check-vulnerabilities` toggled) so the diff never misattributes "we didn't check
  before" as "this is a new finding."

## Non-Goals

- No cross-repo aggregation or cross-machine/cloud sync of history — purely local, per
  checkout, matching `.veridion/`'s existing gitignored, local-only nature.
- No wiring into the reasoning-phase audit report in this increment (no new manual Part, no
  "Since Last Audit" report section) — a natural follow-up, but deliberately separate, mirroring
  how Part IX's synthesis layer shipped as its own round rather than bundled with earlier
  evidence work.
- No configurable retention count in this increment — hardcoded at 20 snapshots, matching the
  project's "add config only once a real need appears" pattern (`.veridion.json` was only added
  after Part IV's open question, not preemptively).
- No diffing of `git.ownership` or `repository.languages/frameworks/ai_usage/policy_docs/
  build_tools/monorepo` — low-signal for a "what changed" view; only secrets, dependency
  vulnerabilities, layer violations, and a few aggregate counts are curated.

## Storage & Snapshot Lifecycle

A new `.veridion/history/` directory (gitignored, local, sibling to the existing `.veridion/
evidence.json`) holds full copies of past `evidence.json` files. On every `scan`/`audit` run,
after `evidence.json` is written, a copy is also saved into `history/`, then rotated to keep
the most recent 20 (FIFO — oldest deleted first once the count exceeds 20 after the new save).

Snapshot filenames are derived from the evidence's own `scanned_at` field (already an
ISO-8601 UTC timestamp), sanitized for filesystem safety (`:` → `-`). This gives free
chronological ordering by filename with no counter file. **Collision handling:** if two scans
produce the same `scanned_at` value (possible in tight automated loops, e.g. a test calling
`scan_repository` repeatedly without delay, since Python's `isoformat()` can coincide at
microsecond resolution), `save_snapshot` appends a short numeric suffix (`-1`, `-2`, ...) rather
than silently overwriting an existing snapshot file — no snapshot is ever silently lost.

Snapshots store the full evidence.json content (not a stripped-down subset) — disk cost is
low (evidence.json is well under 1MB even for a repo Procta's size) and this keeps `--full`
diffing and any future curated-field expansion possible without needing to re-scan history.

**Corrupt snapshot handling:** if the most recent prior snapshot fails to parse as JSON,
`query changes` reports that plainly ("most recent snapshot is unreadable") rather than
silently falling back further to an older snapshot — falling back further would silently
compare against a much older run without saying so, which is exactly the kind of quiet
surprise this project has been disciplined about avoiding elsewhere (e.g. the
`_load_architecture_config` malformed-JSON handling treats it as absent, not "try the next
best thing").

## New Module: `veridion/history.py`

- `save_snapshot(evidence: dict, repo_path: Path, keep: int = 20) -> Path` — writes the
  snapshot, then rotates, returns the path written.
- `list_snapshots(repo_path: Path) -> list[Path]` — chronological (oldest first), based on
  filename sort (which is timestamp-derived).
- `compute_diff(old: dict, new: dict, full: bool = False) -> dict` — pure function, no I/O.

## New CLI: `veridion query changes --path <repo> [--full]`

Compares the two most recent snapshots in `history/` (the current run vs. the prior run).

- **Fewer than 2 snapshots exist** (first-ever scan): reports "no prior snapshot to compare
  against" and exits cleanly (exit code 0, informational — not an error condition, matching
  `query ownership`'s no-target-required pattern rather than `query imports`'s
  requires-a-target error pattern).
- **Prior snapshot exists but is corrupt**: reports "most recent snapshot is unreadable"
  distinctly from the "no prior snapshot" case.

## Diff Content (curated, default)

Matches the project's existing curated-list discipline (`LAYER_FOLDER_MARKERS`,
`SECRET_PATTERNS`) rather than a generic deep-diff. Identity keys (confirmed against the
current schema, not guessed):

| Finding type | Identity key | Source fields |
|---|---|---|
| Secrets (working-tree) | `(path, pattern, match_preview)` | `security.secrets.findings` |
| Secrets (git history) | `(commit, path, pattern)` | `security.secrets.history_findings` |
| Dependency vulnerabilities | `(ecosystem, package, advisory_id)` | `security.dependency_vulnerabilities.findings` |
| Layer violations | `(from, to)` | `architecture.layer_violations.violations` |

For each type, the diff reports items present in `new` but not `old` (new) and items present
in `old` but not `new` (resolved), computed by identity-key set difference.

**Note on git-history findings:** since git history is immutable, a `history_findings` entry
for a given commit will never disappear once the commit exists — resolution only happens if a
future `secrets.py` pattern set changes (a Veridion version upgrade), not from repo activity.
This is worth surfacing as context in the report but is not itself a bug to detect.

**Aggregate count deltas** (single before/after numbers, no identity-matching needed): module
count (`len(repository.modules)`), dependency-graph edge count
(`len(repository.dependency_graph.edges)`), total commits (`git.total_commits`).

**Scan-configuration-changed caveat:** before computing secret/vulnerability deltas, compare
`security.dependency_vulnerabilities.checked` and whether `security.secrets.history_findings`
was populated (a proxy for whether `--no-scan-git-history` was used) between the two snapshots.
If either differs, the diff output prepends an explicit note (e.g. "dependency vulnerability
checking was off in the prior scan and on in this one — new vulnerability findings below may
reflect newly-enabled checking, not necessarily new vulnerabilities") rather than presenting
those findings as plain regressions. This was found during this design's own self-review: a
naive diff would otherwise misattribute "we didn't check before" as "this changed," which is
exactly the kind of confident-but-wrong inference this project exists to prevent.

## `--full` (raw diff)

A generic recursive diff of the two full evidence.json structures with three top-level keys —
`added`, `removed`, `changed` — each a list of `{path, old_value, new_value}` (or just `value`
for added/removed) using flattened dot/bracket-notation paths (e.g.
`repository.dependency_graph.edges[12]`). Exact path-notation convention is a plan-level
implementation detail, not fixed here.

## Reproducibility

`compute_diff` is a pure function of its two dict inputs — same two snapshots in, same diff
out, always. `save_snapshot`'s collision-suffix behavior is the only non-pure-functional part
of this feature, and it only affects filenames, never evidence content.

## Testing Strategy

Unit tests for `save_snapshot` (creates `history/` if absent, rotates correctly at the
21st save — assert exactly 20 remain and the oldest was dropped not the newest, handles
same-timestamp collision without data loss). Unit tests for `compute_diff`: new finding
appears in "new" bucket, resolved finding appears in "resolved" bucket, aggregate deltas are
correct, the scan-configuration-changed caveat fires when `checked`/history-scanning state
differs between snapshots and does not fire when they match, `--full` output correctly
represents an added/removed/changed field. CLI tests for `query changes`: fewer-than-2-snapshots
message, corrupt-snapshot message, normal two-snapshot diff end to end.

## Success Criteria

1. Running `veridion scan` twice against a real repo (Procta) with no changes in between
   produces a `query changes` output with zero new/resolved findings and zero-delta aggregate
   counts — a real no-op regression check, not just a unit-test mock.
2. Introducing a real change between two scans (e.g. adding a file with an obvious fake secret
   pattern to a scratch repo) is correctly reflected as a new finding in `query changes`.
3. Toggling `--no-check-vulnerabilities` between two scans produces the scan-configuration-changed
   caveat rather than a misleading "new vulnerabilities" list.
4. The 21st scan against the same repo results in exactly 20 snapshots retained, with the
   oldest one gone — verified by listing `.veridion/history/` directly.
5. `compute_diff` is confirmed deterministic: run it twice on the same two snapshot dicts,
   assert byte-identical output both times.
