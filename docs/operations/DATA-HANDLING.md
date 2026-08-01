# Hosted Data Handling

**Purpose:** Define the baseline hosted data-handling posture for Aletheore.
**Status:** Active baseline
**Owner:** Arihant Kaul
**Related Documents:** [README.md](README.md), [INCIDENT-RESPONSE.md](INCIDENT-RESPONSE.md), [../../SECURITY.md](../../SECURITY.md)
**Last Updated:** 2026-07-23

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

## Data Transfer Rules

- Deterministic scans should remain local unless a hosted feature explicitly requires upload.
- Managed audits may send evidence to the hosted service and configured LLM provider.
- Local CLI provider usage must stay consent-based.
- Alerts, reviews, audits, and queries should resolve back to code evidence: file, line, symbol, owner, commit, dependency, and risk where available.

## Deletion Baseline

Deletion capability is not yet fully productized. Until then, deletion requests must be handled manually by installation/repository scope and recorded as an operational event.

## Open Controls

- Customer-facing retention settings.
- Self-serve data deletion.
- Audit logs for admin actions.
- Enterprise data-processing addendum.
- Region and subprocessor disclosures.
