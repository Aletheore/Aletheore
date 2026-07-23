# Security

**Purpose:** Define how security issues should be reported and handled.
**Status:** Active baseline
**Owner:** TODO
**Related Documents:** [CONTRIBUTING.md](CONTRIBUTING.md), [GOVERNANCE.md](GOVERNANCE.md), [.github/ISSUE_TEMPLATE/security-report.md](.github/ISSUE_TEMPLATE/security-report.md), [docs/operations/DATA-HANDLING.md](docs/operations/DATA-HANDLING.md), [docs/operations/INCIDENT-RESPONSE.md](docs/operations/INCIDENT-RESPONSE.md)
**Last Updated:** 2026-07-23

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

VDP governance documents are security-relevant only when the issue affects repository authority, release trust, or contributor safety.

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

## Current Limitations

- This project is not yet SOC 2 certified.
- A formal enterprise security packet is not yet complete.
- Public vulnerability disclosure process details may evolve before broad enterprise launch.
