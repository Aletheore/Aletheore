# Dismiss/mute findings (gap item #6)

## Goal

Let a customer dismiss a specific secret or dependency-vulnerability finding
from the hosted dashboard so it stops being shown as open there and stops
appearing in future PR comments — without ever deleting or mutating the
underlying stored scan evidence.

## Scope decisions (confirmed)

- **Finding types**: secrets + dependency vulnerabilities only. Layer
  violations and dead code have no identity concept today and are lower
  security stakes; can extend the same pattern later.
- **Surface**: the hosted dashboard's existing Security findings page
  (`/dashboard/{org}/{repo}/security`) only. No new GitHub-native
  interaction (no reactions/checkboxes — nothing like that exists anywhere
  in the app today, so this would be new surface area for v1).
- **Storage**: a new hosted-only DB table. No automated write-back to the
  customer's `.aletheore.json` (no commit-authoring flow, no repo write
  access needed). A dashboard dismissal has no effect on a local
  `aletheore scan`/`aletheore diff` run — genuinely separate mechanisms,
  same as the existing `accepted_secrets` (repo-committed baseline) staying
  untouched and still working exactly as it does today, composed with this
  new layer rather than replaced by it.

## Why the PR-comment filtering can't reuse severity_threshold's pattern

`severity_threshold` (shipped in PR #147) reads `.aletheore.json` directly
inside `history.py::compute_diff()`, because that file is on disk and
readable by both the CLI and the hosted worker (which runs `aletheore scan`/
`aletheore diff` as a subprocess against a real checkout). `dismissed_findings`
only exists in the hosted Postgres database — the plain `aletheore` package
must never depend on it (same one-way dependency rule as `app_server` never
importing `scan_worker`). So `compute_diff()` stays completely unaware of
dismissals; filtering happens as a **post-processing step** in
`scan_worker/jobs.py`, after `compute_diff()` returns and before the PR
comment is built.

## 1. Migration `033_dismissed_findings.sql`

```sql
CREATE TABLE IF NOT EXISTS dismissed_findings (
    id              BIGSERIAL PRIMARY KEY,
    installation_id BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    repo_full_name  TEXT NOT NULL,
    finding_type    TEXT NOT NULL CHECK (finding_type IN ('secret', 'vulnerability')),
    identity_key    TEXT NOT NULL,
    reason          TEXT,
    dismissed_by    TEXT NOT NULL,
    dismissed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (installation_id, repo_full_name, finding_type, identity_key)
);

CREATE INDEX IF NOT EXISTS dismissed_findings_lookup
    ON dismissed_findings (installation_id, repo_full_name);
```

## 2. Identity keys (new module `app_server/dismissed_findings.py`)

Pure function, no DB dependency — shared by both `app_server` (async
dashboard routes) and `scan_worker` (sync PR-comment job), same
cross-package direction already established (`scan_worker` imports from
`app_server`, never the reverse):

```python
def finding_identity_key(finding_type: str, finding: dict) -> str:
    """Canonical identity string for a finding, used both to store a
    dismissal and to check whether a fresh finding matches one. Uses the
    same field tuples history.py already uses for new/resolved diffing:
    (path, pattern, match_preview) for secrets, (ecosystem, package,
    advisory_id) for vulnerabilities. \\x1f (unit separator) joins fields -
    not a character any of these fields would plausibly contain."""
    if finding_type == "secret":
        return f"{finding['path']}\x1f{finding['pattern']}\x1f{finding['match_preview']}"
    if finding_type == "vulnerability":
        return f"{finding['ecosystem']}\x1f{finding['package']}\x1f{finding['advisory_id']}"
    raise ValueError(f"unknown finding_type: {finding_type!r}")


def filter_dismissed(findings: list[dict], finding_type: str, dismissed_keys: set[str]) -> list[dict]:
    """Used by the PR-scan job to drop already-dismissed findings from a
    diff's "new" list before building the PR comment. Never mutates the
    evidence/diff findings a caller already has a reference to elsewhere -
    returns a new filtered list."""
    return [f for f in findings if finding_identity_key(finding_type, f) not in dismissed_keys]
```

Identity is **always computed server-side** from the finding's own fields —
a dismiss request never accepts a client-supplied identity string directly,
closing off any possibility of dismissing an arbitrary, unrelated key.

## 3. Async DB helpers (`app_server/dismissed_findings.py`, used by dashboard routes)

```python
async def dismiss_finding(
    pool, installation_id: int, repo_full_name: str, finding_type: str,
    finding: dict, dismissed_by: str, reason: str | None = None,
) -> None:
    identity_key = finding_identity_key(finding_type, finding)
    await pool.execute(
        """INSERT INTO dismissed_findings
               (installation_id, repo_full_name, finding_type, identity_key, dismissed_by, reason)
           VALUES ($1, $2, $3, $4, $5, $6)
           ON CONFLICT (installation_id, repo_full_name, finding_type, identity_key) DO NOTHING""",
        installation_id, repo_full_name, finding_type, identity_key, dismissed_by, reason,
    )


async def undismiss_finding(
    pool, installation_id: int, repo_full_name: str, finding_type: str, finding: dict,
) -> None:
    identity_key = finding_identity_key(finding_type, finding)
    await pool.execute(
        """DELETE FROM dismissed_findings
           WHERE installation_id = $1 AND repo_full_name = $2
             AND finding_type = $3 AND identity_key = $4""",
        installation_id, repo_full_name, finding_type, identity_key,
    )


async def get_dismissed_identity_keys(
    pool, installation_id: int, repo_full_name: str,
) -> dict[str, set[str]]:
    rows = await pool.fetch(
        """SELECT finding_type, identity_key FROM dismissed_findings
           WHERE installation_id = $1 AND repo_full_name = $2""",
        installation_id, repo_full_name,
    )
    result: dict[str, set[str]] = {"secret": set(), "vulnerability": set()}
    for row in rows:
        result[row["finding_type"]].add(row["identity_key"])
    return result
```

## 4. Sync DB helper (`scan_worker/db.py`, used by the PR-scan job)

Matches the existing sync `psycopg` style already used there (e.g.
`get_installation`):

```python
def get_dismissed_identity_keys(dsn: str, installation_id: int, repo_full_name: str) -> dict[str, set[str]]:
    import psycopg

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT finding_type, identity_key FROM dismissed_findings
                   WHERE installation_id = %s AND repo_full_name = %s""",
                (installation_id, repo_full_name),
            )
            result: dict[str, set[str]] = {"secret": set(), "vulnerability": set()}
            for finding_type, identity_key in cur.fetchall():
                result[finding_type].add(identity_key)
            return result
```

## 5. Wiring into the PR-scan job (`scan_worker/jobs.py::run_pr_scan_job`)

Right after `diff = compute_diff(old, new, full=False)`:

```python
dismissed = get_dismissed_identity_keys(settings.database_url, installation_id, repo_full_name)
diff["secrets"]["new"] = filter_dismissed(diff["secrets"]["new"], "secret", dismissed["secret"])
diff["vulnerabilities"]["new"] = filter_dismissed(
    diff["vulnerabilities"]["new"], "vulnerability", dismissed["vulnerability"]
)
```

`diff[...]["resolved"]` is left untouched — a dismissed-then-resolved
finding disappearing from "resolved" isn't meaningfully confusing, and
touching it adds no real value.

## 6. Dashboard API (`app_server/dashboard.py`)

`GET /app/{org}/{repo}` gains one field on its existing response:

```python
dismissed = await get_dismissed_identity_keys(pool, installation_id, repo_full_name)
return {
    "repo_full_name": repo_full_name,
    "history": history,
    "dismissed_finding_keys": {"secret": list(dismissed["secret"]), "vulnerability": list(dismissed["vulnerability"])},
}
```

Two new routes, gated the same as the underlying data route the security
page actually fetches from — `_require_dashboard_installation` (session +
membership + **paid-plan check**, `dashboard.py:179`). Correction from an
earlier assumption: the security page's HTML shell route only checks
session, but the JS on that page fetches `GET /app/{org}/{repo}` via
`_require_dashboard_installation`, which 402s on the free plan. Dismiss/
undismiss follow the same gate for consistency — you already need a paid
plan to see dashboard findings at all today.

`_require_dashboard_installation` currently returns only `installation_id`,
not the session, but `dismissed_by` needs `session["github_login"]`.
Refactored to return `tuple[dict, int]` (`session, installation_id`); its 3
existing call sites (`get_dashboard`, `get_dashboard_health`, one more at
`dashboard.py:273`) are updated to unpack both values — a small, mechanical
change, not a behavior change for any of them.

```python
@dashboard_router.post("/app/{org}/{repo}/findings/dismiss")
async def dismiss_finding_route(org: str, repo: str, request: Request):
    session, installation_id = await _require_dashboard_session(request, org, repo)
    body = await request.json()
    finding_type = body.get("finding_type")
    finding = body.get("finding")
    if finding_type not in ("secret", "vulnerability") or not isinstance(finding, dict):
        raise HTTPException(status_code=400, detail="invalid finding_type or finding")
    pool = request.app.state.db_pool
    await dismiss_finding(
        pool, installation_id, f"{org}/{repo}", finding_type, finding,
        session["github_login"], body.get("reason"),
    )
    return {"ok": True}


@dashboard_router.post("/app/{org}/{repo}/findings/undismiss")
async def undismiss_finding_route(org: str, repo: str, request: Request):
    session, installation_id = await _require_dashboard_session(request, org, repo)
    body = await request.json()
    finding_type = body.get("finding_type")
    finding = body.get("finding")
    if finding_type not in ("secret", "vulnerability") or not isinstance(finding, dict):
        raise HTTPException(status_code=400, detail="invalid finding_type or finding")
    pool = request.app.state.db_pool
    await undismiss_finding(pool, installation_id, f"{org}/{repo}", finding_type, finding)
    return {"ok": True}
```

(`_require_dashboard_session` here means whatever the existing session-gate
helper is that also resolves `installation_id` for this org/repo — the
existing `get_dashboard`/`get_dashboard_health` routes already do this via
`_require_dashboard_installation`, which needs to additionally return the
session dict so `dismissed_by` can be set to the real GitHub login rather
than a placeholder.)

## 7. Frontend (`app_server/frontend.py`)

A small JS helper mirroring the Python identity-key format (trivial format,
low duplication risk — unlike the CVSS calculator, not worth avoiding a JS
mirror for):

```js
function findingIdentityKey(findingType, f) {
  if (findingType === 'secret') return f.path + '\x1f' + f.pattern + '\x1f' + f.match_preview;
  return f.ecosystem + '\x1f' + f.package + '\x1f' + f.advisory_id;
}
```

**Security page** (`loadSecurity()`): filter `secretFindings`/`vulnFindings`
by `!dismissedKeys[type].includes(findingIdentityKey(type, f))` before
rendering the open-findings table. Each row gets a "Dismiss" button (POSTs
to the dismiss route, then re-runs `loadSecurity()`). A new
"Show dismissed (N)" toggle reveals the dismissed findings in a second,
visually de-emphasized table with an "Undismiss" button per row — dismissal
must stay visible and reversible, never a silent, permanent hide.

**Overview page** (`loadOverview()`): same filtering applied to
`secretFindings`/`vulnFindings` before computing `totalFindings` and the
"recent findings" preview, so the two pages agree on what counts as open.

## Testing

- `finding_identity_key`: correct format per finding type, raises on an
  unknown finding_type.
- `filter_dismissed`: removes matched findings, leaves non-matched
  findings untouched, no-ops on an empty dismissed set.
- `dismiss_finding`/`undismiss_finding`/`get_dismissed_identity_keys`
  (async, `app_server`): insert then read back; `ON CONFLICT DO NOTHING`
  makes a duplicate dismiss a no-op rather than an error; undismiss then
  re-read confirms removal.
- `get_dismissed_identity_keys` (sync, `scan_worker`): same read-back
  behavior via the sync connection.
- `run_pr_scan_job`: a dismissed secret/vulnerability does not appear in
  the posted PR comment body; a non-dismissed one still does.
- Dashboard routes: dismiss/undismiss require a session (401 without one);
  reject an unknown `finding_type` (400); a full end-to-end test seeds a
  `repo_history` row with a secret finding, dismisses it via the route,
  then confirms `GET /app/{org}/{repo}`'s `dismissed_finding_keys` includes
  it and the security page's rendered output excludes it from the open
  table.
