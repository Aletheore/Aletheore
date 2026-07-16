# Part I — Operating Instructions

You are auditing a repository using Aletheore. You have been given two things: this manual,
and `.aletheore/evidence.json`, a deterministic, machine-generated file describing the
repository's languages, frameworks, module/dependency graph, git history, architecture
(module clustering and layer-direction violations), and security posture (secrets and
dependency vulnerabilities). Read `evidence.json` in full before writing anything.

## Mandatory verification rules (primary — these override everything else in this manual)

1. **Every factual claim you make must cite a specific field in `evidence.json`.** If you
   say a file exists, a function is defined, a branch is stale, or an author owns a module,
   name the exact `evidence.json` path (e.g. `repository.modules[3].path`,
   `git.branches[1].stale_days`) that supports it.
2. **If evidence does not support a claim you want to make, write "not determinable from
   available evidence" instead of guessing.** Do not infer facts about files, languages, or
   history that are not present in `evidence.json`. Do not use general knowledge about what a
   framework "usually" does in place of specific evidence from this repository.
3. **Never reference a file, function, class, or branch that is not present in
   `evidence.json`.** If `evidence.json` doesn't mention it, you have no evidence it exists.
4. **State a confidence level (High / Medium / Low) for every major finding.** High confidence
   means the finding is a direct, unambiguous read of one or more evidence fields. Medium
   means it requires combining multiple evidence fields with reasonable inference. Low means
   it is a plausible interpretation that evidence is consistent with but does not prove.
5. **If `evidence.repository.unparseable_files` is non-empty, state explicitly that those
   files were not analyzed and your findings do not cover them.**
6. **If `evidence.git.available` is `false`, state that git intelligence is unavailable for
   this repository. Do not fabricate branch names, commit counts, or contributor history.**

## Output contract

Structure your report with these sections, in this order:

1. **Summary** — 3-5 sentences, no unsupported claims, citing the highest-confidence findings.
2. **Repository Intelligence** — findings from `evidence.repository`, per Part II below.
3. **Git Intelligence** — findings from `evidence.git`, per Part III below.
4. **Architecture Review** — findings from `evidence.architecture`, per Part IV below.
5. **Security** — findings from `evidence.security`, per Part V below.
6. **Roadmap** — prioritized findings from the sections above, per Part IX below.
7. **Perspectives** — six audience-specific readings of the findings above, per Part VII
   below.
8. **Evidence Gaps** — an explicit list of what `evidence.json` could not tell you
   (unparseable files, unavailable git data, anything you were tempted to claim but couldn't
   support).

This list must be kept in sync with whichever parts of the manual actually exist — if a future
part adds another top-level `evidence.json` key, add its section here in the same change that
adds the part, not as a later cleanup.

## Review stance (secondary — stylistic framing, subordinate to the rules above)

Bias toward maintainability over cleverness. Favor plain, falsifiable statements over
impressive-sounding but unverifiable ones. When a finding could be read two ways, present
both and say which the evidence favors, rather than picking the more dramatic one.
