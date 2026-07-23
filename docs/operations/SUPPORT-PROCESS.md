# Support Process

**Purpose:** Define the baseline support process for Aletheore users.
**Status:** Active baseline
**Owner:** TODO
**Related Documents:** [README.md](README.md), [INCIDENT-RESPONSE.md](INCIDENT-RESPONSE.md), [DATA-HANDLING.md](DATA-HANDLING.md), [../../SECURITY.md](../../SECURITY.md)
**Last Updated:** 2026-07-23

## Purpose

This document defines how user issues should be triaged during controlled beta.

## Support Categories

| Category | Examples | Route |
| --- | --- | --- |
| Security | Token exposure, cross-tenant data, webhook spoofing, unexpected code/evidence transfer. | Private security report. |
| Production incident | Hosted app unavailable, webhooks not processed, paid audit jobs stuck, alert delivery broken. | Incident response process. |
| Billing or plan | Seat count, entitlement mismatch, subscription change, cancellation question. | Owner-handled support queue. |
| Product bug | Incorrect finding, missing code evidence, broken PR comment, CLI crash. | GitHub issue or support queue depending sensitivity. |
| Feature request | New language support, new provider, dashboard improvement. | Public issue or discussion. |

## Triage Rules

- Treat security and data-handling reports as private by default.
- Treat paid-user blocked workflows as urgent.
- Ask for repository names, request IDs, job IDs, and timestamps when available.
- Do not ask users to paste secrets or private source code into public issues.
- Convert repeated support issues into tracked product or operations work.

## Response Targets

| Category | First Response Target |
| --- | ---: |
| Security | See [../../SECURITY.md](../../SECURITY.md). |
| Production incident | Same business day for controlled beta. |
| Billing or plan | 2 business days. |
| Product bug | 3 business days. |
| Feature request | Best effort. |

## Escalation

Escalate immediately when a report suggests:

- Customer data exposure.
- Authentication or authorization bypass.
- Production outage.
- Paid entitlement failure affecting active users.
- Incorrect security findings that could block a production merge.

