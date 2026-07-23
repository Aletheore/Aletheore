# Branch Protection

**Purpose:** Define the minimum repository protection policy for launch hardening.
**Status:** Active baseline
**Owner:** TODO
**Related Documents:** [README.md](README.md), [DEPLOYMENT-VERIFICATION.md](DEPLOYMENT-VERIFICATION.md), [../../SECURITY.md](../../SECURITY.md)
**Last Updated:** 2026-07-23

## Purpose

This document defines the repository rules that should protect the default branch before treating hosted Aletheore as paid-beta ready.

## Protected Branch

Protect the default branch, currently `master`.

## Required Checks

Require the following checks before merge:

- `pytest (3.11)`
- `pytest (3.12)`
- `pytest`
- `Prettier`
- `Markdown lint`
- `Spell check`
- `Link check`
- `Python dependency licenses`
- `Image scan (app-server)`
- `Image scan (scan-worker)`
- `SBOM (app-server)`
- `SBOM (scan-worker)`

If a check name changes, update this document and the repository rules together.

## Review Rules

Use these minimum rules:

- Require pull request before merging.
- Require at least one approving review for production-affecting changes.
- Dismiss stale approvals when new commits are pushed.
- Require conversation resolution.
- Block force pushes to the protected branch.
- Block branch deletion.
- Require linear history only if the project decides to stop preserving reviewed milestone commits.

## Admin Rules

Repository administrators may bypass protections only for urgent security or recovery work. Any bypass should be documented in the pull request, incident record, or deployment record.

## Deployment Coupling

Merging to `master` does not prove production is current. Use [DEPLOYMENT-VERIFICATION.md](DEPLOYMENT-VERIFICATION.md) before claiming a change is live.

