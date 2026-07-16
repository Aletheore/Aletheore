<p align="center">
  <img src="../assets/logo.png" alt="Veridion" width="360">
</p>

# Veridion Prototype

**Status:** Unratified prototype under VDP-0000-REQ-009 ("Prototypes and experiments MAY
precede specifications, but they MUST NOT become normative without the VDP process").

This directory is deliberately out-of-band from the constitutional apparatus in
`../constitution/`, `../GOVERNANCE.md`, `../VISION.md`, and `../ROADMAP.md`. It does not
modify, supersede, or depend on any of that. It is not a proposal and should not be treated as
one — it's just the actual, working tool.

## What this is

A deterministic scanner (tree-sitter + git log, no LLM, fully unit-tested) reads a repository
and writes `.veridion/evidence.json`: languages, module dependency graph, modularity-based
clusters, git ownership and commit cadence, secrets, dependency vulnerabilities, layer-
convention violations, dependency licenses, and static API endpoint maps. Every other feature
below is built on top of that same evidence and
never states anything it can't cite back to a specific field in it.

Secrets, git activity, and dependency-vulnerability checks are language-agnostic. The module
dependency graph (imports, clusters, layer violations) currently understands **Python,
JavaScript/JSX, TypeScript/TSX, Go, Rust, Java, Ruby, PHP, C, C++, and C#** — other languages
are still scanned for secrets/git/vulnerabilities, but get no dependency-graph or architecture
analysis until a grammar is added for them.

Go resolution needs a `go.mod` at the repo root to know the module's own import-path prefix;
without one, Go imports are left unresolved (same as any import Veridion can't place) rather
than guessed at. An import is resolved to every non-test `.go` file in its target directory,
since Go imports whole packages, not individual files.

Rust resolution needs `src/lib.rs` or `src/main.rs` at the repo root (workspace repos with
multiple crates aren't supported yet); without one, nothing resolves. It assumes directory
structure mirrors the module tree (true for the vast majority of real code; `#[path = "..."]`
escape hatches aren't supported), and handles `crate::`/`self::`/`super::` paths, the implicit
crate-relative form (`use handlers::Handler;` from the crate root), grouped (`{Bar, Baz}`),
wildcard (`::*`), and aliased (`as`) forms.

Java resolution has no repo-root config to read at all (no go.mod/Cargo.toml equivalent) - the
source root (Maven/Gradle's `src/main/java`, a bare `src/`, or the repo root itself) is
inferred per-file from each file's own `package` declaration matching its actual directory,
so it works across layouts without assuming one. Handles direct imports, wildcard imports
(fanning out to every `.java` file in that package, same idea as Go's package-level imports),
and `import static` (resolving to the class, not the imported member).

Ruby's `require_relative` always resolves relative to the current file, unambiguous. Plain
`require` is genuinely ambiguous (the overwhelming majority are gems, external), so it only
resolves against a repo-root `lib/` directory - the near-universal Ruby convention for a
project's own internal requires - and is left unresolved otherwise, same as an unrecognized
import in any other language here.

PHP reads `composer.json`'s `autoload.psr-4` mapping (namespace prefix -> directory, longest
prefix wins when more than one could match) to resolve `use` statements; with no composer.json,
`use` doesn't resolve at all. `require`/`require_once`/`include`/`include_once` (including the
idiomatic `__DIR__ . '/../lib/util.php'` form) resolve relative to the current file, the same
as Ruby's `require_relative`.

C/C++ only resolves quoted `#include "foo.h"` (relative to the current file's own directory,
the only part of the real preprocessor search order knowable without a build system's `-I`
flags) - angle-bracket `#include <foo.h>` is always treated as external/system, never resolved,
since a project using `<>` for its own headers via `-I` isn't distinguishable from a real system
header without that same build info. `.h` is parsed with the C++ grammar (a superset that
parses valid C too) since header files are ambiguously C-or-C++.

C# resolves `using Namespace;` at namespace granularity, not class granularity - unlike every
other language here, a C# `using` doesn't name a specific type at all, only a namespace, so it's
resolved the same way Go's package-level import already is: fan out to every `.cs` file in the
directory that namespace corresponds to (namespace-mirrors-directory is only a convention here,
not compiler-enforced, so real misses are expected for code that doesn't follow it). Also
accounts for `<RootNamespace>` (set by every `dotnet new` template by default), which prepends
an implicit prefix to every file's effective namespace with no corresponding directory on disk
at all - verified directly against a real `dotnet build`/`dotnet run`, which is also what
surfaced this: a naive "namespace must fully mirror the directory" version (correct for Java,
which has no such feature) resolved nothing at all until this was accounted for.

## Setup

Not yet on PyPI (the packaging and a tag-triggered publish workflow exist, but nothing has
been published - see `../.github/workflows/publish-pypi.yml`). Once it is:

```bash
pipx install veridion   # or: pip install veridion
```

Until then, install from source:

```bash
cd prototype
pip install -e ".[dev]"
pytest
```

Requires Python 3.11+.

## Configuration

A scanned repo can commit a `.veridion.json` at its root to extend the architecture checks —
it's read as part of `scan`/`audit`, the same deterministic way `requirements.txt` or a policy
doc already is (repo-declared conventions are themselves a fact about the repo, so this
doesn't break reproducibility: same repo content in, same evidence out).

```json
{
  "layer_markers": { "biz": 1 },
  "cluster_resolution": 1.5,
  "accepted_secrets": [
    { "path": "tests/fixtures/sample.py", "pattern": "aws_access_key_id", "match_preview": "AKIA****...MNOP" }
  ]
}
```

- `layer_markers` — extends/overrides the built-in folder-name -> layer-rank table used by
  layer-violation detection (e.g. a repo using a `biz/` folder that isn't one of the built-in
  names would otherwise never get `convention_detected: true`). Merges with the built-in table
  for non-overlapping keys; only overlapping keys get overridden.
- `cluster_resolution` — passed straight into the modularity-clustering algorithm (default
  `1.0`). Higher values favor more, smaller clusters; lower values favor fewer, larger ones.
- `accepted_secrets` — a baseline of reviewed, accepted secret findings (e.g. a genuinely
  fake key in a test fixture that will always match a pattern). Every secrets scanner needs
  this: without it, `--fail-on-new-secrets` has no escape hatch for a known false positive -
  one review-and-accept, and it stops blocking CI, permanently, for that exact finding. Match
  on the finding's exact `path`, `pattern`, and `match_preview` (copy these from a scan's
  output or `veridion query secrets <path>` - `match_preview` is already redacted, safe to
  commit). Accepted findings are **not hidden** - they still appear in `evidence.json`,
  `veridion query secrets`, the dashboard, and the PR comment, each flagged
  `"accepted": true`/labeled "accepted (in .veridion.json baseline)" - only the fail-gates and
  inline PR annotations skip them.

All three keys are optional and independently defaulted/empty if the file is missing,
malformed, or only sets some of them. `layer_markers`/`cluster_resolution` (or `null` if
there's no config file at all) are recorded verbatim in `evidence.json` at
`architecture.config_applied`, so a report can cite exactly what convention was in effect for
that scan.

## Commands

### `veridion scan [path]`

Runs only the deterministic scan phase. Writes `.veridion/evidence.json` and a rolling history
snapshot under `.veridion/history/`. No LLM call — safe to run repeatedly, in CI, or from a
script.

```bash
veridion scan .
veridion scan . --no-check-vulnerabilities   # skip the OSV.dev dependency check
veridion scan . --no-scan-git-history        # skip walking git history for secrets
veridion scan . --no-check-licenses          # skip the dependency-license check
veridion scan . --no-map-endpoints           # skip static API endpoint mapping
```

The license check reads each pinned PyPI/npm dependency's registry metadata (PyPI's `license`
field falling back to its OSI classifiers; npm's `license` field) and categorizes it as
`permissive`, `copyleft-weak` (LGPL, MPL, EPL), `copyleft-strong` (GPL, AGPL), or `unknown` —
only non-permissive dependencies show up as findings, the same way OSV vulnerability checking
only reports actual vulnerabilities, not every clean dependency. It also detects the repo's own
declared license (`pyproject.toml`'s `license` field, `package.json`'s `license` field, or
pattern-matching a `LICENSE` file's text) so a report can flag a copyleft dependency alongside
what license the repo itself claims to be under - a factual categorization, not a legal
compatibility verdict, which is genuinely subjective and outside what a deterministic scanner
should claim.

Static API endpoint mapping records `repository.api_endpoints` for Flask, FastAPI-style
decorators, Django `urlpatterns`, and Express route calls. It is intentionally source-derived:
literal route declarations are recorded with method, path, framework, file, line, handler, and
whether the entry is an unresolved include/mount.

### `veridion audit [path]`

Runs a scan, then shells out to an installed coding-agent CLI (Claude Code today, via
`--agent` to force a specific one) to write a full grounded report to
`.veridion/audit-report.md`, following the per-section instructions in `manual/` (repository
intelligence, git intelligence, architecture, security, AI-usage detection, audience
perspectives, roadmap synthesis) and citing exact evidence fields throughout.

This is a genuinely different kind of operation from everything else in this list: it spawns
a full second agent CLI process (up to a 10-minute timeout) to produce prose, rather than
answering a fast, deterministic query. It's meant to be run by hand, when you actually want a
written document — it is not wired into CI or the MCP server, and shouldn't be: a CI gate
needs to be fast and pass/fail on concrete facts, and an agent already driving an MCP session
can reason over the evidence itself without spawning a nested agent process.

```bash
veridion audit .
veridion audit . --agent claude
```

### `veridion query <kind> [target]`

Answers one targeted question from an existing `evidence.json`, without re-scanning or an LLM
call.

```bash
veridion query imports app/routes.py --path .
veridion query imported-by app/routes.py --path .
veridion query symbols app/routes.py --path .
veridion query branch main --path .
veridion query ownership --path .
veridion query secrets app/routes.py --path .        # findings within just that file
veridion query vulnerabilities --path .
veridion query licenses --path .
veridion query endpoints --path .
veridion query cluster app/routes.py --path .
veridion query layer-violations --path .
veridion query changes --path .              # diff against the previous history snapshot
```

### `veridion diff <old.json> <new.json>`

Compares two `evidence.json` files directly — new/resolved secrets, API endpoints, layer
violations, dependency vulnerabilities, architecture deltas. Powers the GitHub Action below.

```bash
veridion diff old/evidence.json new/evidence.json
veridion diff old/evidence.json new/evidence.json --fail-on-new-secrets
veridion diff old/evidence.json new/evidence.json --fail-on-new-vulnerabilities
veridion diff old/evidence.json new/evidence.json --fail-on-new-layer-violations
```

All three `--fail-on-new-*` flags can be combined; the command exits 1 if any of them find
something new.

### `veridion healthcheck [path] --base-url <url>`

Runs a GET-only live check of mapped API endpoints against a running app instance. This reads
`repository.api_endpoints` from evidence, substitutes placeholder values for path parameters
such as `<int:id>`, `{id}`, and `:id`, skips non-GET endpoints without calling them, and writes
a rotated result file under `.veridion/healthchecks/`.

This command depends on live runtime state, so it is deliberately **not** part of deterministic
scan evidence or `veridion diff`.

```bash
veridion healthcheck . --base-url http://127.0.0.1:5000
```

### `veridion mcp [path]`

Starts a stdio MCP server scoped to one repository, so a coding agent can query its structure
directly instead of shelling out via Bash or re-reading files on every lookup. Exposes 16
tools:

- The 11 query kinds above as tools (`veridion_imports`, `veridion_imported_by`,
  `veridion_symbols`, `veridion_branch`, `veridion_ownership`, `veridion_secrets`,
  `veridion_vulnerabilities`, `veridion_licenses`, `veridion_endpoints`, `veridion_cluster`,
  `veridion_layer_violations`), plus `veridion_changes`.
- `veridion_neighborhood(target)` — a module's imports, dependents, and cluster in one call,
  instead of three round-trips.
- `veridion_search(pattern, regex=False, path_glob=None)` — literal or regex full-text search
  over tracked source files, capped at 200 matches.
- `veridion_scan()` — triggers a fresh deterministic scan and returns a compact summary (not
  the full evidence dump). Does **not** run the agent-driven `audit` report — see the note
  under `veridion audit` above for why that's a deliberate boundary, not a gap.
- `veridion_healthcheck(base_url)` — runs the same GET-only live health check as the CLI and
  persists the result under `.veridion/healthchecks/`.

```bash
veridion mcp .
```

### `veridion dashboard [path]`

A live local web UI (Starlette + SSE, opens in your browser): repo overview, git activity,
trend charts for module/secrets/vulnerability counts across scan history, an interactive
dependency graph, a separate community-aware "clusters" graph with zoom/pan, and the list of
MCP tools available for the repo.

```bash
veridion dashboard . --port 8420
```

## GitHub Action

`../action.yml` ("Veridion Diff" on the Marketplace) is a composite Action that scans a PR's
base and head refs and reports the diff three ways:

- **A PR comment** — new/resolved secrets, new/resolved secrets found in git history,
  new/resolved dependency vulnerabilities, new/resolved layer-convention violations, and
  aggregate deltas (module count, dependency-graph edge count, commit count). Updates the same
  comment on subsequent pushes instead of spamming new ones.
- **Inline annotations** on new secrets specifically — shown directly on the changed line in
  the PR's "Files changed" tab. Scoped to current-tree secrets only, since that's the only
  finding type with both a real file path and a real line number: history-secret findings
  point at an old commit with no line in the current tree, vulnerabilities are package-level,
  and layer violations are file-level (a "from" file imports a "to" file) — none of those have
  a specific line to honestly point at, so they stay in the PR comment rather than getting a
  fabricated line number.
- **The run's Step Summary** — the same content as the PR comment, written on every run
  regardless of event type, so a plain push (no PR to comment on) still shows something.

A secret accepted via `.veridion.json`'s `accepted_secrets` (see Configuration above) is
labeled, not omitted, in the comment and Step Summary, and is excluded from inline annotations
and every `--fail-on-new-*` gate.

It only ever calls `veridion scan` and `veridion diff`, matching the reasoning above: CI needs
something fast and deterministic, not a full agent-driven audit.

```yaml
- uses: ArihantK15/Veridion@master   # pin to a tagged release once one exists past 0.1.0
  with:
    fail-on-new-secrets: true              # exit 1 if a new real (non-placeholder) secret appears
    fail-on-new-vulnerabilities: true      # exit 1 if a new dependency vulnerability appears
    fail-on-new-layer-violations: true     # exit 1 if a new layer-convention violation appears
```

Posting the PR comment needs `permissions: pull-requests: write` (and `issues: write`, since
PR comments use the Issues API) on the calling workflow's job — set `post-pr-comment: false`
to skip just that part and still get annotations, the step summary, and the `diff-json`
output.

## Continuity

Every `scan` (and `audit`, which scans first) saves a timestamped snapshot to
`.veridion/history/` (last 20 kept). `veridion query changes` / `veridion_changes` diff the
two most recent snapshots, and the dashboard's trend charts read the full history.
