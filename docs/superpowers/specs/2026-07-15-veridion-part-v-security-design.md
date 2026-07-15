# Veridion Part V (Security) v-next Design — Secrets & Dependency Vulnerabilities

**Status:** Draft, pending review
**Date:** 2026-07-15

## Problem

v1 (Parts I-III) is built, dogfooded on two real repos, and has already found real bugs and
real findings through actual use (an ahead/behind stub, an author-identity merge, 7 unpushed
commits on Procta). The v1 design spec explicitly deferred deciding what comes next to an
"Open Questions for Post-v1" section: Part IV (architecture pattern review) or Part V
(security review). This spec picks Part V, scoped down hard from the original 10-part
vision's sprawling security wishlist (OWASP Top 10, OWASP API Top 10, ASVS, CWE, MITRE
ATT&CK, NIST, SLSA, container/Kubernetes/cloud, IaC, auth/RBAC/JWT/OAuth/OIDC/SAML, and more)
to exactly two checks that fit the scanner-then-reasoning split already proven to work:
**secrets detection** and **known-vulnerable dependency versions**.

Both were chosen over architecture-pattern detection (the Part IV alternative) because they
are close to fully mechanical to *detect* — the scanner phase can compute them as hard facts,
the same way it computes the module graph or git branch staleness — with the agent needed
only for severity/triage judgment, not detection itself. Architecture-pattern classification
("is this hexagonal architecture") is inherently more judgment-heavy and a worse fit for the
project's core grounding discipline.

## Goals

- Detect likely secrets (API keys, tokens, private keys, generic high-entropy credential
  assignments) committed in the current working tree, with redacted evidence and an explicit
  placeholder-likelihood heuristic — never a raw secret value written to disk anywhere.
- Detect known-vulnerable pinned dependency versions (pip and npm, matching the scanner's
  existing Python/JS language coverage) via a live OSV.dev query, on by default.
- Extend `evidence.json` with a new top-level `security` block, parallel to `repository` and
  `git`, following the same "scanner computes facts, manual instructs the agent how to
  interpret them" pattern as Parts II and III.
- Ship a Part V manual section with the same two-tier structure as Part I: mandatory
  verification/redaction rules, then interpretation guidance.

## Non-Goals (this increment)

- OWASP Top 10 / API Top 10 / ASVS / CWE / MITRE ATT&CK / NIST / SLSA framework mapping.
- Container, Kubernetes, cloud, or IaC scanning.
- Authentication/authorization review (RBAC, ABAC, JWT, OAuth, OIDC, SAML, PKCE).
- Git-history secret scanning (working tree only this round).
- CVSS scoring or severity computation beyond what OSV.dev's own advisory data already
  reports.
- Auto-remediation, PR generation, or any write access to the scanned repository.

### Deferred to Future Parts — not the same as abandoned

Two different tiers, worth keeping distinct so this list doesn't imply a fixed order or
timeline:

- **Git-history secret scanning** is a natural, well-understood follow-up once working-tree
  secrets detection is proven — its shape is already clear (walk commit diffs instead of the
  current tree, same pattern ruleset, same redaction rules), it just wasn't included here to
  keep this increment small.
- **OWASP/CWE/MITRE framework mapping, container/Kubernetes/cloud/IaC scanning, and
  auth/RBAC/JWT/OAuth review** are each substantial enough to be their own future Part,
  deserving the same brainstorming/scoping pass Part IV vs. V just went through — not a
  guaranteed next-in-line queue, just acknowledged as real future scope rather than dropped.

## Evidence Schema Addition (`evidence.security`)

```json
"security": {
  "secrets": {
    "scanned_files": 452,
    "findings": [
      {
        "path": "app/config.py",
        "line": 12,
        "pattern": "aws_access_key_id",
        "match_preview": "AKIA****...Q7ZK",
        "likely_placeholder": false
      }
    ]
  },
  "dependency_vulnerabilities": {
    "checked": true,
    "reason": null,
    "findings": [
      {
        "ecosystem": "PyPI",
        "package": "fastapi",
        "installed_version": "0.110.0",
        "advisory_id": "GHSA-xxxx-xxxx-xxxx",
        "severity": "HIGH",
        "summary": "..."
      }
    ]
  }
}
```

When the vulnerability check is skipped (`--no-check-vulnerabilities`) or fails (offline,
timeout, OSV.dev unreachable), `checked: false` and `reason` names the actual cause — never a
silently empty `findings` list that looks identical to "checked and found nothing."

## Secrets Detection

- **Scope**: current working tree only (git history scanning deferred, see above).
- **Detection**: pattern-based against a small, curated ruleset — AWS access keys, GitHub
  tokens (`ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_` prefixes), Stripe live/test keys, PEM private-key
  headers, Slack tokens, Google API keys, and generic `PASSWORD=`/`SECRET=`/`API_KEY=`-style
  assignments with a long random-looking value. Same category of rules tools like gitleaks
  ship by default, a smaller curated set for this increment.
- **False-positive handling, fully deterministic**: paths matching `*.example`, `*test*`,
  `*fixture*`, `*mock*` naming conventions are still scanned (not silently excluded, matching
  the transparency precedent set by `unparseable_files`) but flagged `likely_placeholder:
  true`. Severity judgment stays a reasoning-phase job per the manual.
- **Redaction**: matched values are never stored in full. `match_preview` keeps a short
  prefix/suffix only. A real key never ends up sitting in plaintext in `evidence.json` or a
  committed report.

## Dependency Vulnerability Checking

- **On by default.** `veridion scan` (and `audit`) performs a live OSV.dev batch query
  covering pinned versions already parsed by Task 3's `detect.py` (`requirements.txt` for
  pip, `package.json`/`package-lock.json` for npm — matching existing language coverage, no
  new ecosystems this round).
- **`--no-check-vulnerabilities` opts out** — for CI runners without network egress, or
  repeated local scans where you don't want to hit OSV.dev's rate limits every run.
- **10-second timeout.** On timeout, connection failure, or non-2xx response: `checked:
  false`, `reason` states the actual failure, scan continues normally (this must never hang
  or fail the whole scan — dependency-vulnerability checking is additive, not load-bearing).
- **No local caching or offline snapshot this round** — every default-on scan makes a live
  call. This is a deliberate simplicity choice for v-next; a cached/rate-limited version is
  future scope if OSV.dev's free tier turns out to be a real constraint in practice.

## Part V Manual Content

Same two-tier structure as Part I:

- **Mandatory, primary**: never state a secret's real value — cite only `match_preview`.
  Never claim "no vulnerabilities found" when `dependency_vulnerabilities.checked` is
  `false` — say plainly that the check didn't run and why. Treat `likely_placeholder: true`
  as a hint to weigh, not an automatic dismissal — a real path could coincidentally match a
  test-naming convention.
- **Interpretation guidance, secondary**: a real-looking, non-`likely_placeholder` key is
  high severity by default. A `CRITICAL`/`HIGH` OSV advisory on a package that's actually
  imported somewhere per `repository.dependency_graph` outranks one on a dependency that
  appears in the manifest but isn't reachable from any scanned module — this is now possible
  to check because the module graph already exists from Task 4.

## Testing Strategy

- **Secrets detection**: unit tests against fixture files with planted real-looking and
  placeholder-looking patterns, verifying both the match itself and the
  `likely_placeholder`/redaction behavior. No network, no LLM — fully deterministic and
  CI-gated, same as the rest of the scanner.
- **Dependency-vulnerability parsing**: unit-tested against a mocked OSV.dev response (fixed
  JSON fixture) — no real network calls in CI. Separately, timeout/failure handling is
  unit-tested by mocking a connection error and confirming graceful `checked: false`
  degradation rather than a crash.
- **Live acceptance gate (manual, not CI-automated)**: run `veridion scan` against Procta
  with the default-on vulnerability check hitting the real OSV.dev API, and separately with
  `--no-check-vulnerabilities`, confirming both paths work — same dogfood-gate pattern as
  v1's Task 9.

## Success Criteria

1. `veridion scan /Users/arihantkaul/proctored-browser` (no flags) makes a real OSV.dev call
   by default and returns either real findings or a clean `checked: true, findings: []`.
2. `veridion scan /Users/arihantkaul/proctored-browser --no-check-vulnerabilities` skips the
   network call entirely and reports `checked: false, reason: "skipped (--no-check-vulnerabilities)"`.
3. Zero real secret values appear anywhere on disk (`evidence.json`, any report) — spot-check
   by grepping the output for known real values that should never appear.
4. At least one test-fixture-style credential in Procta's `tests/` files is correctly flagged
   `likely_placeholder: true`.
5. Killing network access mid-scan (or pointing at an unreachable host) produces `checked:
   false` with a real reason, not a hang and not a crash.

## Open Questions for Post-Part-V

- Whether a local caching layer or bundled offline advisory snapshot becomes necessary if
  OSV.dev rate limits turn out to matter in practice — not addressed here.
- Where git-history secret scanning and the three larger deferred security sub-domains
  (framework mapping, container/cloud/IaC, auth/RBAC/OAuth review) rank against each other
  and against Part IV (architecture) once this ships — not decided here.
