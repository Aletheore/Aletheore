<p align="center">
  <img src="assets/logo.png" alt="Aletheore" width="360">
</p>

<h3 align="center">Evidence-grounded repository intelligence</h3>

<p align="center">
  A deterministic scanner reads your repo and writes structured evidence — every downstream
  feature (AI audit, PR bot, MCP server, dashboard) has to cite that evidence, or it doesn't
  get to make the claim.
</p>

<p align="center">
  <a href="https://pypi.org/project/aletheore/"><img src="https://img.shields.io/pypi/v/aletheore?color=blue" alt="PyPI version"></a>
  <a href="https://pypi.org/project/aletheore/"><img src="https://img.shields.io/pypi/pyversions/aletheore" alt="Python versions"></a>
  <a href="https://github.com/Aletheore/Aletheore/blob/master/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue" alt="License"></a>
  <a href="https://github.com/Aletheore/Aletheore/actions/workflows/tests.yml"><img src="https://github.com/Aletheore/Aletheore/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
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

## What's actually shipped

- **`aletheore scan`** — the deterministic scanner above. Safe to run in CI, on every commit.
- **`aletheore audit`** — scans, then has a coding-agent CLI or API provider write a full
  grounded markdown report, citing exact evidence fields throughout. Meant to be run by hand
  against your own repo — see [`src/README.md`](src/README.md) for why it isn't wired into CI.
- **`aletheore query`** / **`aletheore diff`** — answer a targeted question or compare two
  scans from existing evidence, no re-scan or LLM call needed.
- **`aletheore docs`** — a grounded markdown API reference generated straight from evidence:
  signatures, docstrings/doc-comments, and file:line citations for public functions and
  classes, across all 10 supported languages. No LLM call; an undocumented symbol is labeled
  as such, never given an invented description.
- **`aletheore mcp`** — a stdio MCP server exposing 29 tools (module/symbol/dependency lookups,
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
- uses: Aletheore/Aletheore@v0.7.1
  with:
    fail-on-new-secrets: true
```

Full command reference, MCP tool list, per-language import-resolution details, and
configuration options: **[`src/README.md`](src/README.md)**.

## Repository layout

- `src/` — the actual, working CLI code (see its README for everything above in detail).
- `github-app/` — the hosted GitHub App: FastAPI server, RQ workers, migrations.
- `website/` — the marketing site and live demo.
- `docs/superpowers/` — design specs and implementation plans written during development.
- `docs/operations/` — current operational baselines: incident response, data handling, SLOs,
  deployment verification, branch protection, support process.
- `SECURITY.md` — vulnerability reporting and response targets.

Aletheore is free and open source. If it's useful to you, consider
[sponsoring development](https://github.com/sponsors/ArihantK15) — no accounts, no tracking,
nothing leaves your machine when you run it.
