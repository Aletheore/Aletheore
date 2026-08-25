<p align="center">
  <img src="assets/logo.png" alt="Aletheore" width="360">
</p>

<h3 align="center">Evidence-grounded repository intelligence</h3>

<p align="center">
  Code intelligence that has to show its work. A deterministic scanner reads your repo and
  writes structured evidence — every downstream feature (AI audit, PR bot, MCP server,
  dashboard) has to cite that evidence, or it doesn't get to make the claim.
</p>

<p align="center">
  <a href="https://pypi.org/project/aletheore/"><img src="https://img.shields.io/pypi/v/aletheore?color=blue" alt="PyPI version"></a>
  <a href="https://pypi.org/project/aletheore/"><img src="https://img.shields.io/pypi/pyversions/aletheore" alt="Python versions"></a>
  <a href="https://github.com/Aletheore/Aletheore/blob/master/LICENSE"><img src="https://img.shields.io/badge/license-PolyForm--Noncommercial--1.0.0-blue" alt="License"></a>
  <a href="https://github.com/Aletheore/Aletheore/actions/workflows/tests.yml"><img src="https://github.com/Aletheore/Aletheore/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
  <a href="https://codecov.io/gh/Aletheore/Aletheore"><img src="https://codecov.io/gh/Aletheore/Aletheore/graph/badge.svg" alt="Coverage"></a>
  <a href="https://github.com/Aletheore/Aletheore/actions/workflows/container-security.yml"><img src="https://github.com/Aletheore/Aletheore/actions/workflows/container-security.yml/badge.svg" alt="Container Security"></a>
  <a href="https://securityscorecards.dev/viewer/?uri=github.com/Aletheore/Aletheore"><img src="https://api.securityscorecards.dev/projects/github.com/Aletheore/Aletheore/badge" alt="OpenSSF Scorecard"></a>
  <a href="https://pepy.tech/projects/aletheore"><img src="https://static.pepy.tech/personalized-badge/aletheore?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=GREEN&left_text=downloads" alt="PyPI Downloads"></a>
</p>

```bash
$ pipx install aletheore
$ aletheore scan .

Scanning /path/to/your/repo...
  → Detecting languages, frameworks, and build tools
  → Building module dependency graph (parsing source with tree-sitter)
  → Analyzing git history and ownership
  → Scanning working tree for secrets
  → Checking dependencies for known vulnerabilities (OSV.dev)
  → Mapping API endpoints
  → Done
✓ Scan complete
  Evidence written to /path/to/your/repo/.aletheore/air.json
```

No LLM call, no account, no network access beyond the vulnerability/license registry lookups
(turn those off too for a fully offline run). That one command gets you a real dependency
graph, secrets scan, git-history secret sweep, dependency-vulnerability/license check, and
static API endpoint map — for **Python, JavaScript/JSX, TypeScript/TSX, Go, Rust, Java, Ruby,
PHP, C, C++, and C#**.

## Why

- **Grounded, not vibes.** Every AI-written claim (the `audit` report, PR review comments, the
  architecture wiki) is checked against the file:line it cites. A finding that can't be
  verified against real evidence gets dropped or flagged, not shipped silently.
- **The free tier is actually free.** `scan`, `query`, `diff`, the MCP server, and the local
  dashboard need no account and no API key. Nothing leaves your machine.
- **Bring your own model, or don't use one at all.** `audit` works with six provider families
  (Claude, OpenAI, Google, Mistral, xAI, or a local Ollama model) — your key, your cost, your
  choice — or skip the LLM step entirely and just use the deterministic evidence.
- **1,600+ tests**, real CI, and a GitHub Action that dogfoods itself on every PR to this repo.
- **Benchmarked, not just claimed.** [aletheore-benchmarks](https://github.com/Aletheore/aletheore-benchmarks)
  is the public harness we test review quality against — real open-source PRs, blind LLM
  judging, and a published methodology, comparing Aletheore's evidence-grounded context
  against raw-diff and full-file-context baselines.

## What's actually shipped

- **`aletheore scan`** — the deterministic scanner above. Safe to run in CI, on every commit.
- **`aletheore audit`** — scans, then has a coding-agent CLI or API provider write a full
  grounded markdown report, citing exact evidence fields throughout. Meant to be run by hand
  against your own repo — see [`src/README.md`](src/README.md) for why it isn't wired into CI.
- **`aletheore query`** / **`aletheore diff`** — answer a targeted question or compare two
  scans from existing evidence, no re-scan or LLM call needed.
- **`aletheore mcp`** — a stdio MCP server exposing 30 tools by default (31 with
  `ALETHEORE_MCP_ALLOW=external` enabled) (module/symbol/dependency lookups,
  ownership, clusters, dead code, hotspots, full-text and semantic search, scan and index
  triggers) so a coding agent can query your repo's structure directly instead of shelling out
  or re-reading files on every lookup. `aletheore mcp-install` wires it into Claude Code,
  Cursor, VS Code, Kiro, Opencode, or Codex CLI automatically.
- **`aletheore dashboard`** — a live local web UI: dependency graph, an Obsidian-style cluster
  graph, trend charts across scan history, and the MCP tool list.
- **A GitHub Action** (`action.yml`, on the Marketplace as "Aletheore") — scans a PR's base and
  head refs and posts a diff: new/resolved secrets, dependency vulnerabilities, and
  layer-convention violations, as a PR comment, inline annotations, and the run's Step Summary.
  CI only ever runs `scan` + `diff` — fast and deterministic, never the full agent-driven
  `audit`.

```yaml
- uses: Aletheore/Aletheore@v0.7.2
  with:
    fail-on-new-secrets: true
```

Full command reference, MCP tool list, per-language import-resolution details, and
configuration options: **[`src/README.md`](src/README.md)**.

## Aletheore AIR (hosted GitHub App)

Everything above is the free, local-first CLI (Aletheore Community). Installing the
[Aletheore GitHub App](https://aletheore.com) adds a hosted layer on top of the same
evidence — paid plans start at $29.99/mo for up to 5 team members:

- **Automated PR review** — Flash reviews and managed audits comment directly on pull
  requests, scoped to the changed hunks, citing file:line evidence. Blast-radius checks
  trace a changed symbol to its real callers across the repo (or say plainly when no
  caller could be confirmed, instead of guessing).
- **AIRview** — an AI-generated, always-current architecture map of the repo, rebuilt
  from the same dependency-graph evidence `scan` produces.
- **AI-generated Docs** — per-symbol descriptions written straight from real source,
  drafted as PRs land and backfilled for a repo's existing public API, always marked
  as AI-generated rather than presented as hand-written.
- **Production monitoring** — live endpoint reachability/latency checks mapped back to
  the source handler that owns the route, with Slack/Teams alerts on state changes.
- **Branch-protection checks** and team seat management.

The GitHub App and dashboard code lives in `github-app/`; see its own
[README](github-app/README.md) for deployment and operations details.

## Repository layout

- `src/` — the actual, working CLI code (see its README for everything above in detail).
- `github-app/` — the hosted GitHub App: FastAPI server, RQ workers, migrations. See
  [Aletheore AIR](#aletheore-air-hosted-github-app) above for what it does.
- `website/` — the marketing site and live demo.
- `docs/superpowers/` — design specs and implementation plans written during development.
- `docs/operations/` — current operational baselines: incident response, data handling, SLOs,
  deployment verification, branch protection, support process.
- `SECURITY.md` — vulnerability reporting and response targets.

Related, separate repo: [aletheore-benchmarks](https://github.com/Aletheore/aletheore-benchmarks) —
the public PR-review benchmark harness and published results.

## Licensing

Aletheore is licensed under the
[PolyForm Noncommercial License 1.0.0](LICENSE), not an OSI-approved open-source license.
It's free for individuals: personal use, research, hobby projects, and evaluation. Any use
for or within a company or other organization — including internal tooling at a company you
work for — is a commercial use and requires a separate commercial license. Reach out at
[arihantkaul@outlook.com](mailto:arihantkaul@outlook.com) for commercial licensing, or see
[Aletheore AIR](https://aletheore.com) for the hosted, paid tier.

If it's useful to you personally, consider
[sponsoring development](https://github.com/sponsors/ArihantK15) — no accounts, no tracking,
nothing leaves your machine when you run it.
