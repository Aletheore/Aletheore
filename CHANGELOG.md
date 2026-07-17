# Changelog

Notable changes to Aletheore, by release. The working code lives in `prototype/` — see
[`prototype/README.md`](prototype/README.md) for the full command reference.

## Unreleased

- **Fixed `aletheore audit` hanging or running away when an API-based provider's model stopped
  calling tools mid-report.** The tool-calling loop used to silently retry (up to all 20
  rounds) whenever a model responded with plain text instead of a tool call - live-verified
  against a real local Ollama run that burned 250s+ across 4 rounds without writing a single
  section. Now caps consecutive no-tool-call rounds at 2, with a corrective nudge on the first
  miss and a fast, clear failure on the second. Also forces `tool_choice` (`"required"` for
  OpenAI-compatible providers, `{"type": "any"}` for the native Anthropic adapter) on providers
  that support it, preventing the no-tool-call response from happening at all rather than just
  reacting to it - made opt-in per-adapter after live-verifying that Ollama's own `/v1`
  OpenAI-compat endpoint does not support this parameter (a direct request with it never
  returned at all; the identical request without it returned normally in ~5s).
- Expanded `aletheore audit` to full CLI + API coverage across every major provider: Claude
  (`claude` CLI / `anthropic` API), OpenAI (`codex` CLI / `openai` API), Google (`gemini-cli`
  CLI / `gemini` API), Mistral (`mistral-vibe` CLI / `mistral` API), and xAI (`grok-build` CLI
  / `grok` API), alongside the existing `opencode` CLI and local, key-free `ollama`. Twelve
  `--agent` values total. CLI-based adapters never touch Aletheore's own network code (the
  vendor's own CLI manages its own auth and network calls), so they skip the consent prompt;
  every API-key-based adapter still shows it every single time.
- Added multi-provider support to `aletheore audit`: OpenCode, OpenAI, Mistral, xAI Grok,
  Ollama (local), and Gemini alongside the existing Claude Code adapter. Interactive runs
  always show a provider-selection menu, even with only one available; non-interactive runs
  require `--agent` explicitly. Every run using an API-based provider shows a fresh consent
  prompt naming the exact provider before any data leaves the machine - never remembered,
  every single time. API keys are checked from each provider's standard environment variable
  first, with an explicit prompt-and-choose-to-save-or-discard flow if missing. The API-based
  providers can only ever read this repository's already-computed evidence, never raw source
  files - a hard architectural boundary, not a setting.

## 0.3.0 — 2026-07-16

- Added live progress reporting to `scan`/`audit` — every major phase (module graph build,
  git history, secrets, vulnerability/license checks, endpoint mapping) prints as it starts,
  and dependency-license checking (a real, sequential, one-request-per-dependency network
  call — the least visible part of a scan) reports per-dependency progress. On a real
  terminal the per-dependency counter updates in place; piped to a log or CI, every message
  prints on its own line instead, since `\r` only means "return to start of line" on an
  actual TTY. `audit`'s wait on the coding-agent subprocess now shows an elapsed-time
  indicator too, so a multi-minute run doesn't look identical to a hang.
- Switched the MCP server's tool results and the file the `audit` command's coding-agent
  adapter reads from JSON to [TOON](https://toonformat.dev) (Token-Oriented Object Notation)
  - a lossless, more token-efficient re-encoding of the same data (~30-60% fewer tokens,
    confirmed directly against Aletheore's own evidence shape). `.aletheore/evidence.json`
    stays the canonical on-disk format (the dashboard and any external tooling still need
    real JSON); a second `.aletheore/evidence.toon` file is written alongside it
    specifically for the audit flow, and the manual's operating instructions now explain the
    TOON syntax briefly for the agent reading it.
- **Fixed a real, actively misleading bug in `aletheore dashboard`**: it printed "Dashboard
  running" and opened a browser tab *before* actually trying to bind the port, so if the port
  was already taken (e.g. a dashboard left running for a different repo), the browser silently
  connected to that other, unrelated process instead — a reload looked like a working live
  dashboard while actually showing a completely different repo's data. Now checks the port
  first and fails with a clear message, without opening the browser, if it's already in use.
- Migrated the CLI from `argparse` to [Typer](https://typer.tiangolo.com) + [Rich](https://rich.readthedocs.io):
  every subcommand now gets a properly formatted, colored `--help` automatically (previously
  only the top-level `--help` had any real formatting - every subcommand showed argparse's bare
  default). The colorful `ALETHEORE` banner on a bare `aletheore` invocation is now a real Rich
  panel. Every existing flag name and behavior is preserved exactly (`--no-check-vulnerabilities`,
  `--base-url`, etc.); the only user-visible addition is that flags like `--no-check-licenses`
  now also have an explicit positive counterpart (`--check-licenses`) for free, from Typer's
  `--flag/--no-flag` pair syntax.

## 0.2.1 — 2026-07-16

- **Fixed `aletheore audit` being completely broken on every real `pip install`.** `manual/`
  (the operating instructions the coding-agent adapter reads to write a grounded report) was
  never included in the packaged wheel, and even if it had been, `MANUAL_DIR`'s path
  computation (`parent.parent`) only resolved correctly in the dev repo's layout, not an
  installed one. Fixed by moving `manual/` inside the `aletheore` package itself (next to
  `static/`, which already worked correctly), fixing the path computation to match, and adding
  it to `package-data`. Verified by downloading the actual broken `0.2.0` wheel and confirming
  `manual/` was absent from it, then building and installing a real wheel with the fix and
  running a full `aletheore audit` end-to-end against it.
- Added a proper first-run CLI experience: running bare `aletheore` (or `aletheore --help`)
  now shows a bordered banner explaining what the tool is and a one-line summary of every
  command, instead of a bare `usage:` line with no context.

## 0.2.0 — 2026-07-16

- **Renamed the project from Veridion to Aletheore** (package, CLI command, MCP tool prefixes,
  `.veridion/` → `.aletheore/` config convention, GitHub repo) and moved the repo from the
  personal `ArihantK15` account into the new `Aletheore` GitHub organization. Everything below
  this point reflects the new name; the `0.1.1` and `0.1.0` entries are left as a historical
  record under the name that was actually live at the time, not rewritten.
- Added `.github/workflows/tests.yml` — the test suite now actually runs in CI on every
  push/PR, across Python 3.11 and 3.12. Previously nothing ran it automatically.
- Added real PyPI packaging (full metadata in `prototype/pyproject.toml`) and
  `.github/workflows/publish-pypi.yml`, which publishes via trusted publishing whenever a
  GitHub Release is published. Not live yet — needs the PyPI-side trusted-publisher
  registration first.
- Added a secrets baseline: `.aletheore.json`'s new `accepted_secrets` key lets a known,
  reviewed finding (e.g. a fake key in a test fixture) stop blocking `--fail-on-new-secrets`
  permanently, without hiding it from evidence, queries, the dashboard, or the PR comment.
- The module dependency graph now understands seven new languages beyond the original
  Python/JavaScript/TypeScript: **Go**, **Rust**, **Java**, **Ruby**, **PHP**, **C/C++**, and
  **C#** — each with its own import-resolution model (package-directory fan-out, `crate`/
  `self`/`super` path walking, per-file source-root inference, `require`/`require_relative`,
  PSR-4 autoloading, quoted `#include`, and namespace-directory fan-out with `RootNamespace`
  handling, respectively), verified against real compiled/executed code in each language
  (`cargo build`, `javac`, `ruby`, `php`, `clang++`, `dotnet run`) rather than hand-written
  fixtures alone.
- Added dependency license checking, alongside secrets/vulnerabilities: every pinned PyPI/npm
  dependency's registry-declared license is categorized as permissive, copyleft-weak, or
  copyleft-strong, with only non-permissive ones surfaced as findings. Also detects the repo's
  own declared license. New `aletheore query licenses` / `aletheore_licenses` MCP tool (14
  tools, up from 13), `--no-check-licenses` flag on `scan`/`audit`.
- Added static API endpoint mapping for Flask, FastAPI-style decorators, Django, and Express
  as a new `repository.api_endpoints` evidence block, with a `aletheore query endpoints` /
  `aletheore_endpoints` MCP tool (15 deterministic/query tools, up from 14), a
  `--no-map-endpoints` flag, and tracking of added/removed endpoints in `aletheore diff`.
- Extended static API endpoint mapping to 8 more frameworks across 6 languages: Go (stdlib
  `net/http`/`gorilla/mux`, and Gin), Rust (Axum), Java (Spring Boot), Ruby (Rails), PHP
  (Laravel), and C# (both attribute-routed Controllers and Minimal API) - 10 frameworks total
  now, up from 4. Endpoint entries gain a `note` field for same-file prefixes that aren't
  composed into the recorded path (Spring Boot's class-level `@RequestMapping`, C#'s `[Route]`
  template, Laravel's `Route::group` prefix), alongside the existing `unresolved` flag for
  distinct mount/include-style indirection (Go's `.PathPrefix().Subrouter()`, Axum's `.nest`,
  Rails' `resources`, C#'s `MapGroup`).
- Added `aletheore healthcheck --base-url <url>` and a matching `aletheore_healthcheck` MCP tool:
  a GET-only live check of an app's mapped endpoints against a running instance. Deliberately
  kept outside the deterministic evidence/diff model, since it depends on live runtime state,
  not just repo content. The full MCP surface is now 16 tools including healthcheck.

## 0.1.1 — 2026-07-16

- The `Veridion Diff` GitHub Action now posts its findings as a PR comment (updating the same
  comment on later pushes) instead of only exposing a `diff-json` step output.
- Added `fail-on-new-vulnerabilities` and `fail-on-new-layer-violations` inputs (and matching
  `veridion diff` CLI flags), alongside the existing `fail-on-new-secrets`.
- Dependency-vulnerability checking is now actually enabled in the Action's scan steps — it
  was previously skipped via `--no-check-vulnerabilities`, which would have made the new
  vulnerabilities fail-gate permanently dead.
- Added inline Checks-API annotations for new secrets, landing on the exact changed line in
  a PR's "Files changed" tab.
- The Action now writes to the run's Step Summary on every run, not just `pull_request` events,
  so a plain push still shows results somewhere.

## 0.1.0 — 2026-07-16

- First tagged release. Published as the `Veridion Diff` GitHub Action on the Marketplace: a
  composite Action that scans a PR's base and head refs and diffs them — new/resolved secrets,
  secrets found in git history, dependency vulnerabilities, layer-convention violations, and
  aggregate deltas (module/edge/commit counts).
- Everything the Action builds on already existed in the CLI before this release: `veridion
  scan`/`audit`/`query`/`diff`, an MCP server (13 tools), and a local live dashboard.
