# Deterministic scanner evaluation — closing the LLM blind spot on stateful/adversarial trust bugs

## Context

A full night of testing (2026-09-20) established a real, repeatable gap: 8 models
(GLM-5.3-Flash, DeepSeek-V4-Flash, gpt-5.6-luna, gpt-4.1-mini, o4-mini,
NVIDIA-Nemotron-3.5-Lightning, google/gemma-4-26B, NVIDIA-Nemotron-3-Nano-30B),
across plain-prompt generation, a targeted "adversarial state change" prompt
rule, and full tool-executing agent mode with real repo access, all missed the
same two real security bugs from the validated Martian Code Review Bench gold
set:

- **grafana/grafana#103633** (High/security): `Check` trusts a cached
  permission grant immediately but re-derives a cached denial from a fresh DB
  lookup — a revoked-but-still-cached grant can outlive the revocation.
- **getsentry/sentry#67876** (Medium/security): OAuth `state` is derived from
  `pipeline.signature` (a static, predictable value) instead of a fresh
  per-request random value, defeating the replay-resistance the parameter
  exists for.

GLM-5.3-Flash (production's real generation model) was the best performer of
everything tested (2/4 hits on a small security-tagged spot-check) and stays
the production default — nothing tested beat it. Neither more context
(`referenced_symbol_context`), a rewritten safety rule targeting this exact
bug shape, nor real `grep`/`read` tool access to the full repo changed the
outcome on either bug, across any model. Full detail in this session's
transcript; no separate write-up of the model comparison exists yet beyond
this doc.

**Conclusion driving this evaluation**: this is not a model-selection problem.
It's worth checking whether a deterministic (non-LLM) layer can catch what no
LLM tested here could.

## What we actually need (not a generic SAST tool)

Both unsolved bugs share a shape neither general-purpose SAST rules nor paid
taint analysis are built for:

1. **grafana-103633**: an *asymmetric-trust invariant* — two code paths
   handling the same resource (a permission decision) apply different
   freshness/trust guarantees to opposite outcomes (grant vs. deny). This
   requires relating two non-adjacent code locations and recognizing an
   implicit domain invariant ("both outcomes should have the same staleness
   guarantee") that's never expressed syntactically anywhere in the code.
2. **sentry-67876**: a *security-token-randomness invariant* — a value used
   as a CSRF/OAuth `state` parameter must come from a real random source, not
   a static/derived value. This one **is** mechanically checkable: "is this
   variable, used as a `state`/`nonce`/CSRF-token parameter, assigned from a
   call to a recognized secure-random function or not" is a real, narrow,
   syntactic pattern.

Neither is what SonarQube/Semgrep/Bearer's free tiers do (single-location
pattern matching) or what paid taint analysis does (traces attacker-
controlled *input* to a dangerous *sink* — a different invariant entirely:
"don't let attacker data reach exec/query/eval unsanitized").

**What this means concretely**: off-the-shelf scanners are worth adding for
broad, generic coverage (typos, complexity, duplication, common CWE patterns,
secrets) — genuinely valuable, verified live below — but they will not close
this specific gap out of the box. Closing it needs two different things:

- A **narrow custom rule** for the token-randomness sub-class (buildable
  today in a pattern-matching DSL like Semgrep's — a few lines of YAML: flag
  any `state`/`csrf_token`/`nonce`-named parameter not assigned from a
  recognized RNG call).
- A **bespoke, project-aware check** for the asymmetric-cache-trust
  sub-class, in the same style as this codebase's existing
  `find_semantic_regressions` / `_detect_moved_code_blocks` (real AST/regex-
  anchored checks Aletheore already ships), not a generic tool — "does a
  corresponding invalidation path exist for the paired state transition" is
  project-aware, not a local syntactic pattern.

## SonarQube Community Build — tested live

**Setup**: `docker run -d --name sonarqube-test -p 9000:9000 sonarqube:community`
(version 26.9.0.129388), `brew install sonar-scanner` (8.1.0.6389), scanned
against a real PR-head checkout of grafana/grafana#103633 (base repo + the
real diff applied via `git apply`).

**License**: LGPL, true open source, free for commercial use. 17+ years old,
~11K GitHub stars, real large-scale adoption. Covers Go/TypeScript/Python (our
stack) plus 20+ languages total in the free Community Build. Excludes
C/C++/COBOL (commercial-only), irrelevant to us.

**Real scan results** (full Grafana monorepo, ~10,554 files, 4m20s scan +
~40s server processing):

| Metric | Count |
|---|---|
| Total issues | 15,846 |
| BUG | 552 |
| VULNERABILITY | 138 |
| CODE_SMELL | 15,156 |
| BLOCKER severity | 89 |
| CRITICAL severity | 2,335 |

**On the exact target file** (`pkg/services/authz/rbac/service.go`, where the
grafana-103633 bug lives): 4 issues, all pure code-quality smells (function
has 8 params, duplicated string literals). **Zero relevant to the golden
bug** — confirms the license/capability research exactly: no cross-function
trust-asymmetry checking in the free tier, no data-flow analysis of any kind
without a paid edition, and even paid taint analysis wouldn't have been the
right shape of check.

**One real, genuine side-catch**: lines 307-308 flagged two near-identical
duplicated string literals — `"unsupport resource"` (a real typo, missing
"ed") and `"unsupported resource"` (correct) — both used as literals multiple
times. None of the 8 LLMs tested tonight caught this, because it's exactly
the class of thing an LLM reviewer self-censors as "too minor to mention."
Real, concrete evidence of SonarQube's complementary value even though it
missed the target bug.

**Verdict**: real, mature, free, legitimate candidate for a broad
complementary deterministic layer. Not a fix for the specific gap that
motivated this evaluation.

## Semgrep — tested live

**Setup**: `pipx install semgrep` (1.177.0) — installed isolated via pipx
after a first attempt via `python3 -m pip install semgrep` corrupted the
shared environment (downgraded `mcp` below aletheore's `>=2.0` floor and
three `opentelemetry-*` packages pr-agent pins to exact versions; reverted
immediately, verified `flash_review.py`/`model_tiers.py` still import
cleanly afterward). Scanned the same target directory
(`pkg/services/authz/rbac/`) with `--config=auto` (1074 community rules
loaded, 131 applicable to Go, 42 files scanned).

**License**: LGPL-2.1 for the CE engine, true open source. Real note: a 2024
licensing controversy moved some components out of the permissive tier,
prompting a community fork (`Opengrep`) that preserves the original
permissive terms — worth knowing before depending on Semgrep's license
staying exactly where it is.

**Real scan result**: 1 finding — `go.lang.security.audit.xss.import-text-
template.import-text-template`, a generic WARNING that importing Go's
`text/template` package risks XSS if used to render untrusted content
(`pkg/services/authz/rbac/store/queries.go:6`). Legitimate generic finding,
**zero relevance** to the target bug. Confirms the single-file-only
architecture limitation from the earlier research: no rule category exists
for cross-function trust-asymmetry, free or paid.

## Bearer — tested live

**Setup**: `brew install bearer/tap/bearer` (2.1.1) — clean, self-contained
install, no dependency conflicts. Required git-tracked files to scan (errored
with "couldn't find any files to scan" against the untracked working tree;
resolved by committing the scratch checkout, which is disposable test-only
content, not real project history).

**License**: Elastic License v2 (ELv2) — source-available, **not** OSI-
approved open source, unlike SonarQube/Semgrep. Doesn't block internal use,
but worth flagging as a real difference in kind, not just degree.

**Real scan result**: 72 checks run (all of Bearer's default Go rules),
**zero failures detected**. This is Bearer working as designed, not a gap —
it's purpose-built for sensitive-data-flow/privacy compliance (PII, PHI,
GDPR/HIPAA-relevant data types), and a permission-cache implementation
legitimately has nothing in that category to find (it did detect exactly one
PII-adjacent data-type occurrence elsewhere in the scanned files, confirming
the detector itself is live and working). Correctly out of scope for this
bug class — the narrowest-purpose of the three tools tested.

## GitHub CodeQL — checked, disqualified on license (not tested live)

Pulled the real primary-source license text
(`raw.githubusercontent.com/github/codeql-cli-binaries/main/LICENSE.md`),
not a secondhand summary, since this is a real compliance question. Two
clauses rule it out for Aletheore's actual business model, more decisively
than any of the three tools above:

1. Automated/CI/CD analysis is only permitted for an Open Source Codebase
   that is specifically "hosted and maintained on GitHub.com" - narrower
   than SonarQube/Semgrep/Bearer, none of which care where the repo lives.
2. The disqualifying clause: the license explicitly forbids using the
   Software to *"provide or make available the Software as a hosted
   solution (whether on a standalone basis or combined, incorporated or
   integrated with other software or services) for others to use."* That
   is a direct, explicit prohibition on exactly what Aletheore does -
   offering scan results as part of a hosted product to customers -
   regardless of whether the target repo is open source or private.

The only way around either restriction is a paid GitHub Advanced Security
license, which would have to be *the customer's* license for *their* repo,
not something Aletheore could rely on generally. **Not evaluated further -
no live test run, since the license rules out the real use case before any
technical evaluation would matter.**

## Recommendation

All three are real, working, legitimate tools — confirmed hands-on, not just
from documentation. None caught the target bug, and none were ever going to:
that conclusion was reachable from the license/architecture research alone,
and the live runs just confirm it with real data instead of an inference.

They are not redundant with each other, though:

- **SonarQube**: broadest general-purpose coverage (bugs, code smells,
  duplication, complexity, generic vulnerability patterns) across the whole
  repo — real value demonstrated live (a genuine typo/duplicated-error-string
  bug at grafana-103633's service.go:307-308 that none of the 8 LLMs tested
  tonight bothered to flag). Best fit as the broad baseline layer.
- **Semgrep**: narrower out-of-the-box registry coverage than SonarQube on
  this sample, but the lowest-friction path to **custom rules** (plain YAML
  pattern DSL) — the right vehicle for the one sub-class from this
  investigation that's actually mechanically checkable: flagging a
  `state`/`csrf_token`/`nonce`-shaped variable that isn't assigned from a
  recognized secure-random call.
- **Bearer**: narrowest scope of the three (privacy/sensitive-data flows
  specifically) — real value only on repos that actually handle PII/PHI:
  worth having if/when Aletheore reviews code touching user data, not
  generally applicable to every PR the way the other two are.

**"Pull all three in" is a reasonable plan** if the goal is broad,
complementary, battle-tested deterministic coverage layered under/alongside
LLM review — genuinely additive, not overlapping. It is **not** a fix for
the specific gap that motivated this evaluation (asymmetric-trust and
token-randomness bugs); that still needs the two custom checks described in
"What we actually need" above, built by hand (one narrow Semgrep rule, one
bespoke Aletheore-native check in the `find_semantic_regressions` style).
Integration design (how results surface in PR comments, how findings get
deduplicated against LLM findings, cost/latency of running three real
scanners per review) is real, separate work not scoped by this evaluation.
