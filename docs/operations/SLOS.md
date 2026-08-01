# Service Level Objectives

**Purpose:** Define initial reliability targets for hosted Aletheore.
**Status:** Active baseline
**Owner:** Arihant Kaul
**Related Documents:** [README.md](README.md), [INCIDENT-RESPONSE.md](INCIDENT-RESPONSE.md), [DEPLOYMENT-VERIFICATION.md](DEPLOYMENT-VERIFICATION.md)
**Last Updated:** 2026-07-23

## Purpose

These targets are internal engineering objectives, not customer SLAs.

## Initial Objectives

| Area | Objective | Measurement |
| --- | --- | --- |
| Web app availability | 99.5% monthly availability for authenticated hosted routes. | Successful HTTP responses from external health checks. |
| Webhook intake | 99% of valid GitHub webhooks acknowledged within 5 seconds. | App logs by path and status. |
| Queue processing | 95% of PR scan jobs start within 2 minutes during normal load. | Queue depth and started-job metrics. |
| Health checks | 95% of configured endpoint checks complete within the configured timeout window. | Health sweep job results. |
| Managed audits | 95% of accepted managed-audit jobs complete or fail explicitly within 15 minutes. | RQ job status and worker logs. |
| Recovery | Restore drill succeeds into a non-production database at least monthly. | Restore drill record. |

## Alert Candidates

- App server unavailable for 2 consecutive checks.
- Queue depth above threshold for 10 minutes.
- Failed jobs above threshold for 10 minutes.
- PostgreSQL unhealthy.
- Redis unavailable.
- Backup missing for more than 24 hours.
- Monthly LLM spend reaches 80% of configured cap.

## Review Cadence

Review targets after every production incident and before expanding beyond controlled beta.
