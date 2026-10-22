---
name: gem-devops-guidelines
description: Design or review infrastructure, deployment, CI/CD, Docker, Kubernetes, health checks, rollback, feature flags, production readiness, and mobile release workflows. Use for DevOps, platform, container, pipeline, or release tasks.
skill_id: devops/gem-devops-guidelines
title: Gem DevOps Guidelines
source_url: https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/gem-devops-guidelines
license: MIT
shelf: ship-operate
aletheore: {"id": "devops/gem-devops-guidelines", "shelf": "ship-operate", "category": "devops", "origin": "https://github.com/github/awesome-copilot/tree/cb64ad12e32737ae764becd6885462e9b4c7cb00/skills/gem-devops-guidelines", "license": "MIT", "license_source": "LICENSE", "upstream_commit": "cb64ad12e32737ae764becd6885462e9b4c7cb00", "quality": 3, "curation_note": "infra and deployment guidelines", "needs_tool": null, "vet": {"injection_pattern_hits": [], "portability_flags": ["slash"], "companion_files": 0, "has_scripts": false}, "curated_on": "2026-10-18", "requires": [], "adapted": {}, "status": "ready", "mentions_agents": []}
---

# DevOps Guidelines

Apply only sections relevant to workload/provider/environment/acceptance criteria. Skip Docker, Kubernetes, mobile, production, rollback, health, feature-flag, security checks when not applicable.

## Deployment strategy

- Rolling (default): gradual, zero-downtime replacement.
- Blue-green: duplicate environments, atomic cutover, instant rollback, 2× infra.
- Canary: route small % first; requires traffic splitting.

## Docker

- Pin base-image tags (`node:22-alpine`); NEVER `:latest`.
- Multi-stage builds + non-root user. Copy deps first for caching.
- `.dockerignore`: `node_modules`, `.git`, tests. Define `HEALTHCHECK` + resource limits.

## Kubernetes

Configure startup, readiness, liveness probes with workload-appropriate initial delays/thresholds.

## CI/CD

- PR: lint -> typecheck -> unit -> integration -> preview.
- Main: build -> staging -> smoke -> production.

## Health and shutdown

- Simple: `GET /health` -> `{ "status": "ok" }`.
- Detailed: dependencies, uptime, version.
- Services MUST expose meaningful health + gracefully handle `SIGTERM` when workload requires it.

## Configuration

Use environment variables (Twelve-Factor), separated by environment. Validate at startup + fail fast. NEVER commit secrets or hard-code `NODE_ENV=production`.

## Rollback

- Kubernetes: `kubectl rollout undo`.
- Vercel: `vercel rollback`.
- Docker: redeploy previous pinned image.

## Feature Flags

- Lifecycle: create -> enable -> 5% -> 25% -> 50% -> 100% -> remove flag + dead code.
- Every flag MUST have owner, expiration, rollback trigger. Remove within two weeks.

## Checklists

- Pre-deploy: passing tests, code review, env vars, migrations, rollback plan.
- Post-deploy: healthy, monitored, old pods terminated, outcome documented.
- Production: passing tests; no hardcoded secrets; JSON logs; meaningful health; pinned versions; validated env vars; resource limits; TLS; CVE scan; CORS; rate limiting; CSP/HSTS/X-Frame-Options; tested rollback; runbook; on-call.
- Apply security/CVE checks to executable or security-sensitive workloads.

## Mobile Deployment

- EAS: `eas build:configure`; `eas build -p ios|android --profile preview`; `eas update --branch production`; `--auto-submit`.
- Fastlane: iOS `match`/`cert`/`sigh`/`pilot`; Android Gradle/`supply`.
- Credentials in env/secret storage, never Git. Automate iOS signing with `fastlane match`; use `keytool` + Google Play App Signing for Android.
- TestFlight: internal instant; external 90 days/100 testers. Google Play: internal/beta/production. Expect 1–7 days review.
- Rollback: EAS `eas update:rollback`; native release -> revert build; store release -> reduce phased rollout.
