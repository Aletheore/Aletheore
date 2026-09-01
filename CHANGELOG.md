# Changelog

Notable changes to Aletheore, by release. The working code lives in `src/` — see
[`src/README.md`](src/README.md) for the full command reference.

## 0.9.10 — 2026-08-31

- **Added Kotlin and Swift as fully supported scanner languages** (13
  languages total, up from 11). `.kt`/`.kts` and Swift source now parse
  into the same dependency graph, endpoint map, license scan, and
  vulnerability scan as every other language.
- **Fixed six real dead-code false-positive classes**, found by
  re-validating the new languages against real repositories rather than
  trusting the initial PRs' own tests: JVM test files and Android
  manifest/Hilt-Dagger DI wiring weren't recognized as reachable entry
  points; Swift files in the same build target implicitly see each
  other with no import, and a `Package.swift` string-interpolation
  pattern was silently truncating manifest parsing and merging distinct
  targets into one; Kotlin top-level functions and top-level `val`/`var`
  declarations were never resolved as import targets, only
  classes/interfaces/objects; Kotlin files in the same package
  implicitly see each other's declarations with no import, same as
  Java.
- **Capped dependency-license fetches to a real wall-clock timeout**, so
  one slow registry lookup can no longer stall an entire scan.
- **Deterministic symbol attribution for secrets findings** — CLI
  secrets findings now cite the exact enclosing symbol via the same
  evidence-resolution path used elsewhere, instead of leaving
  attribution to best-effort heuristics.

## 0.9.9 — 2026-08-29

- **Strengthened the CLI's free-tier install nudge, and added a support
  contact.** `scan`/`audit`'s prompt to install the GitHub App for free
  PR reviews used to lead with a hedge ("globally rate-limited and
  subject to availability") right at the call to action, and was styled
  fully dim - easy to skim past at the exact moment a real run had just
  demonstrated value. Reframed to lead with the value prop and a bold
  install link; the honest rate-limit disclosure is kept, just moved to
  a smaller trailing line rather than dropped. Also added a
  `support@aletheore.com` line to the banner shown on every bare
  `aletheore` invocation - previously the only in-CLI pointer for a bug
  report or suggestion was the GitHub repo link.

## 0.9.8 — 2026-08-28

- **Bounded the Java/C# scanner pre-pass's peak memory** (audit finding
  15). `java_pre_parsed`/`csharp_pre_parsed` used to hold every `.java`/
  `.cs` file's parsed tree-sitter `Tree` simultaneously once the source-
  root pre-pass finished, before the main loop had consumed any of them -
  trees run roughly 37x their source size, and the hosted scan-worker
  containers are capped at 1GB, so a large enough Java/C# repo could
  OOM-crash the whole scan right there, before any partial result
  exists to fall back to. Removed the cache entirely instead of shrinking
  its retention window: each file's tree now falls out of scope at the
  end of its own pre-pass iteration, and the main loop re-parses each
  file from scratch. Trades doubled tree-sitter parse CPU for never
  holding more than about one file's tree in memory at a time - measured
  directly on a 600-file synthetic Java repo: peak boundary memory
  dropped from ~406MB to ~3.3MB.
- **Fixed 8 real scanner import/endpoint-resolution bugs**, each
  independently reproduced against the real tree-sitter grammar before
  fixing: Rust `pub use` re-exports extracted the literal word `"pub"`
  instead of the real path; Rust nested `use` group items (`use
  std::{fmt, io::{self, Write}}`) below the first brace level were
  dropped; PHP grouped `use Foo\Bar\{ClassA, ClassB}` statements were
  skipped entirely; PHP `use Foo\Bar as Baz` aliases produced a phantom
  duplicate entry; Java `import static a.b.C.*` misparsed as a directory
  import; TypeScript `import foo = require('./foo')` (import-equals)
  silently produced zero imports; Django's `urlpatterns += [...]` routes
  were never extracted (only plain `=` assignment was); and Python/JS/TS
  files that imported themselves could defeat dead-code detection's
  unreachable-file check, unlike the other 7 language branches which
  already guarded against it.
- **Fixed the endpoint cache reusing a stale composed path across an
  incremental scan** when only a *different* file's `include_router(...,
  prefix=...)` call changed, not the router-defining file itself - a
  common FastAPI pattern (router defined in one file, mounted with a
  prefix in another) that silently corrupted endpoint evidence on every
  subsequent push/PR scan of the affected repo until the router file
  changed for an unrelated reason.
- **Fixed `OpenAICompatibleAdapter.invoke()`** (the tool-calling agent
  loop `aletheore audit`'s reasoning phase uses) never passing
  `extra_body` to the API, unlike `simple_completion()` - `gpt-5.6-luna`
  rejects function tools unless `reasoning_effort` is explicitly
  `"none"`, so any audit run routed to Luna crashed on its very first
  LLM call.
- **`aletheore_search`'s MCP result is now bounded by total character
  size, not just match count.** `_SEARCH_MATCH_CAP` bounded how many
  matches came back, but nothing bounded their combined size - 200
  matches of long lines (a minified bundle, a generated file, a single
  huge JSON line) could produce a result an MCP client rejects for
  exceeding its own size limit. Two independent guards: a per-line
  truncation and a total character budget that stops the search early
  and flags `truncated: true`, reusing the existing truncation signal.
- **Sharpened the MCP server's vocabulary guidance and documented two
  tools' actual parameter shapes**, both verified with real A/B subagent
  runs. `aletheore_search` (literal/regex) needed the same explicit
  sequencing guidance the semantic tools already had - a paraphrased
  query matches nothing at all there, not just "scores lower."
  `aletheore_symbol_source` and `aletheore_find_evidence_for_endpoint`'s
  docstrings never stated they take two separate arguments, not a single
  combined string - agents were burning 3-4 calls guessing the shape.
  Verified: a task that previously took 15-23 tool calls with 3
  parameter errors dropped to 13 calls with zero.

## 0.9.7 — 2026-08-28

- **The Anthropic adapter now retries transient errors** (auth hiccups,
  rate limits, connection drops, timeouts, 5xx) up to 3x with backoff,
  same as the OpenAI-compatible adapter already did - previously a
  transient error killed the whole `aletheore audit` run instantly instead
  of quietly recovering. Found by a second Claude session auditing the
  adapters for the same sibling-parity gap already found and fixed
  elsewhere this project. CLI-only (`aletheore audit` with an Anthropic
  key); no hosted-service exposure.
- **Import edges now carry an optional confidence tag** when their
  resolution wasn't a single deterministic outcome - `"inferred"` for a
  source-root/namespace-prefix/PSR-4-prefix tiebreak among genuinely
  multiple real candidates (Python, Java, PHP), `"ambiguous"` for a C#
  type-reference edge kept despite more than one file declaring that type
  name, which previously was **dropped silently** instead. Adapted from
  researching a competitor's own graph schema, then adjusted for
  Aletheore's token-cost constraints: omitted entirely for the common
  exact-resolution case and for the six languages whose resolvers are
  never ambiguous at all (js/ts, go, rust, ruby, c/cpp), so this costs
  nothing in evidence-packet size for the large majority of edges.
  Benchmarked against real, pinned per-language corpora - on the real
  AutoMapper (C#) corpus, 738 type-reference edges that used to vanish
  silently are now kept and honestly flagged. An ambiguous edge is
  excluded from the static wiki diagrams (a diagram reads as fact, a
  stronger claim than an uncertain edge should make) but shown dimmed
  and dashed in the CLI's own interactive dependency graph, matching how
  the competitor's own visualization handles lower-confidence edges.
  Also fixed a real correctness bug found while wiring this up: Java's
  multi-source-root tiebreak picked whichever root came first in raw
  filesystem walk order (not stable across runs/platforms) instead of a
  sorted, deterministic order like Python's roots already used.
  AIR schema bumped 0.4.0 → 0.5.0 (see `docs/AIR-SCHEMA.md`).
- **The MCP server now sends real getting-started guidance in the
  connection handshake itself** (`instructions`, not a resource an agent
  has to separately fetch) - covers the scan → index → search/answer
  ordering, that scan/index report live progress rather than hanging
  silently, and a benchmark-grounded note that phrasing a semantic
  question in the codebase's own vocabulary measurably beats a
  paraphrase (several corpora scored under 35% top-1 accuracy on
  vocabulary-avoiding phrasing in Aletheore's own published benchmark,
  recovering 20-47 points in the project's own terms).

## 0.9.6 — 2026-08-27

- **Parallel-parse worker count is now capped to the real available CPU
  quota, not raw `os.cpu_count()`.** In a CPU-limited container (CI runner,
  Docker `--cpus`, a Kubernetes pod), `os.cpu_count()` reports the host's
  total core count, not what the container is actually allotted, so the
  0.9.5 parallel-parse feature could over-spawn workers and thrash rather
  than speed anything up. `_available_parallelism()` now takes the minimum
  of `os.cpu_count()`, a cgroup v1/v2 CPU quota read, and
  `os.sched_getaffinity(0)` where available, overridable via
  `ALETHEORE_PARALLEL_PARSE_JOBS`. Verified against real Docker containers
  across quota/affinity combinations, and against a real CI runner failure
  this surfaced (a 4-affinity-core runner reporting 8 via `os.cpu_count()`).
- **The hosted scan-worker can now opt out of parallel parsing entirely**
  via `ALETHEORE_DISABLE_PARALLEL_PARSE`, set automatically on Aletheore's
  own memory-constrained hosted infrastructure without changing the default
  for local CLI users.
- **`aletheore index`'s local embedding setup no longer dead-ends** when
  Ollama is running but the embedding model isn't pulled yet. It now
  auto-pulls the model and shows real setup steps instead of a bare
  connection error.
- **FastAPI endpoint mapping no longer misses `include_router` calls that
  reference a router by module attribute** (`include_router(users.router,
  prefix="/users")`), only bare identifiers before. Confirmed this
  previously produced a real, reachable endpoint's path with its mount
  prefix silently dropped.
- **`git_intel`'s incrementally-synced `recent_commits` ordering was
  inverted** for every caller that feeds commits in real `git log` order
  (newest first): `fold()` iterated forward while building the list with
  `insert(0, ...)`, so the truncation after a busy file's 10-commit cap
  kept the oldest commits and dropped the genuinely recent ones. Anything
  reading `recent_commits[0]` as "the latest commit" (hosted health-check
  correlation, likely-owner inference) was pointing at stale data on
  high-churn files. Fixed by reversing the iteration order; four affected
  test fixtures (three built oldest-first, the mirror image of real git log
  output, which had been masking the bug) corrected to match reality.

## 0.9.5 — 2026-08-27

- **`aletheore scan` is up to 4.5x faster on large repos** — real, measured,
  not projected. Two separate fixes, discovered by profiling a real ~4-minute
  scan of ERPNext (~1M LOC) rather than guessing where the time went:
  - **Parsing is now parallelized** (`ProcessPoolExecutor`, one process per
    core) — `build_module_graph`'s tree-sitter parsing held the GIL under
    threading, so this needed real multiprocessing, with each worker
    returning plain dicts/lists since `Tree`/`Node` objects aren't picklable.
    Measured on ERPNext: parsing itself went from 10.47s to 7.18s (~30%
    faster) — real, but a small piece of the total.
  - **Dead-code detection's dotted-string reference check was the actual
    bottleneck** — 77% of total scan wall-clock, invisible until profiled.
    The old check compiled a fresh regex per unreachable-module candidate
    and scanned every other file's full source for it —
    O(candidates × files × avg file length). Replaced with a single-pass
    dotted-string token index (O(total source size) to build, O(1) per
    candidate lookup after) — same matching semantics, verified via parity
    tests against a deliberately naive reimplementation of the original
    algorithm, plus exact set-equality on real ERPNext output (not just
    matching counts).
  - Combined, real end-to-end effect on the same pinned ERPNext checkout:
    **236.02s → 52.41s total wall-clock scan time**, confirmed by actually
    running it before and after, not estimated.
  - A pre-release audit of this same rewrite caught and fixed a real
    correctness regression before it shipped: the new index treated the
    full captured quoted-string token as always boundary-valid, but the
    original per-candidate regex only accepted a `.` or a closing quote as
    the terminating boundary — a quoted string like `"pkg.mod completed
    successfully"` wrongly registered `pkg.mod` as referenced, which could
    silently "rescue" a genuinely dead module from being reported. Fixed
    (only the closing-quote case counts now); parity tests extended to
    cover this exact shape.
- **Secret scanner missed the single most common hardcoded-credential
  shape: a quoted key in JSON/YAML/dict-literal config** —
  `{"API_KEY": "sk-..."}`, `{'password': '...'}`, a docker-compose
  `environment:` block, a `terraform.tfvars` value. The keyword's own
  closing quote sat between it and the `:`/`=` separator, which the
  pattern's boundary classes couldn't skip over — completely invisible,
  not a partial miss. Same audit that caught the dead-code regression
  above, found by systematically checking other boundary-condition regexes
  in the codebase for the same class of gap. Fixed: left-boundary class now
  includes quote characters, an optional quote is consumed after the
  keyword, and `}`/`]` were added to the right-boundary lookahead alongside
  the existing whitespace/end-of-line/`,#;)` set.

## 0.9.4 — 2026-08-26

- **Fixed three real secret-scanner false positives**, all the same root
  shape: a value that should read as an obvious placeholder only got that
  treatment when it also lived at a path containing
  "test"/"example"/"fixture"/"mock" - contradicting the scanner's own
  stated intent that marker words are recognized "independent of where the
  file lives."
  - AWS's own documented example key (`AKIAIOSFODNN7EXAMPLE`) wasn't
    recognized outside a test-ish path - a student README pasting AWS's
    setup-docs example key verbatim would have triggered a false "new
    secret" PR comment.
  - A hand-typed or padded-out fake value built from a repeated unit (e.g.
    `abcdefghij1234567890` doubled) now gets caught by a new
    zlib-compression-ratio check - real credential generators don't
    produce repeated substrings, so a value that compresses well below its
    own length is an unambiguous signal on its own. Threshold picked
    empirically against 1,000 real random secrets.
  - Stripe's own published test key
    (`sk_test_4eC39HqLyjWDarjtT1zdp7dc`, from their docs) is now
    recognized directly via a small, exact-match known-vendor-values set.

## 0.9.3 — 2026-08-26

- **Fixed a real, silent TOON encoding/decoding bug**: certain nested-list
  shapes (a list value sitting alongside non-list siblings in the same
  array) encoded cleanly but then failed to decode - `to_toon()` now
  round-trip-verifies its own output before returning, so this class of
  corruption raises `ToonEncodingError` at write time instead of silently
  writing a broken `.aletheore/air.toon` that only fails later, confusingly,
  when `audit` tries to read it. Every call site (`write_evidence`, the MCP
  server, the managed-audit client, `query`'s TOON output, both coding-agent
  adapters) now degrades cleanly on a TOON failure instead of crashing.
  Also fixes a separate bug where `ToonDecodeError` isn't an `OSError`, so a
  malformed evidence file crashed the coding-agent adapters with a raw
  traceback instead of a clean error. Covered by a new seeded fuzz test
  (3,000 random nested shapes) alongside the targeted regression tests.
- Bumped the `anthropic` dependency floor to `>=0.40,<2.0`.

## 0.9.2 — 2026-08-25

- **`scan` and `audit` now point free-tier users at the GitHub App** after a
  successful run - a single line noting that Aletheore also does free,
  evidence-grounded PR reviews, with the install link. Only shown on
  success, and lives in the top-level command bodies rather than the
  shared scan helper, so it can't repeat on every cycle of `watch`'s
  internal re-scanning.
- **Corrected "during early access" framing on the pricing page.** Free AI
  PR reviews are routed across multiple providers' free tiers rather than
  depending on any single company's quota, so the real constraint is
  rate limits under load, not a fixed expiration date - the copy
  previously implied a countdown that isn't actually there.

## 0.9.1 — 2026-08-24

- **Three endpoint-mapping scoping bugs, all affecting the accuracy of "what's
  reachable" for a security review.** A router variable named inside an
  unrelated function (the idiomatic FastAPI name `router` reused in a
  factory) could silently overwrite the real module-level router's prefix.
  Two different files' routers both named `router` (also idiomatic) could
  cross-contaminate each other's mount prefixes, producing phantom endpoint
  entries. And a router mounted with `include_router(router)` (no explicit
  `prefix=`) had its own unprefixed endpoint dropped entirely whenever that
  same router also had a prefixed mount elsewhere - a real, reachable,
  unauthenticated-by-default endpoint invisible to the map.
- **Secret scanner missed dotted-attribute credential assignments** -
  `self.PASSWORD = ...`, `cfg.API_KEY = ...` - one of the most common
  hardcoded-credential shapes in object-oriented code, invisible because
  `.` wasn't in the scanner's left-boundary character class.
- **Schema mapper silently corrupted on ordinary SQL comments.** An inline
  `-- comment` inside a `CREATE TABLE` column list fused the following real
  column into a bogus one and dropped it with no trace; a stray `(` inside a
  comment could merge two tables into one and drop the second entirely; a
  `;` inside a `/* */` block comment ended a statement early.
- **Three crash bugs fixed**: one invalid UTF-8 byte anywhere in a repo
  aborted the entire scan; a chunk found by both retrievers in search's RRF
  fusion could lose its score entirely and crash `aletheore_answer` with a
  `TypeError`; AIRview's non-scanned-file wiki fallback called a function
  never imported into that module, a guaranteed `NameError` on every use
  that a bare `except` was silently swallowing.
- **Architecture clustering no longer counts test files toward
  subsystems.** A repo whose dependency graph is mostly test files (one
  real corpus was 82% by node count) fragmented into hundreds of
  near-singleton "subsystems" instead of a handful of meaningful ones -
  same fix already applied to retrieval, now shared with clustering too.
- **Local search index now detects an embedder swap even when vector
  dimensions match.** Two different embedding models can both produce
  768-dimension vectors from unrelated vector spaces; an index built under
  one and searched under the other passed the existing dimension check
  silently and returned coherent-looking but wrong rankings, with no error.
- **Three retrieval-quality regressions fixed**: a `.NET`-suffix test-path
  exclusion matched ordinary words ending in "tests" (`Contests`,
  `Protests`); the "in C" language-detection pattern matched inside
  `in C++`/`in C#` too; a Java/C# demotion rule kept demoting
  interface-plus-abstract-class files even though its own stated intent was
  "no concrete class alongside its interface."
- **Fixed an unpinned, marker-qualified dependency (e.g.
  `typing_extensions; python_version < "3.10"`) being silently dropped**
  from CVE scanning, license checking, and unused-dependency detection - a
  regression against an earlier fix's own stated intent.
- **Fixed the module-overview chunk boundary** using the textually-first
  symbol instead of the actually-first-by-line-number one - a class
  declared before a repo's first function had its entire body swallowed
  into the overview chunk, duplicating content already indexed separately.
- **`aletheore audit --no-map-schema` was parsed but never forwarded** to
  either call site, so it was completely inert despite being documented and
  already working on `aletheore scan`.
- **`aletheore login`'s whoami check no longer crashes on a malformed
  response body** (captive portal, misconfigured proxy, CDN error page) -
  it now degrades to "unknown" like every other failure mode instead of
  raising an uncaught `JSONDecodeError`.
- **The audit sponsor panel no longer claims nothing left the machine** on
  a run that actually sent evidence to a third-party API (with consent) or
  used an already-authenticated local adapter - it previously printed
  unconditionally, contradicting the consent prompt shown moments earlier
  in the same run.
- **`aletheore healthcheck` no longer exits 0 with no summary when every
  endpoint is unreachable** - a completely-down target looked identical to
  a healthy one to any script or CI job checking the exit code.
- **Bare `aletheore` invocation now surfaces an available update**,
  matching what `aletheore status` already showed - silent when already up
  to date or when the check fails, so nothing changes for anyone who
  doesn't need to see it.
- **`mcp-install` prints a copyable `claude mcp add` command for Claude
  Code** targets, and no longer prints setup guidance for targets it didn't
  actually configure (PyCharm/Codex notes shown regardless of `--target`).
  `aletheore login` also now tells you when a saved token already exists
  before replacing it, and `query --help`'s "one of the 23 query kinds"
  count is computed dynamically instead of the stale hardcoded number
  (there are 24, and counting).
- **Perf: Java/C# pre-parsed trees no longer stay pinned in memory for the
  whole scan.** Both languages need a whole-repo pre-pass before the main
  loop can infer a source root; the cache holding those parses now releases
  each entry as soon as the main loop consumes it, instead of holding all
  of them until the entire scan (every file, every language) finishes.

## 0.9.0 — 2026-08-21

- **CLI now tells you when the first scan or index will be slower.** A first
  run (no cached evidence yet) takes longer than an incremental one - the CLI
  says so up front instead of leaving you wondering if it's stuck.
- **Fixed dead-code false positives on RQ-style string-dispatched entry points
  and pytest's `conftest.py`.** Code invoked via `queue.enqueue("module.func",
  ...)` or reached only through pytest's filename-based auto-discovery was
  invisible to static import analysis and got flagged as unreachable.
- **`aletheore_ownership` (MCP) can now be scoped to a single file** -
  previously repo-wide only, even though the underlying query already
  supported a per-file target.
- **Fixed a secrets-detection gap**: newer Google AI Studio API keys
  (containing a literal `.`) were silently dropped instead of flagged,
  because the value pattern's character class didn't include it.
- **`aletheore_answer` (MCP) now correctly honors withheld external-
  transmission consent** - it previously ignored the operator's decision on
  this one tool and could send content to a hosted endpoint regardless.
- **Fixed a rare embedding-batch duplication bug**: when a hosted embedding
  call failed on the very first batch of a run, the local fallback could
  double-embed that batch, silently misaligning the search index for an
  unknown subset of chunks.

## 0.8.13 — 2026-08-19

- **License changed from Apache 2.0 to the [PolyForm Noncommercial License
  1.0.0](LICENSE).** Aletheore remains free for personal, noncommercial use -
  individual developers, research, hobby projects, evaluation. Using it for or
  within a company or other organization (including as internal tooling at a
  company you work for) is a commercial use and requires a separate commercial
  license. This is not retroactive: anyone who obtained a copy under the prior
  Apache 2.0 license (every release through 0.8.12, and any clone or fork made
  before this change) keeps their Apache 2.0 rights for that copy. Only new
  releases and new distributions from this point forward are under the new
  terms. See the [Licensing](README.md#licensing) section of the README.
- **Removed the CLI's anonymous usage ping.** Every completed scan used to send a single
  fire-and-forget event (`scan` + a random per-machine ID, respecting `DO_NOT_TRACK`/
  `ALETHEORE_TELEMETRY_DISABLED`) to a hosted endpoint - it carried no repo name, code, or account
  info, but any HTTP request necessarily carries the caller's IP, and the endpoint itself was
  unauthenticated (the CLI has no account to authenticate with), making it the single most exposed
  write path in the hosted service. Removed end to end: nothing is sent, no flag needed. Adoption
  is now tracked from public PyPI download stats instead.

## 0.8.12 — 2026-08-15

- **C# repositories had almost no dependency graph, because C# does not need
  imports.** Measured on `AutoMapper/AutoMapper`: 512 `.cs` files, 11 of them
  (2%) with any recorded dependency, 187 edges, and community detection
  returning **474 clusters for 513 modules** — one per file, which makes the
  generated wiki's subsystem pages meaningless and leaves `rank_files_by_importance`
  with no in-degree signal. This was not a parsing bug: the `using` resolver is
  correct, 507 of 512 files declare a namespace, and all 74 internal `using`
  directives resolve. The cause is that **419 of 512 files contain no `using` at
  all** — a type in the same namespace needs no import, and AutoMapper puts most
  of its files in `namespace AutoMapper`. There was nothing to parse; the
  dependency lives in the body, where a type is named. Edges are now also derived
  from type references: a type declared in exactly one file in the repository and
  named as a whole word in another. Deliberately conservative, because a false
  edge invents a relationship the wiki then explains — ambiguous names contribute
  nothing, names under four characters are ignored, a file's own types are
  excluded, and edges are capped at 40 per file. Result on AutoMapper: **2% → 77%**
  of files with dependencies, 187 → **2,140** edges, 0.36 → **4.18** edges per
  module (flask is 3.80), clusters **474 → 120**, and the top of the importance
  ranking becomes `MapperConfiguration.cs` and `Mapper.cs` — the actual core.
  Scan cost is +1.6s on 513 files. Downstream on the comprehension benchmark:
  subsystems 473 → 119, generation output tokens 2.56M → 1.15M (~55% cheaper),
  and file-page selection improves from 59% to 40% test/spec files. The judged
  comprehension score itself is flat (+0.04, p=0.88) — this ships for the graph,
  the cost and the ranking, not for the score. No other language is affected:
  the change is inside the C# branch of the extractor.

## 0.8.11 — 2026-08-13

- **A question naming a language was answered in a different one.** In a polyglot
  repository the same concept is implemented once per language — `apache/thrift` defines
  `TBinaryProtocol` in C++, Java, Python, Ruby, PHP, Go and C# — so "where is
  TBinaryProtocol implemented in the C++ library" has one correct answer and six
  near-identical wrong ones. `search_index` already accepted a `language` pre-filter that
  resolves this, but nothing ever populated it, so the language named in the question
  competed only as ordinary text. Measured on thrift: five of six cross-language failures
  returned a different language's file entirely, C++ missing all three of its questions.
  The language named in a query is now detected and passed to that filter — cross-language
  top-3 60.0% → 73.3% and top-5 60.0% → **93.3%**, general-regime top-5 40.0% → 53.3%.
  Detection is deliberately conservative, because a wrong pre-filter removes the correct
  answer from the candidate pool rather than merely ranking it lower: an unambiguous name
  (`golang`, `typescript`, `c++`) matches alone, while a name that is also ordinary English
  or a prefix of another language (`go`, `c`, `java` inside `javascript`) needs a cue such
  as "library" or "in Go", and a query naming two languages is declined. Across the 356
  single-language benchmark questions it fires on two, both in `pallets/flask` naming
  Python, and flask's results are byte-identical to three decimal places of MRR.

## 0.8.10 — 2026-08-13

- **A file mixing an interface with its own concrete implementation was demoted wholesale
  on the strength of the interface alone.** `_is_declaration_only_file` flagged an entire
  Java or C# file as pure contract if it contained an `interface` line anywhere, with no
  check for whether real implementation sat alongside it — AutoMapper's `Mapper.cs` and
  `Configuration/MapperConfiguration.cs` each pair a small interface with the actual
  concrete class, and gson's `internal/bind/TypeAdapters.java` trips the same rule on one
  interface nested 900 lines deep inside an otherwise fully-implemented registry class. Now
  a file is declaration-only only if it has no concrete class alongside the interface, and,
  separately, an embedded interface's own chunk carries the demotion on its own terms even
  in a file the file-level check no longer flags — the two AutoMapper files above still
  correctly demote their one interface-shaped chunk apiece. Measured on all 12 benchmark
  corpora, both regimes, master ef3b137, re-scanned and re-indexed from scratch: 10 of 12
  are byte-identical, both regimes — no PHP, Go, Rust, Python, Ruby, TypeScript, JavaScript,
  C or C++ side effects. AutoMapper top-3 gains 6.7 points (13.3% → 20.0%) with top-5 fully
  recovered to baseline (26.7% → 33.3%) and nothing else moved, while gson top-3 gives back
  the same 6.7 points (73.3% → 66.7%) it had gained from the same underlying misclassification
  bug — not a defect in this fix: `TypeAdapters.java` is a genuine registry of real
  `TypeAdapter` implementations, not a misclassified interface, and now legitimately competes
  with `TypeAdapter.java` on lexical/topical grounds the same way Slim's PHP siblings already
  do. That's the open follow-up — a separate, already-scoped near-duplicate-crowding problem
  with its own baseline, not a next step on this branch.

## 0.8.9 — 2026-08-13

- **.NET test projects were being indexed as implementation.** `_is_test_path` matched only
  the exact lowercase segments `tests`, `test`, `spec`, `__tests__` and `testing`, so .NET's
  universal conventions — `src/UnitTests/`, `AutoMapper.DI.Tests/`, `IntegrationTests/` —
  were never excluded, and neither was any Java or C# project following the same naming.
  Measured on `AutoMapper/AutoMapper`: every one of 15 location questions returned
  `src/UnitTests/` files ahead of the implementation, for **0.0% top-1**. Matching is now
  case-insensitive and also covers a segment ending in `tests` or `.test`, which lifts
  AutoMapper to 6.7% top-1 and 33.3% top-5 with no change to any other corpus. Deliberately
  matched on the plural: a `test` suffix would swallow ordinary words like `latest`.

## 0.8.8 — 2026-08-13

- **Java visibility ignored Java's own access modifiers.** `is_public` was computed as
  `not _is_nested_in_function(node)` — a fair proxy for Python, which has no access
  modifiers, but simply wrong for Java, which states visibility in a `modifiers` node.
  `docs_reference.py` filters the generated API reference on that flag, so every `private`
  and `protected` Java method was being published as public API. Now read from the
  modifiers, with the absent-modifier case handled correctly: a member of an interface or
  annotation type carries no `modifiers` node at all and is implicitly public by Java's
  rules, so treating "no `public` keyword" as private would have hidden `google/gson`'s
  `TypeAdapterFactory.create` — a worse error than the one being fixed. Measured on
  `google/gson`: 69% of extracted symbols are public, where previously 100% were reported
  as such. Retrieval is unchanged on all eight benchmark corpora — this fixes generated
  documentation, not search.

## 0.8.7 — 2026-08-13

- **A FastAPI router mounted at more than one prefix silently lost one of its mount points.**
  `include_router(router, prefix="/api")` in one place and `include_router(router,
  prefix="/admin")` in another are both real, independently reachable mount points for every
  route on that router — but `_extract_flask_fastapi_routes` chained the two prefixes onto a
  single path instead of emitting one endpoint per mount, producing a single wrong compound
  path (`/api/admin/...`) and dropping the other mount's endpoint entirely. Each mount prefix
  now composes independently with the router's own constructor prefix into its own endpoint.
  Caught by Aletheore's own Flash review, running on `gpt-5.6-luna`, on the PR that introduced
  the surrounding prefix-composition logic (#230) — verified against the real code before
  fixing, not taken on faith.

## 0.8.6 — 2026-08-13

- **Documentation, demos and benchmarks competed with the library for answer slots.** Asked
  where something is implemented, retrieval returned the docs site that describes it or the
  benchmark that times it. Measured across eight corpora: `colinhacks/zod` spent 28% of its
  top-5 slots outside `packages/zod` and `google/gson` 21% outside `gson/src/main`
  (`proto/`, `metrics/`, `extras/`), against 0-7% for single-module repositories. Files under
  a documentation, example, demo or benchmark directory are now demoted — a rank penalty, not
  an exclusion, so an `examples/` directory is still reachable when it is the only match, the
  same treatment interfaces already get. `google/gson` top-1 33.3% → 40.0%, top-5 66.7% →
  80.0%; `pallets/flask` top-1 68.8% → 71.9%. No corpus regressed on any metric; across all
  137 questions top-1 44.5% → 46.0% and top-5 73.7% → 75.2%.

- **Dependency, secret and endpoint scanning missed real findings** (#230, released here — it
  carried no changelog entry of its own). `_parse_pep508_dependency` silently dropped any
  dependency using a compound PEP 440 range (`>=X,<Y`), the `~=` operator, or no version at
  all — on this repository's own `pyproject.toml`, 15 of 17 runtime dependencies were
  invisible to CVE scanning, licence checking and unused-dependency detection alike, since
  all three share that parser. `_extract_javascript` matched only ES `import`, so CommonJS
  `require()`, re-export barrels and dynamic `import()` were invisible to the dependency
  graph, producing false dead-code positives. `generic_credential_assignment` required a
  quoted value, missing unquoted `.env`, docker-compose, shell-export and YAML assignments,
  and scanned each line with `search()` rather than `finditer()`, so a second match on the
  same line was dropped; ASIA session tokens and `github_pat_` fine-grained PATs are now
  covered. `_extract_flask_fastapi_routes` never composed `APIRouter(prefix=...)` or
  `include_router(..., prefix=...)` into the extracted path, so FastAPI's standard
  multi-file layout produced systematically prefix-less routes with no signal anything was
  missing.

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
