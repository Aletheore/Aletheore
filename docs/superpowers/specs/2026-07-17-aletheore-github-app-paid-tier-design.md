# Aletheore GitHub App — Paid Tier: Managed Audit Runs & Team/Risk Features

**Status:** Draft, pending review
**Date:** 2026-07-17

## Problem

The GitHub App foundation (`2026-07-17-aletheore-github-app-foundation-design.md`, live on
`aletheore.com`) deliberately shipped nothing paid — free PR comments, free dashboard,
`installations.plan` tracked but never checked. Two paid capabilities were chosen afterward for
having a real "this costs us something to provide" story: **managed audit runs** (real LLM
inference cost per use) and **team/risk features** (Slack/Teams alerts, branch-protection
policy — genuine toil/risk reduction, not prettier visualization). This spec designs both,
combined, since they share one new foundation piece neither could ship without: a way for a
paying installation's admin to actually *manage* anything (issue API tokens, configure a Slack
webhook) — which means login.

## Goals

- **GitHub OAuth login** on `aletheore.com`. Anyone who installed the App can log in; what they
  see depends on their installation's live `plan` (same "check the field live" principle already
  used for `marketplace_purchase` cancellation).
- **Personal API tokens**, generated from that login page, capped per installation (an
  adjustable count, not a hardcoded number — the actual seat limits are a pricing decision for
  later), used to authenticate CLI/MCP-triggered managed audits.
- **Managed audit runs**: the full agentic `audit` report, run using Aletheore's own shared LLM
  key instead of BYOK, on-demand only (never automatic-per-push — real inference cost per run
  means unbounded automatic triggering is a real cost risk, not a hypothetical one). Two
  triggers, one shared execution engine:
  - A PR comment command (`/aletheore audit`) — server clones the PR (reusing `scan_worker`'s
    existing clone step), runs the audit, replies with the report.
  - `aletheore audit --managed` from the CLI (and the equivalent MCP tool) — the caller already
    has local evidence, so it's sent directly to a new endpoint; no server-side cloning needed
    for this path at all.
- **Team/risk features**, configured from the same login page:
  - Slack/Teams webhook alerts, firing only when a scan's diff contains a genuinely new finding
    (secret, vulnerability, or layer violation) — reusing `compute_diff`'s existing `new` lists
    unchanged, not a new detection mechanism.
  - Branch-protection policy: the App creates a GitHub Check Run on the PR's head commit,
    failing it when a new secret is found. Scoped to secrets only (the unambiguous, universally
    urgent case) — not vulnerabilities or layer violations, which are more often debatable/lower
    urgency and better left as PR-comment information rather than a merge blocker.

## Non-Goals

- **No uptime/health-check monitoring in this spec.** A real, separately valuable idea (raised
  during this design session): periodically ping a repo's mapped API endpoints — reusing
  `aletheore healthcheck`'s existing deterministic logic — and alert on Slack/Teams if something
  goes down. Deliberately parked as its own future spec: everything else here is event-driven
  (a GitHub webhook fires, a job runs); this would be the first *time-driven* capability
  (a scheduler polling on an interval, remembering what "normal" looked like last time) and
  doesn't share that infrastructure with anything in this spec. Not forgotten, just sequenced
  after this ships.
- **No branch-protection blocking for vulnerabilities or layer violations.** Secrets only, per
  above — scope can widen later if real usage shows demand, but starting narrow matches the
  project's standing discipline of not overselling a heuristic's reliability.
- **The App cannot unilaterally force a merge block.** It can only report a Check Run result;
  actually requiring that check to pass before merge is a GitHub branch-protection setting only
  the repo's own admin can configure (GitHub's permission model, not something an App can
  override). The dashboard/docs must say this plainly — implying otherwise would be exactly the
  kind of overclaim this project's evidence-grounded ethos exists to avoid.
- **No encryption-at-rest design for stored OAuth tokens in this spec** — flagged as a real
  requirement (a session's GitHub access token is a real credential and must not sit in Postgres
  in plaintext), but the exact mechanism (application-level encryption vs. relying on the
  server's disk encryption) is left to the implementation plan rather than decided here.

## Architecture

### 1. GitHub OAuth login

The App already exists; OAuth login is enabled by adding a Callback URL in its settings
(`https://aletheore.com/auth/callback`) — editable after the fact like every other App setting,
confirmed earlier this session. Flow:

1. `GET /auth/login` redirects to GitHub's OAuth authorize URL using the App's Client ID
   (visible on its settings page).
2. GitHub redirects back to `/auth/callback` with a `code`; the server exchanges it for a user
   access token (standard OAuth code exchange, `httpx` POST to GitHub's token endpoint — same
   HTTP client already used throughout `scan_worker`/`app_server`).
3. The server calls `GET /user/installations` **using that user's token** — GitHub's own API
   returns exactly the installations this person can administer. This is the entire
   authorization check: no separate roles/permissions table, GitHub's own model is the source of
   truth for "who can manage this installation," exactly the same reasoning that made
   `marketplace_purchase`'s `account.id` the right billing anchor in the foundation spec.
4. A `sessions` row is created (session id, GitHub user id/login, the access token) and a signed
   session-id cookie is set. The dashboard's existing routes gain a login-aware layer: logged-in
   + administers installation X + `installations.plan(X) != 'free'` unlocks token management and
   team/risk settings for X.

### 2. Personal API tokens

```sql
ALTER TABLE installations ADD COLUMN max_api_tokens INT NOT NULL DEFAULT 3;

CREATE TABLE api_tokens (
    id                    BIGSERIAL PRIMARY KEY,
    installation_id       BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    token_hash            TEXT NOT NULL UNIQUE,
    label                 TEXT NOT NULL,
    created_by_github_login TEXT NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at          TIMESTAMPTZ,
    revoked_at            TIMESTAMPTZ
);
```

Generation (from the logged-in dashboard, for an installation the session administers): server
generates a random 32-byte token, shows the raw value **once** in the response (standard
practice — GitHub's own PATs work the same way), stores only its SHA-256 hash. Rejected with a
clear error if the installation already has `max_api_tokens` non-revoked tokens. Revocation sets
`revoked_at`; revoked tokens fail auth immediately (checked on every use, not cached).

### 3. Managed audit runs — shared engine

**The key insight, verified against real code, not assumed**: `run_reasoning_phase(adapter,
repo_path, manual_dir)` (`aletheore/report.py`) only ever reads `.aletheore/evidence.toon` and
the manual's markdown files from `repo_path`, and writes `.aletheore/audit-report.md` there — it
never touches the rest of the repo's source. The adapter itself (`AnthropicAdapter`, chosen here
specifically because it's the one adapter that's a real API client, not a CLI wrapper — confirmed
by reading its `invoke()`: a genuine Anthropic API tool-calling loop, no terminal/interactive CLI
needed) only ever calls `read_evidence_section`/`write_report_section` — it never reads raw
source files. That means **both trigger paths can call the exact same function unchanged**, they
just differ in how `repo_path` gets populated:

- **PR-comment path**: `scan_worker` already clones the PR into a job-scoped temp dir for the
  free scan. The managed-audit job reuses that same directory — it's a real checkout, evidence
  is already written there by the existing `aletheore scan` step.
- **CLI/MCP path**: no checkout exists or is needed. A new endpoint receives TOON-encoded
  evidence (matching the project's existing token-efficient encoding, used everywhere else) in
  the request body, writes it to a fresh job-scoped temp dir as `.aletheore/evidence.toon`, then
  calls `run_reasoning_phase` against that scratch directory exactly as the PR path does.

**The shared key**: `AnthropicAdapter.is_available()`/`get_api_key()` (`aletheore/credentials.py`,
confirmed by reading it) check the `ANTHROPIC_API_KEY` environment variable *before* any local
credentials file. Setting that one env var in `scan-worker`'s container **is** the entire "shared
key" mechanism — zero changes needed to the adapter or credentials module.

**PR-comment trigger**: subscribing to the `issue_comment` webhook event (PR comments fire this
event, with `issue.pull_request` present to distinguish them from plain issue comments — a new
event subscription the App needs, added the same way `pull_request` was added originally).
`app_server` checks the comment body for `/aletheore audit`, verifies the repo's installation has
`plan != 'free'`, and enqueues a managed-audit job (same RQ queue, same worker pool as the free
scan) rather than reusing the free scan's job for a different purpose.

**CLI/MCP trigger**: `POST /v1/managed-audit`, `Authorization: Bearer <token>`, body =
TOON-encoded evidence. Server hashes the token, looks it up in `api_tokens` (not revoked),
resolves `installation_id`, checks `installations.plan != 'free'` live, updates
`last_used_at`, enqueues the same job type as the PR-comment path (parameterized by
installation id + raw evidence instead of installation id + PR coordinates). `aletheore audit
--managed` (CLI) and the equivalent MCP tool both just POST to this endpoint and poll (or wait
synchronously with a generous timeout, matching how `aletheore audit` already blocks locally
while the adapter runs) for the report.

### 4. Team/risk features

**Slack/Teams alerts**: a `webhook_url` column on `installations` (nullable — unset means no
alerting configured). After every PR scan (the existing free-tier job, not a new one), if
`compute_diff`'s `new` lists (secrets, vulnerabilities, layer violations) are non-empty **and**
the installation has a paid plan **and** `webhook_url` is set, POST a formatted summary to that
URL. This rides the exact same trigger the PR comment already uses — not a new detection
mechanism, just an additional delivery channel for the same already-computed "something new was
found" fact.

**Branch-protection Check Run**: requires the App to add the `checks: write` permission (another
addition existing installers get prompted to approve, per GitHub's standard permission-change
flow, confirmed earlier this session). After the free scan job, if the installation is paid and
`compute_diff`'s `secrets.new` (real, non-placeholder, non-baseline-accepted — same filter
`--fail-on-new-secrets` already applies) is non-empty, create/update a Check Run on the head SHA
with `conclusion: failure` and a summary listing the finding(s); otherwise `conclusion: success`.
The dashboard's team/risk settings page must state plainly that the customer still needs to mark
this check as *required* in their own repo's branch protection settings for it to actually block
a merge — the App reports the result, GitHub's own settings enforce it.

## Storage (full picture, additive to the foundation's schema)

```sql
ALTER TABLE installations ADD COLUMN max_api_tokens INT NOT NULL DEFAULT 3;
ALTER TABLE installations ADD COLUMN webhook_url TEXT;

CREATE TABLE sessions (
    id                  TEXT PRIMARY KEY,
    github_user_id      BIGINT NOT NULL,
    github_login        TEXT NOT NULL,
    github_access_token TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at          TIMESTAMPTZ NOT NULL
);

CREATE TABLE api_tokens (
    id                      BIGSERIAL PRIMARY KEY,
    installation_id         BIGINT NOT NULL REFERENCES installations(installation_id) ON DELETE CASCADE,
    token_hash              TEXT NOT NULL UNIQUE,
    label                   TEXT NOT NULL,
    created_by_github_login TEXT NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at            TIMESTAMPTZ,
    revoked_at              TIMESTAMPTZ
);
```

## Testing

- **OAuth flow**: code exchange, `/user/installations` call, and session creation each tested
  against mocked GitHub responses (same `httpx.MockTransport` pattern already used for
  `get_installation_token` in the foundation's test suite).
- **Token generation/cap enforcement**: generating up to `max_api_tokens` succeeds, the next one
  is rejected with a clear error; a revoked token fails auth immediately.
- **Shared-engine equivalence**: a test proving the PR-comment path and the CLI/MCP path produce
  the same report given the same evidence — i.e., that `run_reasoning_phase` is genuinely called
  identically by both, not two diverging implementations that happen to look similar today.
- **Managed-audit entitlement**: free-plan installations are rejected on both trigger paths (real
  negative test, not just "paid works").
- **Slack alert trigger**: fires only when `new` lists are non-empty on a paid install with
  `webhook_url` set; does not fire on a clean scan, a free install, or an unset webhook URL
  (three separate negative cases, not one combined assumption).
- **Check Run conclusion**: `failure` on a real new secret, `success` on a clean scan, and
  confirmed it does not fire for new vulnerabilities/layer violations alone (proving the
  secrets-only scoping is actually enforced, not just documented).

## Success Criteria

1. A real GitHub login on `aletheore.com` shows only the installations the logged-in user
   actually administers (verified via a real personal GitHub account, not a mocked session).
2. A generated API token successfully authenticates `aletheore audit --managed` against a real
   local repo end-to-end, producing the same shape of report `aletheore audit` produces locally
   with BYOK.
3. Commenting `/aletheore audit` on a real PR in an installed, paid repo produces a reply with a
   real audit report.
4. A real Slack (or Teams) webhook receives a message when a real new secret is introduced in a
   test PR on a paid install, and does not fire on a clean PR.
5. A real Check Run appears on a test PR's head commit, `failure` when a real new secret is
   introduced, `success` otherwise - confirmed by actually enabling it as a required check on a
   throwaway repo's branch protection and observing GitHub block the merge button.
6. Zero regressions to the free tier - existing foundation tests plus a live re-verification
   (real scan, real webhook, real 0-restart deploy check) all still pass unchanged.
