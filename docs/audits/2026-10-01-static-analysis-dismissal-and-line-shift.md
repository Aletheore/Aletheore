# Static-analysis check run: dismissal wiring and line-shift false positives

Surfaced by PR #888 (a legitimate Bandit B607 fix that shifted line numbers for
the rest of the file, which flipped four unrelated, pre-existing findings
further down to "new" and failed the "Aletheore Deterministic Scan" check run
with no legitimate way to clear them). Both root causes below are fixed, in
two separate commits.

## Fix 1: dashboard dismissals never reached this check run

`run_pr_scan_job` (`github-app/scan_worker/jobs.py`) already ran
`diff["secrets"]["new"]`, `diff["history_secrets"]["new"]`, and
`diff["vulnerabilities"]["new"]` through `filter_dismissed` before posting the
PR comment and creating check runs, but never did the same for
`diff["static_analysis"]["new"]` before `_maybe_create_static_analysis_check_run`
read it. A dashboard dismissal of a static-analysis finding had no effect on
this check run at all.

Fixed by adding the same `filter_dismissed(diff["static_analysis"]["new"],
"static_analysis", dismissed["static_analysis"])` call alongside the other
three, before the file-overview/change-diagram section and the check run are
built from `diff`.

While wiring this up, found that `scan_worker/db.py`'s sync
`get_dismissed_identity_keys` (the RQ-job-side counterpart of
`app_server/dismissed_findings.py`'s async version) never seeded a
`"static_analysis"` key in its result dict at all - unlike the async version,
which seeds all five finding types. A dismissed static-analysis row would have
made `result[finding_type].add(identity_key)` raise `KeyError` reading it back,
and the new `dismissed["static_analysis"]` lookup added above would `KeyError`
on an installation with zero static-analysis dismissals. Fixed by adding the
missing key, same as the other four.

Covered by (TDD: written red against the pre-fix behavior, confirmed failing,
then green after the fix):
- `test_run_pr_scan_job_excludes_a_dismissed_static_analysis_finding_from_the_check_run`
  (`github-app/tests/test_jobs.py`)
- `test_get_dismissed_identity_keys_sync_includes_a_dismissed_static_analysis_finding`
  (`github-app/tests/test_scan_worker_db.py`)

## Fix 2: a line shift misclassified unchanged findings as new/resolved

`_new_and_resolved` (`src/aletheore/history.py`) keyed static-analysis
identity on `(tool, rule_id, path, line)` - exact line, no tolerance for a
shift caused by unrelated edits elsewhere in the same file. Fix 1 above does
not touch this: a finding freshly reclassified as "new" by a line shift was
never dismissed under its new identity, so there was nothing to filter.

### What changed

**`src/aletheore/static_analysis/__init__.py`** now computes a
`content_fingerprint` on every finding at scan time (`_add_content_fingerprints`,
called from `check_static_analysis` before returning): a sha256 of the
3-line window (line-1, line, line+1, each trimmed) read from the checkout
while it's still on disk. None when there's nothing real to hash - no real
line (the misconfig-finding case `_static_analysis_annotations` in
`jobs.py` already special-cases), an unreadable/missing path, or a line past
the end of the file.

**`src/aletheore/history.py`**: `_new_and_resolved` now takes an optional
`key_fn` alongside its existing `fields` tuple, for a category whose identity
isn't a flat field read. `_static_analysis_identity` prefers each finding's
`content_fingerprint` over its line number, falling back to
`(tool, rule_id, path, line)` per-finding when no fingerprint exists (a
line-less finding, or evidence from before this field existed). Since an
unrelated edit shifts the line but not the 3-line window's own content, a
moved-but-unchanged finding now keys identically on both sides of the diff
and is reported as neither new nor resolved.

Also mirrors `_secret_identity_fields`'s existing straddling-upgrade handling
(for secrets' `match_preview` hash-format change): `_has_legacy_static_analysis_identity`
detects a scan where *no* fingerprintable finding has a fingerprint (i.e. the
evidence predates this feature) and falls back to a coarser
`(tool, rule_id, path)` identity for that one diff, so the single scan that
introduces fingerprinting doesn't report every pre-existing finding as both
newly added and resolved. This matters concretely for `aletheore changes`
(`src/aletheore/cli.py`'s `_query_changes`), which diffs the two most recent
*stored* snapshots - exactly the shape of a pre-upgrade-vs-post-upgrade
comparison. It self-heals: the next scan has fingerprints on both sides and
the coarser fallback stops applying on its own. The GitHub App's own PR-scan
path never hits this, since both "old" and "new" evidence there are scanned
fresh, with the same code, every time (`run_pr_scan_job` calls `_run_scan` on
both the base and head checkouts).

**`github-app/app_server/dismissed_findings.py`**: `finding_identity_key` for
`static_analysis` now uses the same `content_fingerprint`-or-`line` position
as the diffing side, so a dismissal survives a later, unrelated line shift
instead of silently stopping to match. Falls back to line when absent, so
every dismissal recorded before this field existed keeps matching exactly as
before - *except* for a dismissal recorded against a finding that, after this
ships, gets re-scanned and acquires a real fingerprint for the first time:
its identity key changes from line-based to fingerprint-based, so that one
dismissal needs to be re-applied once. This is the same accepted cost
`finding_identity_key`'s own docstring already documents for the secret
`match_preview` hash-format change (see migration 045) - a one-time,
self-resolving cost of the fix, not an ongoing gap.

### Known, accepted limitation

Two genuinely different findings that happen to sit on identical surrounding
source lines (e.g. the same boilerplate repeated twice in one file) collapse
to the same fingerprint and the same identity. Narrower than hashing the
rule's own message text alone (most rule messages, e.g. Bandit's B607, are
generic and identical across every call site - PR #888's own four false
positives), but not immune to genuine duplication. Not addressed here;
flagged for anyone revisiting this.

### Tests

TDD throughout (each new assertion confirmed failing against the pre-fix
code, then passing after):
- `src/tests/test_static_analysis_orchestrator.py`: `content_fingerprint` is
  added for a real line, is stable across a line shift, differs for
  different content, and is `None` for a line-less finding or an unreadable
  path.
- `src/tests/test_history.py`: a moved-but-unchanged finding is neither new
  nor resolved; a genuinely new/resolved finding still is; the legacy-upgrade
  straddle doesn't report every pre-existing finding as new, a genuinely new
  one across that same upgrade still is, and the coarse fallback stops
  applying once both sides are fingerprinted.
- `github-app/tests/test_dismissed_findings.py`: the dismissal identity key
  prefers `content_fingerprint`, falls back to line without one, and still
  tells apart two different fingerprints on the same line.
