# Parallel scan parsing (ProcessPoolExecutor) — design

## Why

`aletheore scan` on a large repo (~1M LOC, e.g. ERPNext) takes ~4 minutes; a real head-to-head benchmark against Graphify's `graphify extract . --code-only` (~1 minute on the same corpus, 10 parallel worker processes) showed this as a genuine, honest gap. Root cause, confirmed by direct benchmark on 2026-07-27 (see memory `project_cli_parallel_parse_deferred.md`) and re-verified against current source on 2026-08-27: `build_module_graph`'s file-parsing walk (`src/aletheore/scanner/graph.py`) is single-threaded, and a `ThreadPoolExecutor` cannot help — tree-sitter's Python binding does not release the GIL during `Parser.parse()` (8 parses across 8 threads measured at the same wall time as 8 sequential parses).

This was previously deliberately deferred pending explicit user go-ahead, given the complexity: `tree_sitter.Tree`/`Node` objects are not picklable (re-confirmed directly: `pickle.dumps(tree)` raises `TypeError: cannot pickle 'tree_sitter.Tree' object`), so real parallelism requires `ProcessPoolExecutor`, where each worker parses and extracts independently and returns only plain dicts/lists. Go-ahead given 2026-08-27, scoped explicitly to the **local CLI user's experience** — optimizing the hosted scan-worker's own resource constraints is an explicit non-goal for this pass (see Risks).

## Current shape (verified against live source, 2026-08-27)

`build_module_graph` (`src/aletheore/scanner/graph.py`, ~line 2190) has three phases:

1. **Java pre-pass**: walks every `.java` file once, parses it, extracts its package declaration, and infers `java_source_roots`. Stores each file's `(source, Tree)` in `java_pre_parsed` so the main loop doesn't re-parse it.
2. **C# pre-pass**: same shape for `.cs` files → `csharp_prefix_map`, `csharp_type_owners`, `csharp_pre_parsed`.
3. **Main loop**: walks every source file once. For a file present in the caller-supplied `unchanged_modules` incremental-scan cache, skips parsing entirely and reuses the cached module dict, reconstructing `edges`/`imported_by_map` from its `"imports"` list. For a `.java`/`.cs` file, pops its pre-parsed `(source, Tree)` from the dicts above (never re-parses). For everything else, reads the file and parses it fresh, then dispatches to one of 9 per-language extraction branches (python/go/rust/java/ruby/php/c-cpp/csharp/javascript-family), each producing a `module` dict appended to `modules`, plus `edges`/`imported_by_map` entries per resolved import.

Verified, load-bearing facts this design depends on:

- Every multi-file test in `src/tests/test_graph*.py` and `test_evidence.py` accesses `modules` by path (`{m["path"]: m for m in modules}` or set comparison) — never by list index or exact list-order equality. **Output order is not depended upon anywhere.**
- `module_content_hash(module: dict)` (`src/aletheore/code_graph_diff.py`) hashes one module dict at a time — the incremental-cache system is also order-independent.
- The `unchanged_modules` cache-hit branch already reconstructs `edges`/`imported_by_map` purely from a module dict's `"imports"` list, with no other per-file state. This exact pattern is reusable for parallel results.
- The main loop today has **no per-file exception handling** — an unhandled exception during any file's parse aborts the whole `build_module_graph` call. This is today's real behavior, not something this design needs to preserve deliberately so much as simply not regress.
- `build_module_graph` has exactly one production caller: `src/aletheore/evidence.py`, which reports phase-level progress (e.g. "Building module dependency graph...") around the call — no per-file progress exists inside `build_module_graph` itself today, so none needs to be preserved.

## Decisions (confirmed with the user, 2026-08-27)

1. **Scope**: Java and C# stay fully sequential, exactly as today (their pre-pass + tree-reuse coupling is not touched). Only the other 9 languages' "needs fresh parsing" files go through the process pool. ERPNext (the benchmark corpus) is ~pure Python, so this costs nothing for the immediate goal while keeping the change smaller and safer.
2. **Worker count**: auto-detected via `os.cpu_count()`, no new CLI flag.
3. **Small-repo guard**: below a file-count threshold, stay fully sequential (today's exact code path). The threshold's exact value is an implementation-time empirical decision (measured against real repos of varying size during the plan's execution), not guessed here.

## Design

### New: worker-side extraction function

A new module-level function, e.g. `_parse_and_extract_one(path: Path, repo_path: Path, language_name: str, ts_language, python_source_roots: list[Path], go_module_prefix: str | None, has_rust_crate_root: bool, php_psr4_map: dict[str, Path]) -> dict`, in `src/aletheore/scanner/graph.py`. This is **not new extraction logic** — it's the existing "else" branch's per-language dispatch (the 9 non-Java/C# `elif language_name == ...` blocks), lifted into a standalone function whose only inputs are the read-only global state already computed before today's main loop starts, and whose only output is a plain dict shaped like today's module dict entries (`{"path", "language", "imports", "imported_by": [], "symbols": {...}}` — `imported_by` left empty, filled in later same as today) plus an optional unparseable-reason dict.

`tree_sitter.Language` objects themselves: need to confirm picklability before finalizing the exact signature (if not picklable, workers resolve `ts_language` from `language_name` via the existing `LANGUAGE_BY_EXTENSION`-equivalent mapping using their own worker-local constant, rather than receiving the `Language` object across the process boundary) — a concrete implementation-time check, not a design blocker.

### Worker pool lifecycle

- `ProcessPoolExecutor(initializer=_init_worker)` where `_init_worker()` creates one `tree_sitter.Parser()` per worker process (module-level global in the worker), created once per worker rather than once per file — the same lazy-create-then-reuse shape `build_module_graph` already uses for its own `parser` variable today, just per-worker instead of per-process.
- Only created/entered when the file count needing fresh parsing exceeds the small-repo threshold; otherwise the existing sequential branch runs unchanged.
- One task submitted per file (`executor.submit` or `.map` with a `chunksize` tuned during implementation) to `_parse_and_extract_one`.

### Merge strategy (the key simplification)

Workers return only `module` dicts (with `"imports"` fully resolved) and unparseable entries — nothing else. After collecting all results (regardless of arrival order), the main process applies the **exact same reconstruction the `unchanged_modules` cache-hit branch already performs today**: for each collected module, `for target in module["imports"]: edges.append([module["path"], target]); imported_by_map.setdefault(target, []).append(module["path"])`. This one pass handles cached modules, sequential Java/C# modules, and parallel-worker modules uniformly — no new merge data structure, no partial-combine step. The final `module["imported_by"] = sorted(...)` pass at the end of `build_module_graph` is unchanged.

### Error handling

A worker exception propagates through `Future.result()` and re-raises in the main process — the same all-or-nothing failure behavior `build_module_graph` already has today. No new fault-isolation semantics are introduced.

### Testing

- Unit tests for `_parse_and_extract_one` in isolation: given fixed inputs (a real small file + precomputed global state), asserts the correct module dict comes back — mirrors the existing per-language `test_graph_*.py` fixtures but calling the extracted function directly.
- An explicit **parity test**: run `build_module_graph` on the same small multi-file, multi-language fixture repo twice — once with the small-repo threshold forced low (exercising the parallel path) and once forced high (exercising today's sequential path) — and assert the two `(modules, dependency_graph, unparseable)` results are equal after order-independent normalization (sort both `modules` lists by `path`, sort `edges`). This is the regression guard against the two code paths silently diverging.
- Existing `test_graph*.py`/`test_evidence.py` suites should pass unchanged against whichever path their fixture's file count naturally selects; if any fixture's file count happens to cross the new threshold, parametrize it (or explicitly force the threshold) so both code paths get exercised by the existing corpus of language-specific test cases too, not just the new fixture above.

### Measurement (the deliverable)

Real, honest before/after `aletheore scan` wall-clock time on the same pinned ERPNext checkout already used for the Graphify benchmark (`~/.aletheore-bench/erpnext`, commit `d6956790d8f8940696783bc7ca85438ecd7d4b6e`), reported exactly — not estimated — regardless of outcome. If the parallel path doesn't land in time or doesn't help enough to justify shipping, that's the honest result to report, not something to fudge.

### Release

If it lands and measurably helps: version bump + PyPI release, the same way other `aletheore` CLI fixes have shipped this session. Per standing rule for every publish-facing artifact this session: **do not publish to PyPI without the user's explicit go-ahead at that specific step**, even though the underlying feature work itself was already approved here — a public package release is a separate, harder-to-reverse action from committing code.

## Risks / explicitly out of scope for this pass

- **Hosted scan-worker memory**: spawning `os.cpu_count()` worker processes multiplies memory (each worker independently loads the tree-sitter grammar libraries and holds its own in-flight ASTs). The hosted scan-worker's containers have observed OOM kills on huge repos under existing memory limits (see `GRAPH_COLD_SYNC_DEPTH_CAP` history in `github-app/scan_worker/jobs.py`). This design explicitly optimizes the **local CLI user's** experience; the hosted scan-worker's resource envelope is a separate, deliberately deferred concern per the user's direction ("right now we need the user side to be fixed, we can optimise for ourselves later").
- **Java/C# parallelization**: deliberately out of scope (Decision 1 above) — not a rejection of the idea, just not this pass.
- **A `--jobs`/`-j` override flag**: deliberately out of scope (Decision 2 above).
