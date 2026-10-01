# Static-analysis check run: dismissal wiring (fixed) and line-shift false positives (follow-up)

Surfaced by PR #888 (a legitimate Bandit B607 fix that shifted line numbers for
the rest of the file, which flipped four unrelated, pre-existing findings
further down to "new" and failed the "Aletheore Deterministic Scan" check run
with no legitimate way to clear them).

## Fixed in this change

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

Both fixes are covered by new tests (TDD: written red against the
pre-fix behavior, confirmed failing, then green after the fix):
- `test_run_pr_scan_job_excludes_a_dismissed_static_analysis_finding_from_the_check_run`
  (`github-app/tests/test_jobs.py`)
- `test_get_dismissed_identity_keys_sync_includes_a_dismissed_static_analysis_finding`
  (`github-app/tests/test_scan_worker_db.py`)

## Follow-up, not fixed here: line-shift still produces false "new" findings

`_new_and_resolved` (`src/aletheore/history.py`) keys static-analysis identity
on `(tool, rule_id, path, line)` - exact line, no tolerance for a shift caused
by unrelated edits elsewhere in the same file. This is the same limitation the
function's own comment already calls out, not something this change
introduces. The dismissal fix above does not resolve it: a finding that is
*freshly* reclassified as "new" by a line shift was never dismissed under its
new identity, so there is nothing to filter. (It does help the *recurring*
case - once a user dismisses the finding at its new line, that dismissal now
actually sticks, which is the real improvement here.)

Why this isn't a same-PR fix:

- **No content fingerprint exists to key on instead.** Checked all six
  scanner modules (`src/aletheore/static_analysis/{bandit,semgrep,gosec,pmd,
  trivy,bearer,sonarqube}_scanner.py`): every finding carries only `tool`,
  `rule_id`, `severity`, `type`, `path`, `line`, `message` - no source-line
  text, no column, no snippet. There's nothing in evidence today to hash
  against as a line-independent identity.
- **`message` is usually generic, not per-occurrence.** Bandit's B607 message
  ("subprocess call - check for execution of untrusted input") and most rule
  messages describe the *rule*, not the specific call site - exactly the
  pattern in PR #888's four false positives. Hashing `tool + rule_id +
  message` without the line would collapse every occurrence of that rule in a
  file into one identity, which is a different (also real) bug: a second,
  genuinely new instance of the same rule in the same file would be silently
  treated as the same finding as an already-dismissed one.
- **A line-window tolerance has its own failure mode.** Matching within ±N
  lines instead of exact would misfire when two findings of the same rule
  legitimately sit within N lines of each other (common for repeated-pattern
  rules like B607) - a real fix at one call site and a pre-existing issue at
  a nearby one could swap identities.
- **`_new_and_resolved` is shared infrastructure.** It's the same helper used
  for secrets, history_secrets, vulnerabilities, layer_violations, and
  endpoints (`history.py` lines 264-314+). Any change to its matching
  semantics needs to be scoped to the static_analysis call site only (e.g. a
  parameter, or a separate specialized diff for this one category) without
  touching the other five.
- **The dismissal identity key would need the same change.**
  `finding_identity_key("static_analysis", ...)` in
  `app_server/dismissed_findings.py` also keys on exact
  `path+line+tool+rule_id`. Even after this PR's fix, a dismissed finding
  whose line shifts *again* in some later, unrelated PR will stop matching
  its stored dismissal (new line, new identity key) and reappear as "new" -
  the same underlying problem, one layer up. A real fix likely needs to solve
  both `_new_and_resolved`'s and `finding_identity_key`'s line-sensitivity
  together, or the dismissal side will keep leaking even once diffing is
  fixed.

### Recommended direction (not implemented)

Capture a normalized content fingerprint per finding at scan time - e.g. a
hash of the finding's own source line, trimmed and whitespace-normalized,
read from the checkout while the scanner already has the file open - and add
it to each scanner's finding dict and to evidence's schema. Use
`(tool, rule_id, path, fingerprint)` as the primary identity for static
analysis in both `_new_and_resolved` and `finding_identity_key`, falling back
to today's `(tool, rule_id, path, line)` when no real line exists (the same
misconfig-finding case `_static_analysis_annotations` already special-cases in
`jobs.py`) or when older evidence predates the fingerprint field. This needs
an evidence-schema version bump and a decision on backward compatibility for
existing stored history snapshots and dismissals, so it's a materially larger
and riskier change than the dismissal-wiring fix above, and is left for a
follow-up PR.
