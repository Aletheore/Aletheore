# Part III — Git Intelligence

This section governs how to read `evidence.git`. Follow the mandatory verification rules in
Part I for everything below.

## Availability check (do this first)

**If `evidence.git.available` is `false`, stop here for this section.** State plainly that
git intelligence is unavailable for this repository (e.g. no commits yet), and do not proceed
to describe branches, cadence, or ownership. Do not fabricate any git history.

## What's in `evidence.git` (when available)

- `branches`: each with `name`, `type` (local/remote), `last_commit_at`, `stale_days`,
  `ahead_of_main`/`behind_main`.
- `commit_cadence`: `weekly_counts` (commits per week, most recent last) and a `trend`
  classification.
- `ownership`: per-author commit counts and the percentage of total commits each represents.
- `repo_age_days` and `total_commits`.

## What counts as noteworthy

- **Long-stale branches**: any branch with a large `stale_days` relative to the others. Name
  the branch and its exact `stale_days` value.
- **Ownership concentration**: if one author's `percent` in `ownership` is much higher than
  the rest combined, name the author and the exact percentage — this is a bus-factor signal,
  not a judgment about the author.
- **Cadence drop-offs**: a sharp decline in `commit_cadence.weekly_counts` toward the most
  recent weeks. Cite the actual numbers you're comparing.

## What this section does not produce

Do not attempt to score "branching strategy quality," "commit message quality," or produce a
Merge Order Matrix, Conflict Prediction, or Cherry-Pick Suggestions. Those require judgment
this manual does not yet define rules for. Report the raw facts above; leave scoring for a
future part of the manual.
