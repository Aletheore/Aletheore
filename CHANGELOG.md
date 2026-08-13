# Changelog

Notable changes to Aletheore, by release. The working code lives in `src/` — see
[`src/README.md`](src/README.md) for the full command reference.

## 0.8.5 — 2026-08-13

- **The `[file]` context was spent on every symbol, and mostly diluted them.** It exists to
  break ties between near-identical chunks, but it was attached to every symbol in a file
  whether or not that symbol had a tie to break — so the same sentence was repeated across
  every chunk of the file, and each symbol's own text carried proportionally less weight.
  It now goes only to symbols whose name is declared in more than one file, which is the
  collision it was built for: `serde` declares `deserialize` in 57 files, `slimphp/Slim`
  declares `__invoke` in four. Measured across four corpora and 77 questions, against 0.8.4:
  `pallets/flask` top-1 65.6% → 68.8%, `serde-rs/serde` top-1 46.7% → 53.3%,
  `gin-gonic/gin` top-3 93.3% → 100%, `slimphp/Slim` top-5 60.0% → 66.7%. No corpus
  regressed on any metric; total top-1 across all 77 questions rose 57.1% → 59.7% and MRR
  improved on all four.

- **`aletheore --version` and `aletheore status` reported 0.7.2 on every 0.8.x release.**
  Both read `importlib.metadata.version("aletheore")`, which comes from
  `src/pyproject.toml`, and that file was never bumped past 0.7.2 while `__version__` moved
  to 0.8.4 — so the metadata version and the declared version had drifted five releases
  apart. It also meant no 0.8.x artefact could be published at all, since 0.7.2 was already
  taken on PyPI, which is why `pip install aletheore==0.8.0` does not work today. Both are
  now 0.8.5, and a test asserts they cannot drift again.

## 0.8.4 — 2026-08-13

- **A header-less file lost retrieval ties it should have won.** `slimphp/Slim`'s
  `CallableResolver.php` — the correct answer to "how is a callable given as a string turned
  into something invokable?" — goes straight from `declare(strict_types=1)` to `namespace` to
  `use`, with no header comment at all, while its four lexical competitors (`__invoke` methods
  in sibling files, matching "invokable" on "`__invoke`") all sit in files with a header
  docblock. The `[file]` context feature meant to disambiguate near-identical chunks was
  disambiguating backwards: every wrong answer got a hint, the right one got none. Fixed with a
  fallback, used only when a file has no header comment of its own: the docstring of the class
  or interface the file is named after (PHP/Java/C#/TypeScript's one-type-per-file convention).
  Matched by name against the file's own stem, not "the first symbol in the file," so it can't
  reintroduce the bug the file-header comment logic already guards against (stapling one
  symbol's docstring onto every other symbol in the file).
- Correction to 0.8.3's licence-banner fix: it was real and worth keeping (it was wasting
  embedding budget on 372 chunks), but it was not the cause of PHP's stuck 26.7% top-1 -
  uniform noise across every chunk mostly cancels in relative ranking. This `__invoke`
  collision is the actual cause.

## 0.8.3 — 2026-08-13

- **Licence-banner text was leaking into `[file]` context, actively harming retrieval.**
  `_LEGAL_NOISE` caught licence/copyright lines but not the project banner line that precedes
  them (no legal keyword of its own), and `_file_header_comment` never stripped a trailing
  comment terminator or a leftover bare doc tag. `slimphp/Slim`'s files all open with a banner
  whose `"Slim Framework (https://slimframework.com)"` line survived the filter and whose
  "@api */" line leaked both the tag and the comment closer. Measured: 372 of 455 chunks
  carried a `[file]` context, but only 17 distinct strings across the whole repo - 121 chunks
  shared the identical string, actively diluting every symbol's own body instead of
  disambiguating it. Fixed in three layers: `_LEGAL_NOISE` widened to catch bare
  `@author`/`@package`/`@link`/`@copyright`/`@api` tags and bare URL lines, plus a new
  `_PROJECT_BANNER` regex for "name (https://...)" banners; a trailing comment terminator is
  now stripped unconditionally from any C-style comment line; and, as the durable backstop,
  `build_chunks` now tallies how often each distinct context string recurs across the repo and
  drops any shared by more than `_BOILERPLATE_MIN_REPEAT_COUNT` files, whether or not any regex
  anticipated its shape.

## 0.8.2 — 2026-08-12

- **TypeScript type and interface declarations were never extracted.** `_extract_javascript`
  handled function/class declarations and assigned function expressions, but not
  `type_alias_declaration` or `interface_declaration` - in TypeScript those ARE the public API
  surface, especially for a type-centric library. `colinhacks/zod` has 972 `export type`/
  `export interface` declarations in its core src; 39 files with zero other symbols contained
  210 of them, entirely invisible to the index (`enumUtil.ts`, for example, is entirely type
  declarations inside a namespace). Now extracted into `classes`, the same way Java's and C#'s
  own `interface_declaration` already was - including declarations nested inside a
  `namespace`/`module` body, which zod uses.
- **Declaration-only files (interfaces, `.d.ts`, headers with only prototypes) were crowding
  out implementations in retrieval.** A pure-contract file has rich doc-comments describing
  behaviour with no implementation to dilute them, which makes it unusually attractive to an
  embedder for "how does X work" - measured on `slimphp/Slim`: interfaces were 17 of 72 PHP
  files (24%) and took 18 of 75 top-5 slots (24%), displacing the correct answer on 4 of 6
  misses. Fixed as a demotion, not an exclusion - unlike a test path, an interface is
  legitimately the answer to "where is the contract for X defined?" - via a rank penalty in
  the retriever's reciprocal-rank fusion, detected by path convention (`Interfaces/`,
  `Contracts/`) or per-language content (PHP `interface`, Java/C# `interface`, a Rust `trait`
  with no default bodies, a C/C++ header with only prototypes, a TypeScript file with type/
  interface declarations and no implementation). These two had to ship together: extracting
  TypeScript types without demoting them would have made the crowding-out problem worse.

## 0.8.1 — 2026-08-12

- **Ruby constants were never extracted.** The 0.8.0 module-constants extraction required
  file scope (`is_top_level`), but Ruby constants are idiomatically declared inside a module
  or class body, not at file scope - a real repo scan (`sinatra/sinatra`) found 10 constants
  indented inside module/class bodies and 0 at true top level, so scanning all 147 modules
  yielded a single constant, from a test file. Now accepts a capitalised assignment nested
  directly in a `class`/`module` body (`Sinatra::Base::DROP_BODY_RESPONSES`) in addition to
  true top level; a capitalised assignment inside a `def` body stays excluded as a
  method-local.

## 0.8.0 — 2026-08-12

Scanner coverage across every supported language, plus the retrieval and wiki work that
depends on it. Measured, with the harness and raw results published at
[Aletheore/aletheore-benchmarks](https://github.com/Aletheore/aletheore-benchmarks).

**Three languages had no working dependency graph.** Everything downstream — clustering,
subsystem naming, importance ranking, AIRview, layer violations — consumes that graph, so
their output was structurally wrong while looking normal.

- **CommonJS produced an empty graph.** Only ESM `import` was extracted, never `require()`.
  `expressjs/express` scanned as 141 modules with 0 resolved imports, so community detection
  emitted one cluster per file. Now 125/141 modules, 159 edges, 27 clusters.
- **Rust failed two ways, silently.** `serde-rs/serde` scanned as 208 modules with 0 edges:
  Cargo workspaces were unsupported (only `<repo>/src/lib.rs` was checked), and `mod foo;` —
  how a crate declares its module tree — was not treated as an edge.
- **C#** resolved nothing for flat projects whose namespace comes from `<RootNamespace>`
  with no mirroring directories.
- **JavaScript missed assigned function expressions.** Express defines its whole surface as
  `app.use = function use(fn) {...}`; 102 of its 141 files had no symbols at all.
- **Module-level constants are now extracted in all 11 languages**, not just Python. A file
  can export a public API with no function or class — Flask's `signals.py` is ten
  assignments exporting ten public signals, and was invisible to every consumer of the
  evidence. `symbols.constants` is present on every module.

**Retrieval.** Each symbol chunk now carries its file's header comment, which disambiguates
near-identical symbols in trait-heavy code — serde defines `deserialize` in 57 different
files. Rust top-5 went 60.0% → 73.3%, Python top-3 93.8% → 96.9%. Constants are indexed only
for files that define nothing else, so declaration-only files become findable without
diluting files that already have code.

**Ranking.** File importance now counts symbol size and public-API surface, not just
in-degree — entry points sit at the top of the import tree so almost nothing imports them,
which had `requests`' `api.py` (its entire public API) ranked 17th behind `compat.py`, a
compatibility shim. Symbols shown to a writing model are ordered public-first by source span
rather than concatenated by kind and truncated, which had left Flask's `app.py` showing 15
symbols, all functions, with the `Flask` class itself invisible.

## 0.7.1 — 2026-08-07

- Closed 3 known vulnerabilities (PYSEC-2026-3552/3553/3554) by bumping the `cryptography`
  dependency.
- Round-1 hardening from the internal audit report: local `aletheore audit` now runs the same
  citation-verification path as the hosted managed audit (previously duplicated, now shared via
  `aletheore.citation_verifier`); the git-history secret scan is now watchdog-bounded against
  multi-minute hangs on large histories; a report is now clearly marked when it falls back to raw
  agent output instead of silently presenting it as contract-compliant; a secret finding now
  requires real value-shape evidence (entropy + placeholder markers), not just file path, before
  being downgraded to a likely placeholder; production dependencies now have pinned upper bounds
  and are split from test-only deps.
- Enforced evidence schema-version compatibility on every CLI read path
  (query/index/diff/healthcheck/verify), not just the MCP server — closing the gap left by the
  same fix landing MCP-only in 0.7.0.
- Fixed Flash Review discarding real findings about **deleted code**: a citation landing just past
  a deletion-only hunk's collapsed boundary was rejected before its content could be checked,
  silently turning a true positive into "No issues found."
- Fixed Flash Review repeating the same zero-grounded-findings message twice in one comment.
- Fixed `mcp-install` writing the bare command name `"aletheore"` into every coding-tool config
  instead of an absolute path — silently broken whenever the launching tool's subprocess PATH
  doesn't include wherever `aletheore` was actually installed (the common case for a
  pip-installed-in-a-venv install launched by a GUI coding tool). Now resolves to the exact
  install that ran `mcp-install`.
- Three hosted-audit hardening fixes: a fail-closed collaborator permission check before running a
  triggered audit, credential stripping on reused checkouts, and Docker socket isolation via a
  narrow-purpose sidecar (verified live against production).
- Reworked the AIRview diagram zoom into a real pan/zoom toolbar, and polished the hosted
  dashboard, pricing, and developers pages.
- Routine dependency updates: GitHub Actions runners, `rq`, `psycopg`, `tree-sitter`, `typer`,
  `pytest`, `pytest-asyncio`, `cspell`, `prettier`, `markdownlint-cli2`.

## 0.7.0 — 2026-07-31

- Added **Regression Fencing**: flags a changed function signature when a real caller wasn't
  updated in the same PR, distinguishing a genuinely breaking change from an additive,
  backward-compatible one (e.g. a new required parameter vs. a new optional one with a default).
  Posts a signed Check Run a repo can require in branch protection.
- Systematic audit of the grounding system across Flash Review, AIRview, and the Managed Audit
  report: citations in the Managed Audit report are now verified against real evidence before
  signing (previously prompt-based only); every grounding rejection is now logged with its
  file:line and reason instead of failing silently; AIRview no longer deletes an entire
  subsystem over one unverified sentence (retries once, then keeps the deterministic diagram/file
  list with just the prose withheld); Flash Review discloses when a PR was too large to fully
  review instead of reporting "No issues found" identically either way; citations against files
  with no extension (`Dockerfile`, `Makefile`) are now checked instead of silently ignored; a
  citation at line 0 is now rejected.
- Fixed Flash Review dropping correct findings about **deleted code**: a deletion-only diff hunk
  collapses to just its context lines, so a finding about the removed code was rejected as
  "outside the diff" before the content check could weigh in.
- Fixed the AIRview diagram zoom overlay on genuinely large Mermaid graphs: it scaled via CSS
  `transform: scale()`, which grows the painted appearance but not the scrollable layout size,
  leaving large sections of a big diagram permanently unreachable by scroll.
- Completed the AIR paid-tier feature set: real Microsoft Teams alert support (Slack's classic
  webhook format was retired; now auto-detects and sends the current Adaptive Card format), a
  "send test notification" button for the alert webhook, real per-seat Paddle billing, an
  endpoint health history/trend view, and push-triggered incremental rescans.
- Disabled forced `tool_choice` for the `deepseek` adapter — `deepseek-v4-pro` runs in thinking
  mode by default, which rejects `tool_choice="required"`.
- Fixed three CLI output bugs found dogfooding the actual install → first-run path: the no-args
  banner and `init`'s config-key descriptions wrapped long text back to the terminal's left edge
  instead of staying indented under their column; `scan`/`audit` completion messages could get a
  real newline inserted mid-filename by the fixed-width result box, corrupting a copied path.

## 0.6.1 — 2026-07-28

- Fixed `aletheore_search_codebase`/`aletheore_answer` telling an MCP-connected agent to run
  `aletheore index <path>` (a shell command it can't execute) when the semantic index hasn't been
  built yet, instead of pointing it at the `aletheore_index` tool it actually has.

## 0.6.0 — 2026-07-28

- Gave the CLI its own on-disk incremental-scan cache (content-hash keyed) plus on-disk
  license/vulnerability registry-lookup caches, so a repeat `scan`/`audit` on an unchanged repo
  skips re-parsing and re-querying work it already did.
- Hardened the module-graph builder against relative-import path escapes in Ruby, PHP, C/C++,
  Java, and C# (a coincidentally-matching package/namespace and directory name could previously
  crash the scan with an unhandled `ValueError`), fixed Java/C# files being parsed twice per
  scan, and made repo walks skip symlinked files and directories instead of following them.
- Verified LLM-claimed citation lines against real file content instead of just file existence,
  closing a grounding gap in audit output.
- Regenerated MCP tool docs from the actual server registry, gave each dynamic MCP query tool
  its own description, routed the MCP managed-audit tool through the shared credential store,
  added an `aletheore_index` tool to build the semantic search index on demand, and cached
  parsed evidence in-process so repeated MCP queries against the same evidence file don't
  re-read and re-parse it.
- Added a durable, incrementally-updated code graph (files/symbols/edges/endpoints) backing the
  hosted service, with a persistent-checkout + skip-unchanged-files fast path, anonymous CLI scan
  usage telemetry, and Sentry-compatible runtime event ingestion for zero-hop debugging.
- Added a DeepSeek adapter, parallelized dependency license checks instead of running them
  serially, and fixed the GitHub Action workflow's git worktree/submodule exclusion and
  first-commit-lookup performance.
- Hardened the hosted GitHub App: automatic GitHub access-token refresh for long-lived sessions,
  fixed several dashboard issues (401 reload loop, missing security findings, endpoint display,
  AIRview/Live Wiki sections, Mermaid graph rendering), and added a monthly scanned-repos cap.
- Gave the CLI real spinner animation (in place of a static arrow) on long-running phases and
  wrapped scan/audit/managed-audit completion messages in a bordered panel, matching the
  existing banner/sponsor panel style.

## 0.5.0 — 2026-07-23

- Launched the redesigned Aletheore marketing website with clearer positioning, pricing,
  developer documentation, social links, sitemap coverage, and mobile navigation fixes.
- Added the hosted GitHub App foundation and hardening: PR scan workers, managed audit
  plumbing, health monitoring, public health APIs, deployment documentation, security
  workflows, SBOM/image scanning, and operational runbooks.
- Expanded evidence grounding across alerts, reviews, audits, and queries so product output
  can resolve back toward concrete code evidence such as file, line, symbol, owner, commit,
  dependency, and risk.
- Added deeper repository intelligence, including API endpoint mapping, multi-language
  endpoint support, database and infrastructure detection, threat-model perspective work,
  dependency manifest fallbacks, embedding fallbacks, evidence packet caching, and
  deterministic enrichment foundations.
- Improved the developer experience around the CLI, MCP server, query commands, AIRview,
  status/login flows, provider adapters, release checks, and prelaunch CI.

## 0.4.0 — 2026-07-18

- Extended dependency vulnerability/license checking to cover manifests as well as lockfiles,
  so a project isn't silently reported as "0 findings" (indistinguishable from a clean scan)
  when its dependencies are declared somewhere the lockfile-only parsers didn't read - verified
  against real repos before and after: Django (no root `requirements.txt` - declares deps in
  `pyproject.toml`), `spring-petclinic` (Spring Boot's BOM-inherited dependency versions),
  `apache/dubbo` (57-module multi-module repo), `serde`/`guzzle` (popular libraries that ship no
  lockfile at all), and Microsoft's `eShopOnWeb` (.NET Central Package Management). Python now
  additionally parses `pyproject.toml` (PEP 621 and Poetry); npm prefers the resolved version
  from `package-lock.json` over `package.json`'s declared range when a lockfile is present; Rust,
  PHP, Ruby, and C# each fall back to their manifest (`Cargo.toml`, `composer.json`, `*.gemspec`,
  `.csproj`/`Directory.Packages.props`) when no lockfile exists; Maven now resolves
  `${property}`-style versions and same-file `dependencyManagement`-inherited versions, and
  recurses into every module listed in a multi-module `pom.xml` - while also fixing a
  over-counting bug where the old lookup incorrectly pulled in profile-only and
  dependencyManagement-only entries as if they were the project's real active dependencies
  (confirmed on `dubbo`: 6 real dependencies vs. 46 falsely matched).
- Added vulnerability/license checking for six more ecosystems beyond Python and JavaScript: Go
  (`go.mod`, via the official `pkg.go.dev` v1beta API), Rust (`Cargo.lock`, crates.io), Java
  (`pom.xml`, Maven Central), Ruby (`Gemfile.lock`, RubyGems), PHP (`composer.lock`, Packagist),
  and C# (`packages.lock.json`, NuGet) - live-verified against a real Kubernetes scan (206 Go
  dependencies, 9 vulnerability findings, 40 license findings).
- Added `aletheore status`: reports the installed version, whether a newer release is available
  on PyPI, and current login state.
- Added `aletheore login`: GitHub OAuth device-flow authentication (no client secret needed,
  no browser redirect - a device code is shown, approved on github.com, and the CLI polls until
  approved).
- Added local semantic code search and retrieval-grounded Q&A: `aletheore index` builds a
  LanceDB index over symbol-bounded code chunks using local Ollama embeddings
  (`nomic-embed-text`), `aletheore query search-codebase` returns TOON-encoded semantic
  matches, and `aletheore query answer` reuses the provider adapter infrastructure for cited
  answers with a distance-based confidence gate. Extracted symbols now include exact
  1-indexed `start_line`/`end_line` bounds across supported languages.
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
