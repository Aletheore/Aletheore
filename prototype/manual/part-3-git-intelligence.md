# Part III — Git Intelligence

This section governs how to read `evidence.git`. Follow the mandatory verification rules in
Part I for everything below.

## Availability check (do this first)

**If `evidence.git.available` is `false`, stop here for this section.** State plainly that
git intelligence is unavailable for this repository (e.g. no commits yet), and do not proceed
to describe branches, cadence, or ownership. Do not fabricate any git history.

## What's in `evidence.git` (when available)

- `branches`: each with `name`, `type` (local/remote), `last_commit_at`, `stale_days`,
  `ahead_of_main`/`behind_main` (real commit counts relative to the detected default branch,
  not placeholders — a branch can legitimately show 0/0 if it's fully merged).
- `commit_cadence`: `weekly_counts` (commits per week, most recent last), a `trend`
  classification, and `most_recent_week_partial` (true if the most recent bucket hasn't
  covered a full 7 days yet as of `evidence.scanned_at`).
- `ownership`: grouped by `email` (case-insensitively normalized), each entry listing the
  `names` git recorded for that email, `commit_count`, and `percent`. Two different display
  names under the same email are already the same person by construction — do not re-flag
  that as an identity ambiguity.
- `repo_age_days` and `total_commits`.

## What counts as noteworthy

- **Long-stale branches**: any branch with a large `stale_days` relative to the others. Name
  the branch and its exact `stale_days` value.
- **Unmerged work**: a branch with nonzero `ahead_of_main` (especially combined with
  `behind_main` near 0, meaning it's otherwise caught up) represents real unmerged commits
  sitting outside the default branch. Name the branch and both exact counts — this is a
  directly useful signal, not a hedge.
- **Ownership concentration**: if one `email` entry's `percent` in `ownership` is much higher
  than the rest combined, name it (its most recent-looking `names` entry is fine for
  readability) and the exact percentage — this is a bus-factor signal, not a judgment about
  the person.
- **Cadence drop-offs**: a sharp decline in `commit_cadence.weekly_counts` toward the most
  recent weeks. Cite the actual numbers you're comparing, and check
  `most_recent_week_partial` before calling the final week a slowdown — if it's `true`, say
  the drop is not yet confirmed because the week is still in progress, not that it's a
  decline.

## What this section does not produce

Do not attempt to score "branching strategy quality," "commit message quality," or produce a
Merge Order Matrix, Conflict Prediction, or Cherry-Pick Suggestions. Those require judgment
this manual does not yet define rules for. Report the raw facts above; leave scoring for a
future part of the manual.
