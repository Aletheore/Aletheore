<p align="center">
  <img src="../assets/logo.png" alt="Aletheore" width="360">
</p>

# Aletheore

Evidence-grounded repository intelligence: a deterministic scanner (tree-sitter + git log, no
LLM, fully unit-tested) reads a repository and writes `.aletheore/air.json` — languages, module
dependency graph, modularity-based clusters, git ownership and commit cadence, secrets,
dependency vulnerabilities, layer-convention violations, dependency licenses, and static API
endpoint maps. Every other feature below (the AI-written audit report, the GitHub Action's PR
comments, the MCP server, the dashboard) is built on top of that same evidence and never states
anything it can't cite back to a specific field in it.

This is the real, working CLI package — the same code that runs in CI (a real test suite,
1,600+ tests across this package and the hosted service) and in production behind the hosted
GitHub App (see `../github-app/`). Full project overview: [`../README.md`](../README.md).

## Quickstart

```bash
pipx install aletheore
aletheore demo           # optional: real scan of a small synthetic sample
aletheore scan .
cat .aletheore/air.json   # the evidence everything else reads from
```

That's the whole deterministic path: no LLM call or account. Run
`aletheore scan . --explain-network` to inspect potential traffic without scanning:
dependency registries, Semgrep rule downloads and metrics, configured SonarQube analysis
submission, and conditional crash reporting. For a scan with those network paths disabled:

```bash
ALETHEORE_CRASH_REPORTING=0 aletheore scan . --no-check-vulnerabilities --no-check-licenses --no-check-static-analysis
```

Everything below - the per-language import
resolution details, `audit`'s LLM-written report, the MCP server, the dashboard - builds on
top of that one `air.json` file.

Secrets, git activity, and dependency-vulnerability checks are language-agnostic. The module
dependency graph (imports, clusters, layer violations) currently understands **Python,
JavaScript/JSX, TypeScript/TSX, Go, Rust, Java, Kotlin, Ruby, PHP, C, C++, C#, and Swift** — other
languages are still scanned for secrets/git/vulnerabilities, but get no dependency-graph or
architecture analysis until a grammar is added for them.

Go resolution needs a `go.mod` at the repo root to know the module's own import-path prefix;
without one, Go imports are left unresolved (same as any import Aletheore can't place) rather
than guessed at. An import is resolved to every non-test `.go` file in its target directory,
since Go imports whole packages, not individual files.

Swift resolution works the same way, one level up: a Swift `import` names a whole compiled
target, not a file, so it's resolved to every `.swift` file belonging to that target - inferred
from `Package.swift`'s own `path:` overrides where present, falling back to the SwiftPM
convention (`Sources/<TargetName>/`, `Tests/<TargetName>/`) everywhere else.

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

Kotlin resolution infers its source root the same way Java's does - per-file, from each
file's own `package` declaration matching its actual directory (Gradle's conventional
`src/main/kotlin`, a bare `src/`, or the repo root all work). Unlike Java, a Kotlin file's
name doesn't need to match any top-level declaration - multiple top-level classes/functions
per file are idiomatic and common (confirmed against android/architecture-samples) - so
import resolution tries the same-named `.kt` file first, then falls back to searching the
containing directory for the actual matching declaration.

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

Published on PyPI (`pypi.org/project/aletheore`), released via a tag-triggered publish
workflow (`../.github/workflows/publish-pypi.yml`):

```bash
pipx install aletheore   # or: pip install aletheore
```

To work on aletheore itself, install from source instead:

```bash
cd src
pip install -e ".[dev]"
pytest
```

Requires Python 3.11 or newer (tested on 3.11 through 3.14).

## Configuration

A scanned repo can commit a `.aletheore.json` at its root to extend the architecture checks —
it's read as part of `scan`/`audit`, the same deterministic way `requirements.txt` or a policy
doc already is (repo-declared conventions are themselves a fact about the repo, so this
doesn't break reproducibility: same repo content in, same evidence out).

```json
{
  "layer_markers": { "biz": 1 },
  "cluster_resolution": 1.5,
  "dead_code_entry_points": ["scripts/migrate.py"],
  "accepted_secrets": [
    { "path": "tests/fixtures/sample.py", "pattern": "aws_access_key_id", "match_preview": "AKIA****...MNOP" }
  ]
}
```

`aletheore init [path]` scaffolds this file with all four keys present (empty/default), plus
a one-line explanation of each printed to the console.

- `layer_markers` — extends/overrides the built-in folder-name -> layer-rank table used by
  layer-violation detection (e.g. a repo using a `biz/` folder that isn't one of the built-in
  names would otherwise never get `convention_detected: true`). Merges with the built-in table
  for non-overlapping keys; only overlapping keys get overridden.
- `cluster_resolution` — passed straight into the modularity-clustering algorithm (default
  `1.0`). Higher values favor more, smaller clusters; lower values favor fewer, larger ones.
- `dead_code_entry_points` — extra file paths (beyond what's auto-detected: a framework's
  `main.py`/`app.py`, test files, `__init__.py` re-exports) to treat as reachable roots when
  computing unreferenced code - a script only ever invoked by a cron job or CI step, never
  imported from anywhere else in the repo, would otherwise be flagged as dead code.
- `accepted_secrets` — a baseline of reviewed, accepted secret findings (e.g. a genuinely
  fake key in a test fixture that will always match a pattern). Every secrets scanner needs
  this: without it, `--fail-on-new-secrets` has no escape hatch for a known false positive -
  one review-and-accept, and it stops blocking CI, permanently, for that exact finding. Match
  on the finding's exact `path`, `pattern`, and `match_preview` (copy these from a scan's
  output or `aletheore query secrets <path>` - `match_preview` is already redacted, safe to
  commit). Accepted findings are **not hidden** - they still appear in `air.json`,
  `aletheore query secrets`, the dashboard, and the PR comment, each flagged
  `"accepted": true`/labeled "accepted (in .aletheore.json baseline)" - only the fail-gates and
  inline PR annotations skip them.

All four keys are optional and independently defaulted/empty if the file is missing,
malformed, or only sets some of them. `layer_markers`/`cluster_resolution` (or `null` if
there's no config file at all) are recorded verbatim in `air.json` at
`architecture.config_applied`, so a report can cite exactly what convention was in effect for
that scan.

## Commands

### `aletheore demo`

Creates a fresh synthetic Python checkout example with three local commits, scans it
with the real engine, and shows what Aletheore produces and how to access it:
saved JSON/TOON evidence, a source query, direct and transitive import mapping,
and an actual local, read-only MCP impact-tool call. No coding-agent conversation
or model response is simulated. The printable report also includes candidate tests and hotspots.
Candidate tests are not executed. The dashboard highlights the sample target and labels
checks that did not run. Requires Git;
does not clone, index embeddings, query registries, or invoke external scanners.
In a real terminal, the tour uses the same wordmark and arrow-key menus as the home
screen. Continue or go back one step at a time, open the sample dashboard directly,
or select your own repository. Its network plan is shown before you choose to scan.
The home menu and **All commands → Scan and explore** both include the demo.
Normal scans may enable checks the demo skipped.
Use `aletheore demo --plain` for the printable report and commands. Piped output,
CI, dumb terminals and `ALETHEORE_NO_MENU` also use the printable report.
The sample directory is retained; delete it when finished.
Normal CLI crash-reporting preferences still apply.

### `aletheore init [path]`

Scaffolds a `.aletheore.json` at the repository root with all four Configuration keys above
present (empty/default), refusing to overwrite an existing one. Entirely optional - `scan`/
`audit` work identically with no config file at all.

```bash
aletheore init .
```

### `aletheore scan [path]`

Add `--explain-network` to print the potential network paths and exit without scanning
or prompting for optional scanners. It honors `.aletheore.json`, explicit CLI check
overrides, installed Semgrep/SonarScanner binaries, scanner environment toggles, and
crash-reporting preferences. Enabled paths are conditional on actual inputs and caches;
request counts are not predicted. This is a disclosure, not network enforcement or a
packet trace. External scanner configuration may introduce additional behavior. The
explanation describes `scan`, not `audit`, `index`, or `healthcheck`.

Runs only the deterministic scan phase. Writes `.aletheore/air.json` and a rolling history
snapshot under `.aletheore/history/`. No LLM call — safe to run repeatedly, in CI, or from a
script.

```bash
aletheore scan .
aletheore scan . --no-check-vulnerabilities   # skip the OSV.dev dependency check
aletheore scan . --no-scan-git-history        # skip walking git history for secrets
aletheore scan . --no-check-licenses          # skip the dependency-license check
aletheore scan . --no-map-endpoints           # skip static API endpoint mapping
aletheore scan . --no-check-static-analysis   # skip Semgrep/gosec/Bandit and opt-in Bearer/Joern/SonarQube
aletheore scan . --check-bearer                # include Bearer without being asked
aletheore scan . --check-joern                 # include Joern's Go asymmetric-cache-trust query
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

The static analysis check runs Semgrep, gosec, and Bandit as subprocess scanners (self-skipping
with a reason, not an error, for any binary not on `PATH`) and normalizes their findings into
`security.static_analysis`. Set `ALETHEORE_DISABLE_SEMGREP=1` to skip just Semgrep with a named reason
(it is the slowest of the three on a large repository, and the hosted scan worker sets it). Bearer, Joern, and SonarQube are each opt-in, for different real
reasons. Bearer finds real sensitive-data/PII-flow issues nothing else here does, but its
full-repo runtime doesn't scale cleanly with repo size, so it's never on by default - pass
`--check-bearer`/`--no-check-bearer` explicitly, or, with neither passed, `scan`/`audit` on a
real terminal ask before running it (with the same warning); a non-interactive run (CI, a
script, the hosted worker) skips it by default rather than hang on an unanswerable prompt. Joern
runs one real CFG-based query (Go's asymmetric-cache-trust pattern - two cache guards in the
same function that trust a hit differently, one returning immediately, one re-deriving via a
fresh lookup on some paths) - `--check-joern`, no prompt, since it needs a separate Joern
install (`gosrc2cpg`/`joern` on `PATH`) and a `go.mod` at the scanned root, and a CPG build is
real per-scan JVM-startup-plus-parsing cost, not a fast subprocess call. SonarQube is opt-in and
local-only, since a real SonarQube analysis needs a running server, not
just a CLI. Set `SONARQUBE_HOST_URL` (and, for anything but a fully anonymous local instance,
`SONARQUBE_TOKEN`) to enable it; `SONARQUBE_PROJECT_KEY` defaults to the scanned directory's
own name. With no `SONARQUBE_HOST_URL` set, it's silently skipped like any other unconfigured
check - no server, no cost, no error.

Static API endpoint mapping records `repository.api_endpoints` for Flask, FastAPI-style
decorators, Django `urlpatterns`, Express route calls, Go (`net/http`/`gorilla/mux` and Gin),
Rust (Axum), Java (Spring Boot), Ruby (Rails), PHP (Laravel), and C# (both attribute-routed
Controllers and Minimal API). It is intentionally source-derived: literal route declarations
are recorded with method, path, framework, file, line, handler, whether the entry is an
unresolved include/mount-style indirection, and an optional `note` for known same-file prefixes
that are present but deliberately not composed into the recorded path.

`scan` prints its progress through each major phase as it runs — module graph build, git
history, secrets, vulnerability/license checks, endpoint mapping — since some of these (the
license check especially, one real network request per pinned dependency, no batching) can
take a while with no other feedback otherwise. On a real terminal the license-check counter
updates in place; piped to a log or CI, every dependency prints on its own line instead.

Alongside `air.json`, `scan` also writes `.aletheore/air.toon` — the same evidence,
[TOON](https://toonformat.dev)-encoded (~30-60% fewer tokens for the same data, biggest win on
the uniform arrays of same-shaped objects most of air.json actually is). `air.json`
stays the canonical file for the dashboard and any external tooling; `air.toon` exists
specifically for `audit`'s coding-agent adapter to read instead.

Extracted module symbols include exact 1-indexed line bounds (`name`, `start_line`, `end_line`)
for top-level functions/classes across every supported parser language. Those bounds power the
semantic code index below.

### `aletheore index [path]`

Builds a local LanceDB vector index over the repository's code chunks from an existing scan.
This is explicit and never runs as a side effect of `scan`. Embeddings use local Ollama's
OpenAI-compatible endpoint and `jina-embeddings-v2-base-code` (the same model Aletheore's
hosted tier uses, served locally via its official int8 GGUF quantization).

If Ollama is unreachable and `OPENAI_API_KEY` is configured (same lookup `audit` already uses -
environment variable first, then a saved credential), Aletheore asks for explicit confirmation
before falling back to OpenAI's `text-embedding-3-small` embeddings instead - this sends real
source code chunks to OpenAI's API, so it's never used silently and never used at all when running
non-interactively (e.g. from the MCP server).

```bash
aletheore scan .
ollama pull hf.co/ggml-org/jina-embeddings-v2-base-code-Q8_0-GGUF
aletheore index .
```

### `aletheore audit [path]`

Runs a scan, then uses a selected reasoning provider to write a full grounded report to
`.aletheore/audit-report.md`, following the per-section instructions in `manual/` (repository
intelligence, git intelligence, architecture, security, AI-usage detection, audience
perspectives, roadmap synthesis) and citing exact evidence fields throughout.

This is a genuinely different kind of operation from everything else in this list: it spawns
a full second reasoning process (up to a 10-minute timeout for CLI-backed providers) to
produce prose, rather than answering a fast, deterministic query. It's meant to be run by
hand, when you actually want a written document — it is not wired into CI or the MCP server,
and shouldn't be: a CI gate needs to be fast and pass/fail on concrete facts, and an agent
already driving an MCP session can reason over the evidence itself without spawning a nested
agent process.

There are twelve distinct `--agent` values:

| Provider family | CLI adapter | API/local adapter |
| --- | --- | --- |
| Anthropic | `claude` | `anthropic` |
| OpenAI | `codex` | `openai` |
| Google | `gemini-cli` | `gemini` |
| Mistral | `mistral-vibe` | `mistral` |
| xAI | `grok-build` | `grok` |
| Provider-agnostic/local | `opencode` | `ollama` |

CLI adapters require that the vendor's own CLI is installed and already authenticated. They
run as local subprocesses in the repository working directory, and Aletheore does not add its
own consent prompt because the vendor CLI owns its own auth, permissions, and network behavior.

API-key adapters (`anthropic`, `openai`, `gemini`, `mistral`, `grok`) show a fresh per-run
consent prompt before the provider call. That prompt names the provider and explains that only
already-computed repository evidence is sent, not raw source code. Declining exits cleanly
after writing evidence. `ollama` is local and key-free, so it does not show that API consent
prompt.

API keys are read from the provider's environment variable first (`ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `MISTRAL_API_KEY`, `XAI_API_KEY`, `GEMINI_API_KEY`). If no environment
variable or saved key exists, Aletheore prompts for a key and asks whether to use it once or
save it to `~/.config/aletheore/credentials.json` with `0600` permissions. Keys are never
printed or included in adapter error messages.

**A note on local model reliability for `ollama`**: treat local-model support as
experimental. The default tag (`llama3.1:8b`) reliably fails this audit's structured,
multi-round tool-calling contract — it will typically fail fast with a clear
"finished without writing required section(s)" error rather than hang (Aletheore fails fast
on this, it doesn't retry forever), but it will fail. This is not specific to that one model:
live-tested against four local models on ordinary consumer hardware (`llama3.1:8b`,
`qwen2.5-coder:14b`, `deepseek-coder-v2:latest`, `gpt-oss:20b`), all four failed this specific
task — one lacked Ollama tool-calling support entirely (`deepseek-coder-v2` returns
`does not support tools`), and three produced tool calls but never reliably completed all nine
sections. `gpt-oss:20b` initially hit the per-request timeout at the default 120s; re-tested
with a 600s timeout it did **not** time out, but took ~10 minutes to arrive at the identical
tool-calling failure the other models hit in under 3 - confirming this is a model-capability
limit, not a timeout tuning problem. Raising the timeout only makes a guaranteed failure
slower, so the default stays conservative. No local model has been confirmed to complete this
audit successfully yet. If you get one working, especially something in the 30B+ range on
capable hardware, that's genuinely useful data — there is currently no `--model` override
flag, so trying a different tag than the built-in `llama3.1:8b` requires editing
`KNOWN_ADAPTERS` in `aletheore/cli.py` if you're running from source. For local-model use
today, the practical recommendation is: use `ollama` for quick/free experimentation with the
understanding that it may fail, and rely on an API-key provider or a CLI-based agent (`claude`,
`codex`, `gemini-cli`, `mistral-vibe`, `grok-build`, `opencode`) for a run you actually need to
complete.

API/local adapters are deliberately bounded: they receive no raw repository files and no
filesystem tools. They can only call `read_evidence_section` against
`.aletheore/air.toon`, write named report sections, and finish the report.

Interactive runs always show a provider-selection menu, even if exactly one provider is
available. Non-interactive runs must pass `--agent NAME` explicitly so automation never
silently chooses a provider.

While the reasoning provider runs, an elapsed-time indicator prints so a multi-minute wait
doesn't look identical to a hang (updates in place on a real terminal; prints once at the
start and once at the end when piped to a log). Providers are instructed to use
`.aletheore/air.toon` (see `scan` above) rather than the JSON copy.

```bash
aletheore audit .
aletheore audit . --agent claude
aletheore audit . --agent codex
aletheore audit . --agent openai
aletheore audit . --agent ollama
```

#### `--managed`: BYOK vs. Aletheore's own key

Everything above is **BYOK** (bring your own key) - `audit` uses whichever provider/CLI *you*
already have configured, and the reasoning cost is yours. `aletheore audit . --managed` is the
alternative: it runs the identical report against Aletheore's own hosted reasoning service
instead, using a token tied to a paid GitHub App installation rather than any of your own
provider credentials.

```bash
aletheore login             # GitHub device-flow auth, saves a managed-audit token
aletheore audit . --managed
aletheore status             # confirm login state and installed version
aletheore logout             # clear the saved token
```

`--managed` reads the token from `ALETHEORE_API_TOKEN` first, then the credential
`aletheore login` saved to `~/.config/aletheore/credentials.json` (`--token` overrides both,
and only has any effect together with `--managed`). `--agent` is BYOK-only and has no effect
with `--managed`, since there's no local/API provider selection to make.

### `aletheore login`

Authenticates with GitHub via device flow (prints a one-time code and a URL to enter it at),
resolves which of your paid GitHub App installations to attach to (prompting if you have more
than one), and saves a managed-audit API token locally. Replaces any previously saved token.

### `aletheore logout`

Clears the locally saved managed-audit token. Safe to run even if not currently logged in.

### `aletheore status`

Prints the installed version, whether a newer one is available on PyPI, and - if a
managed-audit token is saved - who it's logged in as and which plan that installation is on.

### Crash reporting

If the CLI itself crashes with an unhandled exception, it sends a crash report so bugs that only show up
on some operating systems or Python versions can be found. It is on by default.

- **Sent:** the exception details and stack trace, the Aletheore, OS and Python versions, and recent log
  lines. Your home directory is replaced with `~`. Command-line arguments, the machine name, local
  variables and request data are removed first. An exception message can still name a file or path from
  the project you were scanning.
- **Never sent:** usage events, timing, performance traces or repository contents. Nothing is sent unless
  the CLI crashes.
- **Turn it off:** `aletheore config crash-reporting off`, or set `ALETHEORE_CRASH_REPORTING=0` (also
  `false`, `no`, `off`). `aletheore config crash-reporting on` turns it back on, and
  `aletheore config crash-reporting` with no argument, like `aletheore status`, shows the current state.
- You see a one-time notice on first run and a line after any crash that was reported, both on stderr.

### `aletheore tokens audit` / `aletheore tokens profile <name>`

A coding agent re-reads its fixed context (system prompt and tool definitions) on every turn. These commands
show that cost and let you trim the part you do not use. Everything is local; nothing is sent to Aletheore.

- `aletheore tokens audit [--project DIR] [--compare core]` sends one tiny request through your own `claude`
  CLI and prints the tools your agent loads, your `ENABLE_TOOL_SEARCH` setting and the fixed per-turn
  input-token count. `--compare core` sends a second request with the `core` profile's deny list on the
  command line (no file is changed) and prints the count after. Each request costs a fraction of a cent, and
  the command asks before it sends anything (`--yes` skips the question).
- `aletheore tokens profile core|core+web [--project DIR]` shows the exact entries it would add to
  `permissions.deny` in the project's `.claude/settings.json` and applies them only after you type `yes`
  (`--yes` skips the question). `core` keeps Bash, Read, Edit, Write, Grep, Glob, Task and ToolSearch, plus
  every MCP tool, and denies the other built-in tools (scheduling, artifacts, worktrees, notifications, web).
  `core+web` also keeps WebFetch and WebSearch.
- `aletheore tokens profile off` removes exactly the entries it added and nothing else. It never touches your
  user-level settings, and a settings file it cannot parse is left unchanged.

The profile is off until you apply it. A tool the profile denies is unavailable to the agent in that project, so
switch to `core+web` or `off` if your work needs it.

### `aletheore shield`

A local safety net for AI-written changes in a Git repository: a guard that reports on a change,
checkpoints you can restore, and a receipt per agent session. It makes no model call and needs no
account. Shield is built for Claude Code hooks plus a Git pre-commit hook.

```text
aletheore shield [--path DIR] [--yes]
aletheore shield undo [REF] [--path DIR] [--yes]
aletheore shield off [--purge] [--path DIR] [--yes]
aletheore shield check [--no-lookups] [--path DIR]
```

- `aletheore shield` with Shield off prints the plan (every file it will create or edit, with a
  diff, and the network note) and asks you to type `yes`. `--yes` skips the question; without a
  terminal and without `--yes` nothing is changed. If Shield is already on it prints the status:
  the checkpoint count, the latest receipt and the next commands. Setup is applied first; if the
  repository has no evidence yet, a quick local scan follows, and a failed scan only prints a
  warning.
- `--path DIR` selects the repository (default: the current directory). Given to `shield`
  itself, `--path` and `--yes` also apply to `undo`, `off` and `check`.
- `undo [REF]` lists the newest checkpoints and restores the one you pick. `REF` is a full ref, its
  short name (the part after `refs/aletheore/checkpoints/`) or a unique ending. With `--yes` and no
  `REF`, the newest checkpoint is used. Before writing, it shows the files it will write, files it
  leaves in place (not in the checkpoint; never deleted) and files it will not touch because
  restoring them is not safe. It takes a safety checkpoint first and prints the command that undoes the undo.
- `off [--purge]` removes the hooks Shield registered, its block in the Git hook, its block in
  `.git/info/exclude` and its config, and puts back any settings file it edited. Checkpoints and
  receipts are kept; `--purge` also deletes the checkpoint refs, `.aletheore/shield/` and the hook
  state. Scan evidence in `.aletheore/` is never removed. While it exists, a one-line git
  exclude for `.aletheore/` stays; delete `.aletheore/` and run `off` again to drop it. If another
  linked worktree still uses Shield, the shared Git hook block is kept.
- `check [--no-lookups]` runs the guard now over the current changes against `HEAD` (budget
  6 seconds). `--no-lookups` skips registry lookups. It prints one line per finding
  (`warn:` or `info:`, a message and, in brackets, where) followed by a `not checked:` line for
  anything skipped, or `No findings; every check ran.` when both are empty.

**What setup changes.** `.claude/settings.local.json` (four hook entries; merged, never
overwritten; personal, not committed), the Git `pre-commit` hook (a block between
`# >>> aletheore shield >>>` and `# <<< aletheore shield <<<`), a marked block in
`.git/info/exclude` covering `/.aletheore/` and `/.claude/settings.local.json`,
`.aletheore/shield/config.json`, and private records in `<git dir>/aletheore-shield/`. A file Shield
cannot edit safely (invalid JSON, a symlink, read-only, tracked by Git, a hook that is not a shell
script or lives outside the Git directory) is not written; the plan shows the text to add yourself.

**Hook events.** Each runs `python -m aletheore.shield.hooks <event>`, always exits 0 and has a hard
time limit.

| Event | Fired by | What it does | Limit |
| --- | --- | --- | --- |
| `pre-edit` | Claude Code `PreToolUse` (Edit, MultiEdit, Write) | Saves the session's base evidence once and checkpoints the working tree; prunes old checkpoints at most hourly | 2 s |
| `post-edit` | `PostToolUse` (same tools) | Runs the guard without registry lookups; gives the agent only findings that differ from the last ones it was given | 2 s |
| `turn-end` | `Stop` | In a session that edited, runs the guard with registry lookups and shows the user text that differs from the last turn-end text | 8 s |
| `session-end` | `SessionEnd` | Writes the receipt, prunes old checkpoints, drops the session state | 8 s |
| `pre-commit` | Git `pre-commit` | Prints the guard text to stderr, each line prefixed with `aletheore shield:` | 8 s |

Session state is one small JSON file per session in `<git dir>/aletheore-shield/hook-<session>.json`,
outside the working tree. Checkpoints are commits under `refs/aletheore/checkpoints/<worktree>/`,
built with Git plumbing so your index, branches, stash and working tree are not touched. They hold
tracked and untracked files minus ignored files, secret-looking files (such as `.env`), Shield's own
folder and files over the size caps. Receipts are Markdown files in `.aletheore/shield/receipts/`.

**What the guard checks.** New dependencies from manifest additions and new imports (PyPI and npm
only; each is checked for existence, age and a near match to a popular package); files that import
the changed files and candidate tests; changed function signatures, stated per file that imports the
module; and new secret, vulnerability, layer-violation and import-cycle findings against the session's
base evidence. Messages give facts and counts, not advice.

**Exit codes.** `shield`, `undo` and `off` exit 0 on success or when nothing needed doing, and 1 when
the path is not a Git repository, setup is blocked or fails (it is rolled back), or nothing could
be asked because there is no terminal and no `--yes`. `check` exits 0 even when it finds
problems; it exits 1 only when the path is not a Git repository.

**Network.** Only with registry lookups on, and only at turn end, commit and `check`: the package name
goes to `pypi.org` or `registry.npmjs.org`. Versions, paths and source are not sent. Set
`ALETHEORE_SHIELD_OFFLINE=1` (also `true` or `yes`) or `"registry_lookups": false` in
`.aletheore/shield/config.json` to turn lookups off; the guard then lists the names as not checked.
The setup screen prints the active network plan.

**Limits and known gaps.**

- A hook never blocks a commit or an agent turn, and the guard never stops the agent.
- Evidence is read from `.aletheore/air.json` as it is on disk. If it is older than a changed
  file, the dependents, signature and new-risk checks are skipped and reported as not checked; a
  background refresh is started (at most once a minute) instead of a re-scan inside the hook.
- Registry lookups do not run per edit, only at turn end and commit; at most a fixed number of
  names are looked up per run, and the rest are listed as not checked.
- A near match to a popular package is an `info` line by itself; it is a `warn` only when the registry
  also shows the name does not exist or is recent.
- Only PyPI and npm are checked; other ecosystems are reported as not checked.
- Dependency and signature findings are import-level (which files import a module), not call-level.
  Candidate tests are suggestions and are not run.
- Undo never deletes a file. It restores bytes the way `git checkout` would, following your
  line-ending and filter settings, so a restored file can differ in line endings from the checkpoint
  blob. It refuses paths under symlinks, inside nested repositories or outside the repository.
- Checkpoint refs are ordinary Git refs; `git push --mirror` would push them.
- Shield is designed for macOS, Linux and Windows. The CI jobs for all three run the unit tests;
  hooks have not been exercised against a real Claude Code session on Windows.

### `aletheore query <kind> [target]`

Answers one targeted question from an existing `air.json`, without re-scanning or an LLM
call.

```bash
aletheore query imports app/routes.py --path .
aletheore query imported-by app/routes.py --path .
aletheore query symbols app/routes.py --path .
aletheore query symbol-source app/routes.py handle_login --path .
aletheore query branch main --path .
aletheore query ownership --path .
aletheore query secrets app/routes.py --path .        # findings within just that file
aletheore query vulnerabilities --path .
aletheore query licenses --path .
aletheore query endpoints --path .
aletheore query cluster app/routes.py --path .
aletheore query layer-violations --path .
aletheore query dead-code --path .
aletheore query hotspots --path .                     # files with the most git churn/co-change
aletheore query database --path .                     # detected ORMs, connection strings, migrations
aletheore query infrastructure --path .                # detected Docker/CI/IaC config
aletheore query environment-variables --path .
aletheore query evidence-for-endpoint "GET /users/:id" --path .
aletheore query evidence-for-symbol handle_login --path .
aletheore query evidence-for-dependency requests --path .
aletheore query changes --path .              # diff against the previous history snapshot
aletheore query search-codebase "how does auth work?" --path .
aletheore query answer "how does auth work?" --path . --agent ollama
```

`dead-code` and `error-handling` print a whole repo, which is often far more than a question about one folder needs.
They accept `--under <dir>`, `--exclude <name>` and `--no-tests` (same meaning as on `dependents`) and then list only
the matching paths, plus a `scope` block saying how many entries were left out. Counts that are not per path
(`by_error_type`, `unused_dependencies`) stay repo-wide, and `error-handling` still carries the caps applied when the
scan was written (`truncated`). Other kinds reject these flags.

`search-codebase` returns TOON-encoded semantic retrieval results from the local index.
`answer` retrieves code chunks from that same index, gates low-confidence matches, and then
uses the selected provider's simple completion path to answer with citations.

### `aletheore dependents <file> [path]`

Lists every file that imports `<file>`, directly or through a chain of other imports, as one complete,
deduplicated, sorted list. Use it for "what depends on this file?" questions instead of looping over
`query imported-by` by hand.

```bash
aletheore dependents src/click/_compat.py --under src --exclude tests
```

- The target can also be a directory: all files directly in it are treated as one package, which is the natural unit for
  Go (`aletheore dependents internal/js --packages`). `--packages` lists one line per importing directory instead of one
  per file.
- `--no-tests` leaves out test files (`tests/` style directories and test-shaped names such as `test_x.py`,
  `x_test.go`, `x.test.ts`, `x.spec.js`, `XTest.java`).
- `--under <dir>` keeps only files below a directory (repeatable).
- `--exclude <name>` drops files under a directory with that exact name, or with that exact file name (repeatable).
  It matches whole path parts, so `--exclude locale` does not drop `middleware/locale.py`.
- The last paragraph of the output states what the list covers: static imports of the file's language, how many
  modules were indexed, how many files could not be parsed, and whether any listed file changed after the scan.
  Imports built at run time (`importlib`, `__import__`) are not tracked.

### `aletheore cycles [path]`

Lists every circular import dependency in the repo, with an example chain for each group of files that
import each other in a circle.

```bash
aletheore cycles                                 # every group, repo-wide
aletheore cycles --under src/billing --exclude tests
```

- `--under <dir>` and `--exclude <name>` (both repeatable) scope the answer. Cycles are still found over the
  whole graph, so a cycle that runs through files outside `<dir>` is not missed, but only files inside the scope
  are listed, as one flat list followed by a one-line summary per group.
- `--exclude` matches whole path parts, so `--exclude locale` does not drop `middleware/locale.py`.
- The closing paragraph states the coverage: static imports only, modules considered, and whether any listed file
  changed after the scan. Imports built at run time (`importlib`, `__import__`) are not tracked.

### `aletheore diff <old.json> <new.json>`

Compares two `air.json` files directly — new/resolved secrets, API endpoints, layer
violations, dependency vulnerabilities, architecture deltas. Powers the GitHub Action below.

```bash
aletheore diff old/air.json new/air.json
aletheore diff old/air.json new/air.json --fail-on-new-secrets
aletheore diff old/air.json new/air.json --fail-on-new-vulnerabilities
aletheore diff old/air.json new/air.json --fail-on-new-layer-violations
```

All three `--fail-on-new-*` flags can be combined; the command exits 1 if any of them find
something new.

`--format sarif` renders new secrets, dependency vulnerabilities, and layer violations as a
SARIF 2.1.0 log instead of the default JSON, for tools that ingest SARIF directly (e.g. GitHub
code scanning). Incompatible with `--full`, since SARIF needs the curated diff shape.

```bash
aletheore diff old/air.json new/air.json --format sarif
```

### `aletheore verify <report.md> [--path <repo>]`

Checks a report's `file:line` citations against a repository's evidence - works on any
markdown report, not just one `aletheore audit` produced, since it only reads the report's
text and `<repo>/.aletheore/air.json`. Exits 1 if any citation can't be verified, so it also
works as a CI gate on hand-written or third-party reports.

```bash
aletheore verify audit-report.md --path .
```

### `aletheore healthcheck [path] --base-url <url>`

Runs a GET-only live check of mapped API endpoints against a running app instance. This reads
`repository.api_endpoints` from evidence, substitutes placeholder values for path parameters
such as `<int:id>`, `{id}`, and `:id`, skips non-GET endpoints without calling them, and writes
a rotated result file under `.aletheore/healthchecks/`.

This command depends on live runtime state, so it is deliberately **not** part of deterministic
scan evidence or `aletheore diff`.

```bash
aletheore healthcheck . --base-url http://127.0.0.1:5000
```

### `aletheore mcp [path]`

Starts a stdio MCP server scoped to one repository, so a coding agent can query its structure
directly instead of shelling out via Bash for every lookup. Every tool
result is [TOON](https://toonformat.dev)-encoded rather than plain JSON — the calling agent's
own token budget is what actually pays for reading these results, and evidence's shape (almost
entirely uniform arrays of same-shaped objects) is exactly TOON's best case. Exposes 32 tools in
a read-only posture, 36 by default, and 37 with every effect permitted — see
[Tool permissions](#tool-permissions) for what gates the rest — plus one optional answer tool
when started with `--agent`:

- The 16 query kinds above as tools, each named `aletheore_<kind>` with underscores in place of
  hyphens (`aletheore_imports`, `aletheore_imported_by`, `aletheore_symbols`, `aletheore_branch`,
  `aletheore_ownership`, `aletheore_secrets`, `aletheore_vulnerabilities`, `aletheore_licenses`,
  `aletheore_endpoints`, `aletheore_cluster`, `aletheore_layer_violations`, `aletheore_dead_code`,
  `aletheore_hotspots`, `aletheore_database`, `aletheore_infrastructure`,
  `aletheore_environment_variables`) — each tool's own description states what `target` expects
  (a file path, a branch name, or that the tool takes no target at all).
- `aletheore_changes(full=False)` — what changed between the two most recent scans.
- `aletheore_overview()` — a repo-level summary: languages, frameworks, monorepo structure,
  dependency-graph size, module/cluster counts, and git age/commit cadence/branch count. The
  starting point for "what is this repo?" — call this before anything else on an unfamiliar
  repository.
- `aletheore_list(kind)` — the valid names/identifiers for one evidence collection (`modules`,
  `clusters`, or `branches`), so another tool's exact-match `target` argument can be filled in
  correctly.
- `aletheore_neighborhood(target)` — a module's imports, dependents, and cluster in one call,
  instead of three round-trips.
- `aletheore_search(pattern, regex=False, path_glob=None)` — literal or regex full-text search
  over tracked source files, capped at 200 matches.
- `aletheore_ast_pattern(language, query)` — structural search by shape, not words: a raw
  tree-sitter S-expression query against every file of one language, re-parsed from disk (not
  air.json) since a structural match needs the real parse tree.
- `aletheore_symbol_source(module, symbol, max_lines=120)` — source text for one named function/class,
  with resolved line bounds, returned as a one-line header plus the raw code. A symbol longer than
  `max_lines` is cut (a long class is shown as its head plus a list of its methods with line ranges);
  `max_lines=0` returns all of it.
- `aletheore_locate(query)` — find where the names in `query` are defined and see the code in one call:
  the functions whose bodies contain those names, each as a `path::Name Lstart-end` header plus its
  source. Says so when a file changed after the last scan and its line ranges may be off.
- `aletheore_verify_citations(report_text)` — checks every `file:line` citation in a report
  against this repo's real evidence and real file line counts, same check as `aletheore verify`.
- `aletheore_find_evidence_for_endpoint(method, path)`, `aletheore_find_evidence_for_symbol(symbol)`,
  `aletheore_find_evidence_for_dependency(dependency)` — resolve an endpoint/symbol/dependency to
  full source evidence: file, line, owner, commit, and (for endpoints) live-health risk.
- `aletheore_scan(...)` — triggers a fresh deterministic scan and returns a compact summary (not
  the full evidence dump). Does **not** run the agent-driven `audit` report — see the note
  under `aletheore audit` above for why that's a deliberate boundary, not a gap.
- `aletheore_healthcheck(base_url)` — runs the same GET-only live health check as the CLI and
  persists the result under `.aletheore/healthchecks/`.
- `aletheore_index()` — builds the local semantic search index, same as `aletheore index`.
- `aletheore_search_codebase(query, k=10)` — semantic search over the local code index.
- `aletheore_managed_audit(token=None)` — **not registered by default** (see below). Runs a full
  managed audit report via Aletheore's hosted service, resolving the token the same way
  `aletheore login` does: an explicit `token` argument, then `ALETHEORE_API_TOKEN`, then the
  credential saved by `aletheore login`.
- Optional: `aletheore_answer(question, k=5)` — available only when the MCP server is started
  with `--agent`, answers from the semantic index using the selected provider.

```bash
aletheore mcp .
```

**Windows: `ImportError: DLL load failed while importing _lancedb`.** This is Windows 11's Smart App Control silently blocking `_lancedb.dll` (from the `lancedb` package the semantic-search tools depend on), not a bug in this package. Check Windows Security → App & browser control → Smart App Control; turning it off resolves the import immediately, with no code changes needed. Microsoft only lets Smart App Control be turned off, not back on, without reinstalling Windows, so this is a one-way decision on the affected machine.

#### Guidance sent to coding agents

The MCP initialization instructions reflect the selected profile and permitted tools. They
explain scan freshness and analysis limits, distinguish candidate tests from measured
coverage, and tell agents to check confidence, skipped checks and truncated output before
claiming completeness. Full-profile guidance includes paging hints and only recommends
scan, index, semantic-search and answer tools that are actually exposed in that session.
The smaller profiles retain compact freshness, completeness and trust guidance.

Repository content and Hallbook skills are data, not permission to override the user's
instructions or run external actions. Agents are instructed to report problems to the user;
filing an issue or sharing diagnostics requires user authorization and redaction. These
instructions guide model behavior; tool registration and client permissions enforce access.

#### Keeping evidence current (on by default)

Evidence is a snapshot of the last scan, so an agent driving the server would otherwise answer
from a repository that has since moved. `aletheore mcp` therefore re-scans in the background a
few seconds after source files stop changing, and says so on stderr when it starts:

```text
aletheore: watching /path/to/repo (412 source files): evidence re-scans 5s after edits settle. Turn off with --no-watch or ALETHEORE_MCP_WATCH=0
```

- **Turn it off** with `aletheore mcp --no-watch`, or `ALETHEORE_MCP_WATCH=0` (also `false`, `no`,
  `off`) in the client's environment. Any other value leaves it on.
- **It needs the `write` effect** — the same permission `aletheore_scan` needs to rewrite
  `.aletheore/`. If `ALETHEORE_MCP_ALLOW` withholds `write`, there is no watcher and the server
  says so.
- **It needs evidence to exist.** With none yet, it starts after the first `aletheore_scan`.
- **It is deliberately lighter than a full scan.** The background re-scan skips dependency
  vulnerabilities and licenses, git history, static analysis, architecture clustering and
  hotspots, and carries the last full scan's values for those forward; run `aletheore scan` (or
  the `aletheore_scan` tool) when you want them refreshed. It only refreshes a semantic index that
  already exists, and never builds a first one.
- **It stays out of the way.** One re-scan at a time (edits made during one become the next), a
  5-second quiet period, and it declines to start above 5,000 source files rather than keep a core
  busy — the message says how many it found. Only one watcher runs per repository even when several
  agent sessions have the server open; the others report that another process is watching.
  On a filesystem that cannot take file locks (some network shares, container bind mounts, WSL's
  Windows drives) it cannot tell, so it watches anyway: evidence writes are atomic, so the worst
  case is a duplicate re-scan, never a corrupt file.
- Everything it prints goes to stderr; stdout stays the MCP transport.

`aletheore watch` does the same thing in the foreground (Ctrl-C to stop), with a 2-second quiet
period and no file limit.

#### Tool permissions

Most of these tools only read `.aletheore/air.json`. A few do more, so each one carries standard
MCP [tool annotations](https://modelcontextprotocol.io/specification/server/tools) —
`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint` — that any MCP client can
read and display.

Annotations are **hints**, though; the MCP spec is explicit that clients should not make tool-use
decisions based on them. So the actual boundary is `ALETHEORE_MCP_ALLOW`, which controls which
effect classes are permitted. A tool whose effects aren't permitted is **never registered** — it
does not appear in the tool list and cannot be called.

| Effect | Meaning | Default |
| --- | --- | --- |
| *(read)* | Reads evidence. Always permitted; not gateable. | on |
| `write` | Writes files under `.aletheore/`. | on |
| `network` | Outbound requests — OSV.dev, package registries, health probes, embeddings. | on |
| `external` | Transmits this repository's evidence to a third-party service. | **off** |

`external` is the one class off by default. Scanning and indexing are what the tool is *for*, and
their effects stay on this machine; the genuinely surprising action is your repository's evidence
leaving it. That could previously happen with no consent step at all, because
`aletheore_managed_audit` silently resolves a token from the OS keychain — anyone who had once run
`aletheore login` had an agent that could upload without being asked.

```bash
# Default: everything except evidence upload.
aletheore mcp .

# Allow the managed-audit tool to upload evidence.
ALETHEORE_MCP_ALLOW=write,network,external aletheore mcp .

# Read-only server: evidence queries only, no scans, writes, or network.
ALETHEORE_MCP_ALLOW=read aletheore mcp .
```

An explicit value **replaces** the default rather than adding to it, so `read` really does mean
read-only. An unrecognized effect name is a startup error rather than a silent no-op. When tools
are withheld, the server prints one line to stderr naming them and the variable to set.

`aletheore_answer` reaches an LLM provider but is not gated, because it is registered only when
you pass `aletheore mcp --agent` — that flag is already the consent step.

`aletheore_search_codebase` (and the query side of `aletheore_answer`) always embeds through a
local Ollama instance and can fall back to OpenAI, which is why it might look like it belongs
under `external`. It doesn't: that fallback requires an interactive confirmation and is refused
outright when stdin isn't a TTY, and an MCP server is always spawned with piped stdio. From MCP
this tool reaches Ollama on localhost or fails — it cannot send code to OpenAI. If Ollama isn't
running, expect an "embedding provider unavailable" error rather than a silent upload.

`aletheore_index` embeds the same way by default, but it has a second path `aletheore_search_codebase`
doesn't: when `external` is permitted, it prefers Aletheore's own hosted embedding endpoint over
the local instance, uploading this repository's code chunks there instead of embedding on your
machine. With `external` withheld (the default posture), `aletheore_index` never reaches that
hosted endpoint either — it falls through to the local Ollama/OpenAI-fallback path above, for the
same reason `aletheore_managed_audit` doesn't upload evidence without being asked. Set
`ALETHEORE_MCP_ALLOW=write,network,external` to opt in.

`aletheore mcp --profile nav` starts a smaller server with only `aletheore_locate`,
`aletheore_symbol_source` and `aletheore_search`: about a tenth of the tool-definition text of the
full server, so a client can load it up front instead of behind a tool-search step. Whether it
saves tokens depends on the task: where the target is already named in the request, a plain grep
is just as cheap, and the tools mainly help when you do not yet know where to look.

Choosing a profile. Every tool definition is sent to the agent on every turn, so the tool set is a
recurring cost:

| `--profile` | Tools | Definitions | Use it for |
|---|---|---|---|
| `full` (default) | every tool, with background watching | about 36,800 characters | the whole surface: scan and index, skills, AST patterns, ownership, the lot |
| `lean` | the router plus eight direct code tools | about 8,900 characters | day-to-day questions about the code (imports, blast radius, where something is defined) |
| `router` | one tool, `aletheore_ask`, which picks the query | about 1,400 characters | the smallest context; plain questions, answered by the matching exact query |
| `nav` | `aletheore_locate`, `aletheore_symbol_source`, `aletheore_search` | about 2,400 characters | finding code when you do not yet know where it is |

`lean`, `router` and `nav` do not watch for file changes; re-run the scan yourself after edits.

### `aletheore mcp-install [path]`

Writes the MCP server registration into your coding tool's own config, so it launches
`aletheore mcp` for you automatically instead of you running it by hand. By default writes for
every scriptable target: Claude Code (`.mcp.json`), Cursor (`.cursor/mcp.json`), VS Code
(`.vscode/mcp.json`), Kiro (`.kiro/settings/mcp.json`), Opencode (`opencode.json`), and OpenAI
Codex CLI (`.codex/config.toml`). Use `--target` to restrict to one or more
(e.g. `--target cursor --target vscode`). Safe to re-run - it merges into any existing config
file rather than overwriting it, so other MCP servers you've already configured are left alone.

```bash
aletheore mcp-install .
```

**PyCharm / other JetBrains IDEs:** not auto-configured. There's no single stable, publicly
documented file format to safely script against - the supported path is Settings | Tools | AI
Assistant | Model Context Protocol, using "Import a Claude MCP config" against the `.mcp.json`
this command already wrote.

**vim, Neovim, Emacs, and other terminal editors:** none of these have a native MCP client -
support depends entirely on whichever AI plugin you've installed (e.g. `avante.nvim`,
`codecompanion.nvim`), each with its own config. Point that plugin at `aletheore mcp <path>`.

**OpenAI Codex CLI:** writes `.codex/config.toml`, but Codex only reads project-scoped MCP
config for projects it already trusts - check Codex's own trust prompt if the tools don't
appear. Writing this file reformats it; hand-written comments in an existing `config.toml` are
not preserved.

### `aletheore dashboard [path]`

A live local web UI (Starlette + SSE, opens in your browser): repo overview, git activity,
trend charts for module/secrets/vulnerability counts across scan history, an interactive
dependency graph, a separate community-aware "clusters" graph with zoom/pan, and the list of
MCP tools available for the repo.

```bash
aletheore dashboard . --port 8420
```

## GitHub Action

`../action.yml` ("Aletheore" on the Marketplace) is a composite Action that scans a PR's
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

A secret accepted via `.aletheore.json`'s `accepted_secrets` (see Configuration above) is
labeled, not omitted, in the comment and Step Summary, and is excluded from inline annotations
and every `--fail-on-new-*` gate.

It only ever calls `aletheore scan` and `aletheore diff`, matching the reasoning above: CI needs
something fast and deterministic, not a full agent-driven audit.

```yaml
- uses: Aletheore/Aletheore@v0.7.2    # pin to a tagged release, not @master
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
`.aletheore/history/` (last 20 kept). `aletheore query changes` / `aletheore_changes` diff the
two most recent snapshots, and the dashboard's trend charts read the full history.
