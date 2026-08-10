# Hosted Data Handling

**Purpose:** Define the baseline hosted data-handling posture for Aletheore.
**Status:** Active baseline
**Owner:** Arihant Kaul
**Related Documents:** [README.md](README.md), [INCIDENT-RESPONSE.md](INCIDENT-RESPONSE.md), [../../SECURITY.md](../../SECURITY.md)
**Last Updated:** 2026-08-09

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

## Open Controls

- Customer-facing retention settings.
- Self-serve data export.
- Audit logs for admin actions other than deletion.
- Enterprise data-processing addendum.
- Region and subprocessor disclosures.
