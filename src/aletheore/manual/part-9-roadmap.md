# Part IX — Roadmap

This section governs how to produce the `## Roadmap` section of the report, appearing after
`## Security` and before `## Evidence Gaps`. Follow the mandatory verification rules in Part I
for everything below.

## What this section does

Synthesize and prioritize findings already stated earlier in this same report. This section
introduces no new evidence and makes no new claims about the repository — it reorders and
triages what Repository Intelligence, Git Intelligence, Architecture Review, and Security
already reported.

## Mandatory rules

1. **Every roadmap item must cite the specific earlier finding it comes from** — name the
   file, branch, cluster, or evidence field exactly as it was already stated earlier in this
   report. Do not restate a finding in generic terms that lose its specificity (e.g. "fix
   circular dependencies" is not acceptable on its own; "fix the two circular import chains
   named in Architecture Review" is).
2. **Do not introduce any claim that wasn't already made in Repository Intelligence, Git
   Intelligence, Architecture Review, or Security.** If you're tempted to recommend something
   evidence doesn't support (general advice like "improve documentation" or "modernize the
   stack" that isn't grounded in a specific finding already reported earlier in this report),
   leave it out entirely.
3. **Walk all four prior sections explicitly, in this order: Repository Intelligence, Git
   Intelligence, Architecture Review, Security.** For each one, either list what from it
   belongs on the roadmap, or state plainly that this section contributed nothing
   roadmap-worthy (for example: "Security: zero findings warranted inclusion — the
   dependency-vulnerability check found nothing and both secret findings were flagged
   `likely_placeholder`"). Do not skip a section silently just because it has nothing to add.
4. **Use exactly three tiers: Immediate, Near-term, Longer-term.** No numeric scores, no
   ROI/difficulty/risk ratings, no day-count estimates (30/60/90-day language or similar). A
   tier assignment plus one sentence of evidence-grounded rationale per item is the complete
   format — nothing more elaborate.
5. **State which tiering criterion applies when assigning a tier**, using the heuristic below
   — so the assignment itself is inspectable, not a bare assertion.

## Tiering heuristic

- **Immediate**: High-confidence findings from earlier sections representing either an active
  risk (a real, non-placeholder secret finding; a confirmed circular import or layer
  violation) or a trivial, no-judgment-needed fix (unpushed local commits sitting on one
  machine; a dependency already flagged with a real advisory in
  `security.dependency_vulnerabilities.findings`).
- **Near-term**: High-confidence findings needing real but bounded effort — adding test
  coverage for a specific high-fan-in untested module named earlier in Repository
  Intelligence, reviewing or merging specific named branches with real `ahead_of_main` counts
  from Git Intelligence, addressing a specific named god-module.
- **Longer-term**: Medium- or Low-confidence findings needing investigation before action
  (cross-cluster coupling flagged "worth investigating" in Architecture Review), or findings
  that are structurally larger in scope (expanding language coverage for files currently
  listed in `repository.unparseable_files`).

## What this section does not produce

No numeric scores of any kind. No ROI, difficulty, or risk ratings. No specific day-count
timeframes. No new claims about the repository beyond what earlier sections in this same
report already stated. No business, revenue, or market recommendations — those aren't
repo-derivable and are out of scope for this manual entirely.
