# Aletheore Skill Hub — Design Spec

## Overview

Developers juggling multiple coding agents (Claude Code, Codex, Cursor, OpenCode) today have to separately research, find, and install the same kind of agent skill (code review, brainstorming, design, debugging, ...) once per agent, with no shared source of truth. The emerging `SKILL.md` open standard (published at `agentskills.io`, Dec 2025) means the *content format* is now genuinely portable — Claude Code, Codex CLI, Cursor (2.4+), OpenCode, Cline, Antigravity, and Gemini CLI all natively parse it — but each agent still only reads its own directory (`.claude/skills/`, `~/.codex/skills/`, `.cursor/skills/`, etc.), so "write the files once" still means fanning them out per agent, per machine.

This spec takes a different approach: **Aletheore itself becomes the single place skills live**, and any agent — regardless of whether it natively understands `SKILL.md` at all — reaches Aletheore to browse a curated catalog and pull just the one skill it needs, on demand. Nothing is written into any agent's own skill directory; Aletheore is the interface, not a file distributor. This sidesteps the per-agent-directory problem entirely and works identically for all four target agents (and any future one) with zero agent-specific code.

Confirmed during design (see Architecture) that this is genuinely new ground: Aletheore's existing adapter layer (`src/aletheore/adapters/`, `KNOWN_ADAPTERS` in `cli.py`) shells out *to* agent CLIs to run scans — a different, unrelated mechanism from this spec, which is about an agent asking Aletheore *for* something. There is no existing skill/`SKILL.md` concept anywhere in the codebase today.

## Goals

- A curated catalog of hand-picked, vetted community skills, organized two levels deep: **category** (Code Review, Debugging, Design, Brainstorming, Product, ...) → **specific skill variant** within it (e.g. under Code Review: security-focused review, performance review, style/lint review).
- Two-phase discovery — list returns compact category/skill metadata only; the full skill body is fetched only once a specific skill is picked — mirroring the same shape Claude Code's own native skill loading already uses, so nothing bloats context unless it's actually needed.
- One underlying implementation exposed two ways: new tools on Aletheore's existing local MCP server (`src/aletheore/mcp_server.py`) for any MCP-connected agent, and an equivalent `aletheore skill-hub` CLI subcommand group for copy-paste use by anything else (or a human). Both call into the same shared module so the two interfaces cannot drift apart in behavior.
- Custom skills: a user can save a personally-authored skill under their own name and use it immediately — **local-first, zero login required**, exactly as frictionless as the bundled curated skills. Logging in adds cross-machine sync on top of that local copy; it never gates the ability to add or use a custom skill in the first place.
- Ships on the free Community tier, unconditionally — this is an explicit product decision ("Aletheore needs to be a great tool, not just a money-making thing"), not a promotional window.
- Browsing/fetching skills — curated or custom — never makes a network call. Login only changes whether a custom skill *also* gets pushed to (and pulled from) the server for cross-machine portability; it is never consulted on the read path. Recommended, never forced, and when used it must succeed for literally anyone with a GitHub account, whether or not they have a paid (or even free) Aletheore installation.
- Every curated skill passes a formalized, automated vetting step (security + license) before entering the catalog — v1 is automated-only; manual/editorial review is a later phase, per explicit instruction to start with automated checks.

## Non-Goals (v1)

- Writing skill content from scratch. V1 curates and adapts the best existing community skills (GitHub, `skills.sh`, `agentskills.io`, etc.) with attribution, not original authorship.
- Automatically tracking and re-syncing curated skills as their upstream source repos change. V1 ships a maintained-by-hand snapshot; a refresh pipeline is future work.
- Writing `SKILL.md` files into any agent-specific directory (`.claude/skills/`, `.cursor/skills/`, `~/.codex/skills/`, etc.). Explicitly rejected during design in favor of the pull-from-Aletheore model above.
- Model-based/semantic vetting of skill instruction text. V1's automated security check is static and pattern-based (reusing Aletheore's own scanner plus an explicit bad-pattern ruleset), not an LLM judge.
- Any change to existing paid-feature entitlement checks. Managed audits, hosted MCP, etc. keep checking `installation.plan` exactly as they do today; nothing in this spec touches that logic.
- Live/continuous sync of custom skills. Sync happens only at two discrete points — `add` (push) and `login` (pull-merge) — never as a background poll or a call made during a plain `list`/`get`.

## Architecture

### Interfaces

- **MCP**: new `_register_skill_hub_tools(mcp_instance, repo_path)` in `src/aletheore/mcp_server.py`, following the exact pattern every existing tool group already uses (`_register_search_tool`, `_register_overview_tool`, etc.). Registers:
  - `aletheore_list_skills(category: str | None = None) -> str` — with no argument, returns every category plus a one-line description; with a category, returns the skill variants inside it. Curated and local custom skills both always appear here, regardless of login — login only affects whether a custom skill *also* syncs to the server, never whether it's visible locally.
  - `aletheore_get_skill(skill_id: str) -> str` — returns the full `SKILL.md` body (instructions plus any bundled reference text) for exactly one skill, curated or custom, by id.
  - `aletheore_add_custom_skill(name: str, content: str) -> str` — saves a user-authored skill locally. Never requires login — works immediately on a fresh install. If a personal token happens to be saved, the same call also pushes the skill to the server in the background for cross-machine sync (see "Custom-skill sync" below); the caller sees one consistent success result either way.
- **CLI**: new `skill_hub_app = typer.Typer()` sub-app in `cli.py`, mounted via `app.add_typer(skill_hub_app, name="skill-hub")` — the exact same pattern `config_app` already uses (`cli.py:2177-2178`). Subcommands: `aletheore skill-hub list [--category X]`, `aletheore skill-hub get <skill-id>`, `aletheore skill-hub add <name> <file>`.
- Both interfaces are thin wrappers around one new internal module, `src/aletheore/skill_hub.py`, which holds the actual list/get/add logic. Neither interface reimplements anything.

### Catalog storage

- Curated skills ship **bundled inside the `aletheore` package** (e.g. `src/aletheore/skills/<category>/<skill-id>/SKILL.md`, plus any `scripts/`/`references/`), so listing and fetching curated skills needs no network call and works immediately offline after `pip install` — preserving the original "install once, zero extra setup" pitch for the free, no-login path.
- Each bundled skill's frontmatter carries one Aletheore-specific field recording its source (origin URL + license), so attribution travels with the content itself rather than living in a side table that can drift out of sync.
- **Custom skills are local-first.** Each one is a single file on disk, under the user's existing local Aletheore config directory — `<config_dir>/custom_skills/<name>.md`, using the same home/XDG resolution `credentials.py`'s `DEFAULT_CREDENTIALS_PATH` already uses (`src/aletheore/user_paths.py`'s `user_home()`), so this needs no new path convention. `aletheore_get_skill`/`skill-hub get` and `list` read the bundled catalog and this local directory directly — **never the network** — so normal use has the same zero-latency, zero-login, offline-safe behavior for custom skills as for curated ones. The server only ever enters the picture at two discrete moments (`add`, and `login`) — see "Custom-skill sync" below — never on a plain read.
- **v1 scope note**: a custom skill is a single text blob (the `SKILL.md` body itself) — no bundled `scripts/`/`references/` the way a curated skill can have. `aletheore_get_skill`'s result always includes bundled reference text for a curated skill and never for a custom one; this is a real, visible difference between the two, not full parity, and is called out explicitly so it isn't read as a bug later.

### Taxonomy

Two levels: `category` (`code-review`, `debugging`, `design`, `brainstorming`, `product`, ...) → `skill_id` within it (`code-review/security-focused`, `code-review/performance`, `code-review/style-lint`, ...). The exact initial category and skill list is an editorial decision for whoever curates the first batch — not fixed by this spec.

**Custom-skill namespacing**: every custom skill's id is the user-chosen name prefixed with a reserved `custom/` segment (e.g. a skill saved as `my-review` is addressed as `custom/my-review`). This makes a collision with any curated `skill_id` structurally impossible — no curated category is ever named `custom` — rather than leaving it to silently shadow or be shadowed by a curated skill of the same name. The user still picks whatever plain name they want; the prefix is purely how `get` disambiguates internally.

## Vetting Pipeline (v1 — automated, gates catalog entry, not request time)

Every candidate skill pulled from an external source runs two automated checks before being committed into the bundled catalog:

1. **Security scan** — any bundled `scripts/`/executable content in the skill folder is scanned with Aletheore's own existing engine (the same secrets/vulnerability/static-analysis tooling it already runs against a user's repo). A separate, explicit pattern ruleset additionally scans the `SKILL.md` instruction text itself for known-bad shapes: data-exfiltration phrasing, unsafe-execution instructions (e.g. `curl | bash`-style), credential-harvesting asks. This is a small, explicit, growable ruleset for v1 — not a model-based judge (see Non-Goals).
2. **License check** — the source repo's license is resolved and checked against an allowlist (MIT, Apache-2.0, BSD-family, etc.). Anything outside the allowlist, or with no discoverable license, is rejected rather than guessed at.

Either check failing blocks the skill from entering the catalog — no soft-pass, no "add with a warning." This runs as part of the curation workflow (a check against the PR/commit that adds a new bundled skill), not at install- or request-time, since the catalog is a static, versioned part of the package.

**Enforcement mechanism**: a new CI job (GitHub Actions workflow, triggered on any PR touching `src/aletheore/skills/**`) runs the vetting checks as a pytest suite against every skill directory in the diff and fails the check run — blocking merge — on any failure. This is a required status check on that path, the same way other required checks already gate merges in this repo; it is not a pre-commit hook (those are opt-in locally and easy to skip) and not a purely-manual step a curator remembers to run.

## Data Model Changes

New tables (exact migration number left to whoever implements, taking the next free one in `github-app/migrations/`):

```sql
CREATE TABLE IF NOT EXISTS personal_identities (
    id                  BIGSERIAL PRIMARY KEY,
    github_user_id      BIGINT NOT NULL UNIQUE,
    github_login        TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS personal_api_tokens (
    id                      BIGSERIAL PRIMARY KEY,
    personal_identity_id    BIGINT NOT NULL REFERENCES personal_identities(id) ON DELETE CASCADE,
    token_hash              TEXT NOT NULL UNIQUE,
    label                   TEXT NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at            TIMESTAMPTZ,
    revoked_at              TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS custom_skills (
    id                      BIGSERIAL PRIMARY KEY,
    personal_identity_id    BIGINT NOT NULL REFERENCES personal_identities(id) ON DELETE CASCADE,
    skill_name              TEXT NOT NULL,
    content                 TEXT NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (personal_identity_id, skill_name)
);
```

This is deliberately separate from `installations`/`api_tokens` (unchanged, still used only for installation-scoped/paid entitlement — confirmed `api_tokens.installation_id` is `NOT NULL`, so a token with no installation literally cannot be represented there). `personal_identities` is keyed purely on GitHub account identity, with zero dependency on any Aletheore GitHub App installation existing — mirroring the shape the existing `sessions` table already uses (`github_user_id`, `github_login`, no `installation_id`), which proves this identity-without-installation pattern is already established elsewhere in this codebase, not something invented new for this feature.

## Auth Flow Changes

- New server endpoint `POST /v1/personal-tokens`: takes the GitHub OAuth token from the device-flow exchange (`Authorization: Bearer <github_token>`), upserts a `personal_identities` row from the already-resolved GitHub user info, mints a token, stores its hash in `personal_api_tokens`, returns it. No installation lookup; cannot fail for lack of one.
- `device_auth.py` gains `mint_personal_token(github_token, label)` calling that endpoint, alongside the existing `mint_cli_token`.
- `cli.py`'s `login()` changes: after `poll_for_access_token` succeeds, it now **always** calls `mint_personal_token` and saves the result under a new credentials slot (e.g. `"aletheore-personal"`) — this step cannot fail for lack of an installation. It *additionally* still attempts `resolve_installation`/`mint_cli_token` exactly as today, for installation-scoped features — but a failure there (zero installations found) becomes a warning ("No Aletheore GitHub App installation found — install it for the full feature set; you're still logged in for Skill Hub.") instead of today's fatal `typer.Exit`.
- `skill_hub.add_custom_skill()` **always writes the local file first, unconditionally** — no login, no auth check, no network call on the critical path. It never returns a "please log in" error, because it never needs to. Browsing/fetching skills never touches auth at all either — zero-login stays zero-login for all of Skill Hub's actual read/write behavior.
- Noted but not required by this spec: `/v1/my-installations` currently filters to paid installations only (`device_auth.py:180-184` raises if empty); relaxing it to also list free-plan installations would let a free-tier GitHub App install surface through `login()` too. This is a small, separately-shippable fix mentioned here because `login()` touches the same code path, but Skill Hub never depends on it.

### Custom-skill sync (server only ever touched at `add` and `login`, never at `get`/`list`)

The server's job is purely to let a custom skill follow the user to a second machine — it is never the source of truth read at use time, and a logged-out user never talks to it at all.

- **Push, on `add`**: after writing the local file, `add_custom_skill()` checks for a saved `"aletheore-personal"` token. If present, it also does a best-effort `PUT` to the server (see API below) with the new content. If that network call fails (offline, server down, whatever), the local add has already succeeded and is reported as successful regardless — a log-level warning only, never a failure the user has to handle. If no personal token is saved, this step is simply skipped; nothing about `add` behaves differently either way from the caller's point of view beyond that silent push.
- **Pull, on `login`**: once `login()` has a personal token (see Auth Flow Changes), it fetches the full list of the account's server-side custom skills and merges them into the local `custom_skills` directory: for each one, compare the server's `updated_at` against the local file's mtime (if the local file exists at all) — server newer or local missing → write/overwrite the local file; local newer → push the local version up instead. This is a simple last-write-wins merge, not a full conflict-resolution system; clock skew between machines is an accepted v1 limitation, not something this spec engineers around. This is the mechanism behind "log in on a different machine and the skill you added elsewhere is just there" — it happens once, at the login moment, not as an ongoing background sync.
- No other point reaches the network. `list`/`get` are 100% local, always — this is deliberate, not an optimization to revisit later: the server is a relay between machines a user owns, not a cache the client is allowed to depend on for normal operation.

### Custom-skill sync API

Three endpoints, all authenticated via `Authorization: Bearer <personal-token>` resolved to a `personal_identity_id` exactly the way `managed_audit_api.py` already resolves an installation from its bearer token. These are called only by the two sync points above — never by a plain `list`/`get`:

- `GET /v1/custom-skills` — list the caller's custom skills: `{"skills": [{"name": "...", "updated_at": "..."}]}` (names + timestamps only, no content — enough for the login-time merge to decide what needs pulling without downloading everything up front).
- `GET /v1/custom-skills/<name>` — `{"name": "...", "content": "...", "updated_at": "..."}`, or `404` if the caller has no skill by that name. Called during the login-time merge only for names that need pulling.
- `PUT /v1/custom-skills/<name>` — body `{"content": "...", "updated_at": "..."}`. **Upsert**, not create-or-reject: pushing the same name again silently replaces the content and bumps `updated_at`, consistent with the table's `UNIQUE (personal_identity_id, skill_name)` constraint. Called from the `add`-time push and from the login-time merge (when the local copy turns out to be newer).

## CLI Changes

- `skill_hub_app` sub-app as described above, with `list`, `get`, `add` subcommands.
- `login()` changes described above (always mints a personal token; installation resolution becomes best-effort).
- `status()` gains a line distinguishing "Skill Hub: logged in as `<github_login>`" from the existing managed-audit/installation line — these are now two independently-true-or-false facts about one account.

## Testing Strategy

- Catalog tests: `list` returns compact metadata only (no full skill body leaks into a list response); category filter behaves correctly; a skill id that doesn't exist in either the bundled catalog or custom storage returns a clear not-found error.
- Vetting pipeline tests: a candidate skill with a bundled script that trips the existing secrets/vulnerability scanner is rejected; one with a non-allowlisted (or missing) license is rejected; a clean one passes.
- Auth tests: `login` succeeds end-to-end for a GitHub account with zero Aletheore installations (personal token minted, installation-scoped token simply absent, no fatal error); succeeds fully for an account with exactly one installation.
- Local-first custom-skill tests: `add` succeeds and the skill is immediately readable via `get` with **zero network calls** and **no personal token saved at all** — asserted by running these against an HTTP client that raises if it's ever invoked, not just by not mocking a server. `add`ing the same name twice overwrites the local file (second `get` returns the new content). A custom skill named the same as a curated `skill_id`'s trailing segment (e.g. custom `security-focused` vs. curated `code-review/security-focused`) resolves to the `custom/`-prefixed one with zero ambiguity.
- Sync tests: with a personal token saved, `add` triggers exactly one `PUT` to the sync API with the right content, and a simulated network failure on that `PUT` still leaves `add` reporting success and the local file written. `login`'s pull-merge: server has a skill the local directory doesn't → written locally after login; local has a skill with a newer mtime than the server's `updated_at` → pushed up instead of overwritten; `GET /v1/custom-skills/<name>` for a name the caller never added returns `404`, never another caller's skill.
- Cross-machine integration test: add a custom skill via the CLI in one test process with a personal token saved (confirms the push lands server-side), then run `login` against a **fresh, empty local config directory** in a separate process and assert the skill shows up locally afterward with identical content, readable with no further network calls — this is the test that actually proves the "log in elsewhere and it's just there" claim, end to end.

## Rollout / Tier Gating

Ships on Community tier, free, unconditionally — no plan check anywhere in this feature's code path. Deliberate product decision, not temporary.

## Open Risks / Explicit Builder Decisions (not blocking, carried forward)

- Exact initial category/skill list and how many ship at launch — editorial decision for whoever curates the first batch.
- The starting pattern ruleset for the "bad instruction phrasing" scan — expected to grow over time as new bad patterns surface, same as any heuristic scanner; starting set left to whoever implements the vetting pipeline.
- Migration file number — take the next free one at implementation time.
- Relaxing `/v1/my-installations` to include free-plan installations (noted above) is optional polish, not required for this spec's core claim.
