# Service Level Objectives

**Purpose:** Define initial reliability targets for hosted Aletheore.
**Status:** Active baseline
**Owner:** Arihant Kaul
**Related Documents:** [README.md](README.md), [INCIDENT-RESPONSE.md](INCIDENT-RESPONSE.md), [DEPLOYMENT-VERIFICATION.md](DEPLOYMENT-VERIFICATION.md)
**Last Updated:** 2026-08-12

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

The scheduler enqueues `run_ops_monitor_job` on the existing scan-worker
loop every health-sweep interval. Alerts use the existing Resend ops-email
destination (`EMAIL_REPLY_TO_ADDRESS`).

- Wired: App server unavailable for 2 consecutive checks
  (`ALETHEORE_APP_HEALTH_URL`, default `http://app-server:8000/healthz`).
- Wired: Queue depth above threshold for 10 minutes
  (`ALETHEORE_OPS_QUEUE_DEPTH_THRESHOLD`, default `25`).
- Wired: Failed jobs above threshold for 10 minutes
  (`ALETHEORE_OPS_FAILED_JOBS_THRESHOLD`, default `0`).
- Wired: Backup missing or stale for more than 24 hours
  (`ALETHEORE_BACKUP_DIR`, default `/app/backups`).
- Partly covered: PostgreSQL unhealthy and Redis unavailable are surfaced
  through `/healthz` and Docker healthchecks when the monitor can still run,
  but there is not yet an independent external pager for total Redis outage.
- Not wired yet: Monthly LLM spend reaches 80% of configured cap. The hard
  spend cap is enforced before LLM calls, but approaching-cap alerting still
  needs a separate telemetry pass.

## Review Cadence

Review targets after every production incident and before expanding beyond controlled beta.
