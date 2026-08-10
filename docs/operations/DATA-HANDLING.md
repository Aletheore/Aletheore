# Hosted Data Handling

**Purpose:** Define the baseline hosted data-handling posture for Aletheore.
**Status:** Active baseline
**Owner:** Arihant Kaul
**Related Documents:** [README.md](README.md), [INCIDENT-RESPONSE.md](INCIDENT-RESPONSE.md), [../../SECURITY.md](../../SECURITY.md)
**Last Updated:** 2026-08-10

## Purpose

This document describes what the hosted GitHub App may process and what must remain explicit to users.

## Data Categories

| Category | Examples | Handling |
| --- | --- | --- |
| GitHub installation data | Installation ID, account login, repository names. | Stored for routing scans, entitlements, and dashboard views. |
| Repository evidence | Scan output, findings, endpoint metadata, dependency evidence, code citations. | Stored as derived audit evidence and rotated by repository history retention. |
| API tokens | CLI/managed-audit tokens. | Store only token hashes; show raw token only at creation. |
| Sessions | GitHub login session and encrypted access token. | Encrypted at rest and expired by session TTL plus cleanup job. |
| Alert targets | Slack/Teams webhook URL, health-check base URL. | Validate before storage; treat as sensitive operational configuration. |
| LLM usage | Prompt/completion token counts and derived cost. | Store aggregate cost for spend-cap enforcement. |
| Deletion audit | Installation ID, account login, requesting actor, counts, timestamp. | Retained after a purge as proof of erasure; never itself deleted. |
| Admin action audit | Actor, action name, non-secret detail (e.g. a token's label, not the token itself), timestamp. | Retained while the installation exists; cascades away if the installation is deleted - unlike the deletion audit, there is no requirement for this to outlive the account it documents. |

## Data Transfer Rules

- Deterministic scans should remain local unless a hosted feature explicitly requires upload.
- Managed audits may send evidence to the hosted service and configured LLM provider.
- Local CLI provider usage must stay consent-based.
- Alerts, reviews, audits, and queries should resolve back to code evidence: file, line, symbol, owner, commit, dependency, and risk where available.

## Deletion Baseline

Deletion is self-serve and available on every plan, including Community and
lapsed subscriptions. Two paths trigger the same purge:

- **Dashboard.** Settings → Delete all data, confirmed by typing the account
  login. Requires a session holding GitHub admin rights on the installation;
  it is deliberately not gated on plan or seat, so a customer whose payment
  failed can still erase their data.
- **Uninstalling the GitHub App.** The `installation.deleted` webhook runs the
  identical purge.

Scope of a purge, in one transaction:

| Data | Outcome |
| --- | --- |
| Installation-scoped rows (evidence, findings, docs, wiki, tokens, seats, health targets, billing links) | Deleted. Every such table declares `ON DELETE CASCADE` against `installations`. |
| `github_user_emails`, `sessions` for anyone who has accessed the installation | Deleted **only** for people left with no other Aletheore installation. A user who administers two orgs is not logged out of the second when the first deletes itself. "Anyone who has accessed" is tracked in `installation_access_log`, on every plan - not `installation_members`, which exists only for paid-seat billing and would silently miss every Community-plan user. |
| `cli_telemetry_events` | Retained. Keyed by a rotating anonymous ID with no link to an installation or a person. |
| `demo_scan_rate_limits` | Retained. IP-keyed abuse control for the unauthenticated demo scanner, not customer data. |
| `data_deletion_log` | Written, never deleted. See below. |

Every purge writes one `data_deletion_log` row: installation ID, account login,
the actor who requested it, repo and user counts, and a timestamp. That table
deliberately carries no foreign key to `installations` — a cascading one would
destroy the record of the deletion as part of the deletion itself. It is the
one place an account login outlives the account, and it is the audit trail.

## Export Baseline

Export is self-serve, reachable from Settings alongside Delete all data, and
carries the same authorization as deletion - a session with GitHub admin
rights on the installation, no plan or seat gate. It returns a single
downloadable JSON file: account and plan, connected repos and their latest
findings, team members, health check targets, and current-month usage.

Deliberately excluded, because a leaked export must never become a working
credential: API tokens are listed by label and ID only (`list_api_tokens`
never returns the hash, let alone the raw token); the alert webhook URL is
omitted entirely, since a Slack-style webhook URL embeds a secret in its own
path. Requesting an export writes one `admin_action_log` row (see below).

## Admin Action Audit Log

Every admin-mutating dashboard action other than deletion (which has its own
permanent `data_deletion_log`, see above) writes one `admin_action_log` row:
who did it, what the action was, and non-secret detail useful for reading the
log later. Covers member add/remove, API token create/revoke, webhook URL
changes, the Docs-repo-commit and managed-audit-suggestions toggles, health
check target add/remove, extra-seat purchase/removal requests, and data
exports.

The detail column is held to the same rule as the export: never a secret. A
token action logs its label and ID, never the token or its hash; a webhook
URL change logs only whether a URL is now set, never the URL itself.

No customer-facing viewer exists yet - the log is queryable (support,
compliance, incident response) but not yet surfaced in the dashboard UI,
same status the deletion audit log has had since it shipped.

## Open Controls

- Customer-facing retention settings.
- Enterprise data-processing addendum.
- Region and subprocessor disclosures.
- Customer-facing viewer for the admin action log (currently query-only).
