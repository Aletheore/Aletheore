# Security

**Purpose:** Define how security issues should be reported and handled.
**Status:** Active baseline
**Owner:** TODO
**Related Documents:** [.github/ISSUE_TEMPLATE/security-report.md](.github/ISSUE_TEMPLATE/security-report.md), [docs/operations/DATA-HANDLING.md](docs/operations/DATA-HANDLING.md), [docs/operations/INCIDENT-RESPONSE.md](docs/operations/INCIDENT-RESPONSE.md)
**Last Updated:** 2026-08-10

## Purpose

This document defines the current security reporting and handling process for Veridion and the hosted Aletheore service.

## Supported Scope

Security reports may cover:

- The local Aletheore CLI and scanner.
- The GitHub Action.
- The hosted GitHub App under this repository.
- Managed audit, Flash review, health-check, dashboard, and webhook handling.
- Repository evidence handling, data retention, token handling, and alert delivery.
- CI, deployment, and container security configuration.

## Reporting

Do not report suspected vulnerabilities through public issues.

Use GitHub private vulnerability reporting when available for this repository. If private reporting is unavailable, contact the project owner directly and include enough detail to reproduce the issue without exposing third-party secrets.

Include:

- Affected component.
- Steps to reproduce.
- Expected impact.
- Whether the issue affects hosted users, local-only users, or CI.
- Any relevant request IDs, job IDs, repository names, or timestamps.

Do not include live customer secrets, private repository contents, or credentials in the report.

## Response Targets

| Severity | Examples | First Response Target |
| --- | --- | ---: |
| Critical | Authentication bypass, data exposure, secret leakage, remote code execution, production compromise. | 24 hours |
| High | Cross-tenant data access, webhook forgery, SSRF to private infrastructure, token misuse. | 48 hours |
| Medium | Denial of service, weakened isolation, unsafe defaults, missing authorization on limited-scope routes. | 5 business days |
| Low | Hardening gaps, misleading docs, non-exploitable configuration weakness. | Best effort |

These are response targets, not contractual SLAs.

## Hosted Data

The hosted service may process repository-derived evidence and operational metadata. See [docs/operations/DATA-HANDLING.md](docs/operations/DATA-HANDLING.md) for the current baseline.

Reports involving hosted data are security-sensitive when they involve:

- Cross-installation or cross-repository data access.
- Exposure of API tokens, GitHub tokens, webhook URLs, session tokens, or app private keys.
- Unexpected transfer of source-derived evidence to third-party providers.
- Failure to delete or isolate customer data after an explicit operational request.

## Disclosure

Please give maintainers a reasonable opportunity to investigate and patch before public disclosure. Coordinated disclosure timing will depend on impact, exploitability, and whether active abuse is suspected.

## Inbound Webhook Handling

Both inbound webhook paths authenticate before doing anything else, and both
deduplicate deliveries afterwards in a shared `webhook_deliveries` ledger
retained 30 days. The claim is a single atomic `INSERT … ON CONFLICT DO
NOTHING`, so two deliveries of one event arriving together cannot both win it.
The ledger is keyed by `(source, delivery_id)`, keeping GitHub and Paddle
identifiers in separate namespaces.

### GitHub

Authenticated by HMAC-SHA256 over the raw body, compared with
`hmac.compare_digest`; a failing request is rejected with 401. GitHub
signatures carry no timestamp, so the ledger is this path's only replay
protection. Deliveries are deduplicated on the `X-GitHub-Delivery` GUID:

- **Automatic retries** reuse the GUID and are suppressed, so a delivery that
  already succeeded cannot enqueue a second scan or review.
- **Replays of a captured payload** carry a GUID already on file and are
  likewise suppressed. The header is mandatory — a request without it is
  rejected with 400 rather than processed, so it cannot be stripped to bypass
  this.
- **Manual redelivery** from the GitHub UI mints a fresh GUID and is processed,
  which is the intended operator behavior.
- **Failed processing** releases the claim before returning the error, so
  GitHub's retry is not mistaken for a duplicate and the event is not lost.

### Paddle

Authenticated by HMAC-SHA256 over `timestamp:body`, with the timestamp checked
to a 5-second tolerance and an allowlist check against Paddle's published
source addresses. Because the timestamp is inside the signed payload, replay of
a captured payload is bounded to that 5-second window.

Deduplication on `event_id` therefore exists here for concurrency rather than
replay: the subscription handler reads an installation's current plan, then
writes it, and gates a pair of expensive full AIRview and Docs builds on that
read having been `free`. Two deliveries of one event arriving together would
both observe `free` and both enqueue those builds. The atomic claim is what
makes that gate hold. A payload without an `event_id` is rejected with 400
rather than processed without a dedupe check.

### Both paths

The claim is taken only after signature verification, so an unauthenticated
caller cannot poison the ledger with invented identifiers to suppress the
genuine deliveries that follow. A handler that raises releases its claim before
the error propagates, so the provider's retry is not mistaken for a duplicate —
losing an event outright is a worse failure than processing one twice.

## Inbound Ingestion Limits

Two endpoints accept caller-supplied bodies outside the webhook paths above, and
each is bounded on three axes: request size, request rate, and accepted schema.

| | `/v1/telemetry` | `/v1/runtime-events` |
| --- | --- | --- |
| Auth | None — the CLI has no account to authenticate with | Bearer API token, paid plans only |
| Body cap | 2 KiB | 256 KiB |
| Rate limit | 120/hour per client IP | 300/hour per installation |
| Schema | Unknown fields rejected; both fields length-bounded | Unknown top-level fields rejected; `event` deliberately open |
| Retention | 365 days, swept on the scheduler tick | n/a — events are not stored, only correlated |

**Body caps are enforced in middleware, before routing.** A check inside the
handler would reject a payload the server had already read and parsed, which is
the cost the cap exists to avoid. Requests declaring no `Content-Length` are
rejected with 411 rather than waved through — without a declared size the cap is
unenforceable before reading, and both legitimate clients always send one. The
caps are per-path on purpose: a global limit would break the managed audit API,
which accepts large evidence payloads by design.

**The two rate limits are keyed differently, deliberately.** Telemetry is keyed
by client IP, taken from the *last* `X-Forwarded-For` entry — the one the
reverse proxy appends. Earlier entries arrive with the request and are
attacker-controlled, so keying on them would let one caller mint unlimited
buckets by varying a header. Runtime events are keyed by installation instead:
that is the unit that owns the cost, and the only identity an authenticated
caller cannot change. Each accepted runtime event enqueues a job onto the same
`scans` queue that runs Flash reviews, AIRview builds, and managed audits, so an
uncapped caller there delays billed work for every installation sharing the
worker.

Both limits fail open if Redis is unavailable, matching the sign-in rate limit
and the Paddle IP allowlist: an outage should cost abuse protection, not
availability.

## What an Audit Signature Attests

Managed audit reports are signed with Ed25519 and can be checked at
`/v1/audit/{token}/verify`, which returns the signature, the public key, and a
`verified` boolean. That signature attests **provenance and integrity**: this
exact text was produced by Aletheore and has not been altered since. It does
not attest that every claim in the report is backed by a citation.

Those are different guarantees, and the difference is load-bearing. Every
finding in an audit resolves to evidence in your code. One optional section
does not: the model's own overall rating and improvement suggestions, appended
after the findings under a heading that names it as not evidence-backed. It is
excluded from the Citation Verification section's counts, because measuring
citation coverage across text that is allowed to speak without citations would
report the wrong number.

So that a reader cannot mistake one guarantee for the other, the verification
response states it directly:

| Field | Meaning |
| --- | --- |
| `verified` | The signature is valid for this content hash — provenance and integrity. |
| `fully_evidence_backed` | `true` when the report contains only cited findings. |
| `non_evidence_backed_sections` | Names any section that is not, empty when there are none. |

Installations that need reports containing only cited findings can turn the
section off in Settings → Managed audit content. With it off, the model call is
skipped entirely rather than made and discarded, so it costs nothing against
the monthly LLM spend cap, and the report's certificate reports
`fully_evidence_backed: true`.

## Current Limitations

- This project is not yet SOC 2 certified.
- A formal enterprise security packet is not yet complete.
- Public vulnerability disclosure process details may evolve before broad enterprise launch.
