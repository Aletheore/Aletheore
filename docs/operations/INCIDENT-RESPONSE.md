# Incident Response

**Purpose:** Define the baseline incident response process for hosted Aletheore.
**Status:** Active baseline
**Owner:** Arihant Kaul
**Related Documents:** [README.md](README.md), [SLOS.md](SLOS.md), [DATA-HANDLING.md](DATA-HANDLING.md)
**Last Updated:** 2026-07-23

## Purpose

This runbook defines the minimum response process for outages, security events, data exposure risk, queue failures, and broken GitHub App behavior.

## Severity Levels

| Severity | Definition | Initial Target |
| --- | --- | ---: |
| SEV-1 | Security compromise, data exposure, widespread outage, or broken authentication. | 15 minutes |
| SEV-2 | Major feature unavailable, queue backlog blocking paid users, or repeated failed deployments. | 1 hour |
| SEV-3 | Degraded performance, partial alerting failure, or isolated customer impact. | 1 business day |

## First Response

For every incident:

1. Identify affected service, commit, deployment time, and customer impact.
2. Freeze non-essential deployment changes.
3. Capture logs, queue state, container state, and database health.
4. Decide whether to roll back, disable a feature, or continue forward with a fix.
5. Record timeline and resolution notes.

## Required Evidence

Incident notes should include:

- Start and detection time.
- Responsible owner.
- Affected repositories/installations if known.
- Request IDs or job IDs when available.
- Deployed commit.
- Mitigation chosen.
- Follow-up task list.

## Customer Communication

For paid users, communicate when an incident affects audit delivery, alerting, health checks, authentication, or data confidentiality.
