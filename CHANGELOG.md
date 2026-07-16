# Changelog

Notable changes to Aletheore, by release. The working code lives in `prototype/` — see
[`prototype/README.md`](prototype/README.md) for the full command reference.

## Unreleased

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
