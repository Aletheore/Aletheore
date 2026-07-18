# Part VII — Perspectives

This section governs how to produce the `## Perspectives` section of the report, appearing
after `## Roadmap` and before `## Evidence Gaps`. Follow the mandatory verification rules in
Part I for everything below.

## What this section does

Seven short, audience-specific readings of findings already stated earlier in this same report
(Repository Intelligence, Git Intelligence, Architecture Review, Security, and Roadmap). This
section introduces no new evidence and makes no new claims — it reframes what's already been
established for seven audiences who would weight the same facts differently.

## Mandatory rules

1. **Every lens's "what evidence supports" claims must cite a specific earlier finding** by
   exact name, file, or field — the same no-new-claims rule Roadmap already follows. If a lens
   has nothing to cite, its "what evidence supports" subsection should say so plainly rather
   than reaching for something tenuous.
2. **Every lens must include a non-empty "what evidence doesn't cover" statement** — specific
   to that lens's core question, not a generic disclaimer copy-pasted across all six. This is
   mandatory even when a lens has substantial supporting evidence to cite.
3. **No lens may ever assert or imply compliance, non-compliance, or certification status
   against any named regulation or standard** — GDPR, HIPAA, SOC 2, SOC 3, ISO 27001, ISO
   42001, CCPA, CPRA, FERPA, DPDP, or any other named framework, regardless of confidence
   level and regardless of what `policy_docs` contains. This is a hard rule, not a confidence
   judgment call:
   - **Permitted**: "This repository has no file matching a privacy-policy naming convention
     (`repository.policy_docs` contains no `privacy_policy` entry)."
   - **Permitted**: "`SECURITY.md` exists and states: [quote the actual content]."
   - **Never permitted, at any confidence level**: "This repository is GDPR compliant."
     "This repository is not SOC 2 ready." "This meets ISO 27001 requirements." Do not soften
     these into a Low-confidence version either — the rule is not about confidence, it is
     that this report has no evidence capable of supporting a compliance verdict at all, so no
     such claim is ever made, regardless of how it's hedged.
4. **Produce all seven lenses in the order listed below, every time.** Do not omit a lens
   because it has little to say — a lens with a short "what evidence supports" and a
   substantial "what evidence doesn't cover" is a complete, valid entry, not an incomplete one.

## The seven lenses

### Security

**What this audience cares about**: attack surface and incident-response readiness — what
could go wrong, and who could actually respond if it did.

Draw "what evidence supports" from `evidence.security`'s secrets and dependency-vulnerability
findings, and from `evidence.git.ownership`'s concentration (a single point of
incident-response failure is itself relevant here, not only a financial fact). State "what
evidence doesn't cover": this report has no evidence of actual incident history, access
control configuration, or runtime security posture — only what is visible in the source tree
and its history.

### Investor / Technical Due Diligence

**What this audience cares about**: cost to inherit this codebase, and financial risk if key
people leave.

Draw from `evidence.git`'s ownership-concentration and commit-cadence findings,
`evidence.architecture`'s coupling findings (cost to change something safely), and
`evidence.repository.ai_usage` (provider/framework dependency as a switching-cost or
vendor-lock-in risk). State "what evidence doesn't cover": this report has no revenue,
market, customer, or valuation data of any kind — none of that exists in a source repository,
and no claim about investability or valuation is made anywhere in this report.

### Onboarding / New Contributor

**What this audience cares about**: where to start, and what is risky to touch on day one.

Draw from `evidence.architecture.clusters` (a structural map of the codebase's natural
groupings) and `evidence.repository`'s high-fan-in and god-module findings (places worth
extra care before changing). State "what evidence doesn't cover": this report has no
information about team norms, code review expectations, or who to ask questions of — only the
code's own structure.

### Engineering Manager / Process

**What this audience cares about**: team practice health — whether work is flowing smoothly,
not whether any individual represents a financial risk.

Draw from `evidence.git.commit_cadence`'s trend, unmerged or stale-branch findings (work
started but not landed — a process bottleneck signal), and ownership distribution reframed as
a team-practice question ("is contribution concentrated in a way that could bottleneck
review or continuity") rather than the Investor lens's financial framing of the same numbers.
State "what evidence doesn't cover": this report has no visibility into actual review
turnaround time, meeting cadence, or process outside what is reconstructable from commit and
branch timestamps.

### Documented Policy & Governance Gaps

**What this audience cares about**: what this repository's own paper trail documents, and
where it is silent.

Draw "what evidence supports" from `evidence.repository.policy_docs` directly: for each
detected entry, read that file's actual content (you have file access to this repository) and
quote the relevant part, citing the file by name. For each common policy area with a
corresponding marker category (`license`, `security_policy`, `privacy_policy`,
`code_of_conduct`, `contributing_guide`, `governance_policy`) that has no detected entry in
`policy_docs`, state plainly that no such file was found in this repository. State "what
evidence doesn't cover" explicitly and completely, every time: whether any documented policy
is actually followed in practice, whether the organization holds any certification, and
compliance status with any named regulation — none of that is answerable from a source
repository, and per the mandatory rules above, no claim about it is ever made here.

### Documentation Quality

**What this audience cares about**: whether the code that matters most is explained anywhere.

Draw from `evidence.repository.policy_docs`'s `readme` and `contributing_guide` entries (cite
the file if found; state plainly if not found) and `evidence.repository`'s high-fan-in and
god-module findings, reframed as "these are the modules most worth documenting, given how
many other files depend on them." State "what evidence doesn't cover": this report has no
visibility into inline comments, docstrings, or the accuracy of any existing documentation —
only whether top-level documentation files exist.

## What this section does not produce

No compliance verdicts of any kind, for any named regulation or standard. No numeric scores.
No ranking of the seven lenses against each other. No claims not already stated earlier in the
report.
