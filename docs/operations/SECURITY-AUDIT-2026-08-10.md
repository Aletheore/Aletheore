# Security Audit — 2026-08-10

**Scope:** Repository and local-execution audit of commit `f0341aa` (backend commit that landed
as PR #183). Did **not** touch the running production service - claims about deployed
infrastructure state are separately tracked in
[`DEPLOYMENT-VERIFICATION.md`](DEPLOYMENT-VERIFICATION.md).
**Method:** 2,160 tests executed (1043 CLI/scanner + 1117 backend, 8 skipped) against live
Postgres and Redis, not just read. Measured coverage 93% (CLI) / 91% (backend) against an 85% CI
gate.
**Verdict:** No for immediate release; yes for roughly one engineer-week of hardening, no
re-architecture required.
**Supersedes:** [`../../STARTUP_AUDIT_REPORT.md`](../../STARTUP_AUDIT_REPORT.md), which is stale
(references `prototype/` and `constitution/` directories that no longer exist in this repo).

## Findings: 12 total, 0 critical, 3 high

Four reproduced directly, not inferred from reading code:

1. **SSRF + local file read via the `aletheore_healthcheck` MCP tool.** `run_healthcheck` did no
   URL scheme validation and urllib's opener retained the `file://` handler - a local file's
   contents were read through it. **Fixed:** PR #188 (merged 2026-08-10) - `base_url` must now be
   `http`/`https`.
2. **ReDoS in `aletheore_search`.** Measured ~23s for `(a+)+$` against one 29-character line, run
   per line across every file; an invalid pattern also raised an uncaught `re.error`. **Fixed:**
   PR #190 - regex mode now runs in a subprocess under a 5s deadline (thread-based and
   `signal.alarm`-based timeouts were tried first and confirmed empirically not to work in this
   codebase's actual call context - see the code comment in `mcp_server.py`).
3. **Scanning a repo mutates it.** `_ensure_aletheore_dir_gitignored` wrote a root `.gitignore`
   after the file-counting walk had already run, so a re-scan of an untouched repo reported a
   different `scanned_files` count than the first scan (22 → 23 → 23, confirmed empirically).
   **Fixed:** PR #191 - the write now happens before any counting walk, so every scan (including
   the first) is self-consistent.
4. **Six detectors emit AIR arrays in filesystem-traversal order**, not sorted - the same repo
   produces differently-ordered evidence on APFS vs ext4. **Fixed:** PR #192.

Plus **OPS-01** (backup script not scheduled): the repo-only grep that found this couldn't see
host crontab state. Live check found the crontab entry *did* exist but had never actually fired
since being installed - confirmed and fixed live (not a git change; documented in
[`DEPLOYMENT-VERIFICATION.md`](DEPLOYMENT-VERIFICATION.md#paddle-webhook-destination-live-account-config-not-in-git)
and its own section there).

## Assessment

The security engineering in the hosted backend (`github-app/`) is stronger than the CLI/MCP
surface it doesn't cover - e.g. a docker-socket-proxy was empirically tested and rejected because
`POST=1` already permits bind mounts, and `_run_git` scrubs credentials out of
`CalledProcessError` before it reaches alert emails. The pattern across these findings: the
backend is held to a visibly higher bar than the CLI/MCP tools, and hardening already applied on
the backend side (e.g. `validate_external_https_url`) hadn't crossed over to the CLI's own
`healthcheck.py`.

## Status of the two lower-severity findings (not security bugs, correctness/reproducibility)

Findings 3 and 4 above are fixed as part of this same pass, not deferred - they were flagged as
"~3 hours combined" and worth bundling with the security fixes since they're defects in the exact
determinism property this product sells.
