"""On-demand structural code search: match a raw tree-sitter query against
every parsed file of one language in a repository.

Unlike the rest of `aletheore query`, this reads and re-parses source from
disk at query time rather than reading cached `.aletheore/air.json`
evidence - a structural pattern match needs the actual parse tree, which
air.json never stores (the extracted symbol/import facts are a small
summary derived from it, not the tree itself).

Only user of tree_sitter's Query/QueryCursor API in this codebase - every
other module uses Parser/Tree/Node directly. That distinction matters: this
module's real-repo use of Query/QueryCursor segfaults reliably on Python
3.14 once enough files/matches accumulate for the cyclic GC to touch the
resulting Node/Tree/QueryCursor object graph (see pyproject.toml's
`requires-python` upper bound). Not fixed by explicit `del`,
`gc.disable()`, or forced `gc.collect()` per iteration - tried all three,
none prevented it.

CORRECTION (2026-09-04, dead-code/ast-pattern overnight benchmark pass):
this module's own prior claim that "the exact same code runs clean on
3.12" was false for real repos at real scale - it was only ever verified
against this project's own ~116 source files. Confirmed directly on
Python 3.12.10, tree-sitter 0.26.0: a low-match-density query
(`(try_statement) @try`) against Django's real ~2,930-file Python tree
segfaults reliably - reproduced independently a second time the same day
(3 of 4 runs, exit code 139 each time; the one clean run confirms this is
probabilistic/timing-dependent, not deterministic - consistent with a
memory-corruption-class bug, not a logic bug). The crash correlates with
*files fully processed before any cap triggers*, not language, matches
found, or Python version alone - a query selective enough to run long
against a big enough repo can hit this on 3.11/3.12 too, inside the
officially supported range, not only on the already-excluded 3.14.

FIX (2026-09-04): batched subprocess isolation. Each batch of
_AST_PATTERN_BATCH_SIZE files runs in its own fresh worker process
(ProcessPoolExecutor with max_tasks_per_child=1 - the default reuses one
worker across every submitted task, which would recreate the exact same
unbounded accumulation this fix exists to prevent). A segfault kills that
one batch's worker, not the whole call: caught as BrokenProcessPool,
every earlier batch's real results are kept, the remainder is marked
truncated - the same honest-truncation contract _AST_PATTERN_MATCH_CAP/
_AST_PATTERN_TOTAL_CHAR_BUDGET already use, not a silent gap. Verified
against the exact case that crashed above: `(try_statement) @try` against
Django's real tree, 20 consecutive runs, 0 crashes (see
benchmarks/ast-pattern-benchmark/ for the full before/after methodology).
Chosen over the two mitigations this module previously flagged as
untried-and-risky: unbatched (one-process-per-call) subprocess isolation
doesn't actually fix anything (the same accumulation happens inside that
one child); pre-emptive file-count truncation needs a threshold with no
clean way to calibrate it, and is silently wrong if that guess is off -
batching costs real per-batch subprocess-spawn latency too, but a wrong
batch-size guess degrades to "that batch's worker dies, cleanly caught,
marked truncated," never a silently incomplete result.

VERIFIED ON 3.14 ITSELF (2026-09-04, same pass): the crash does reproduce
on 3.14 (tree-sitter 0.26.0) - but not from one big single-process call
the way it does on 3.12. 16 consecutive single-call runs of a
full-scan-forcing query (`(match_statement) @m` against Django, 4 real
matches) were all clean; the crash only appeared when the SAME unfixed
process called `search_ast_pattern` repeatedly without restarting -
exactly how a long-lived MCP server process actually behaves (one call
per tool invocation, same process). Segfaulted (exit 139) on the 3rd
repeated call. This means single-call testing alone understates real
risk on 3.14: the object graph accumulates *across* calls, not only
within one. The fix holds under this harder, more realistic scenario
too - 20 consecutive repeated in-process calls of the same query, 0
crashes, because each call's own ProcessPoolExecutor tears its workers
down at call end, so nothing carries over into the next call's object
graph regardless of how many calls the parent process lives through.
"""

from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

from tree_sitter import Parser, Query, QueryCursor, QueryError

from aletheore.scanner.graph import (
    LANGUAGE_BY_EXTENSION,
    MAX_SOURCE_FILE_BYTES,
    _iter_source_files,
    _read_and_parse,
)


class UnknownLanguageError(Exception):
    """`language` isn't one LANGUAGE_BY_EXTENSION recognizes."""


class InvalidPatternError(Exception):
    """`query_source` doesn't compile against the language's grammar."""


# Same caps, same reasoning, as mcp_server.py's _search_files/
# _SEARCH_MATCH_CAP/_SEARCH_TOTAL_CHAR_BUDGET: a broad structural query
# (e.g. every function definition) has no reason to be bounded by
# anything in this module on its own, and a real unscoped query against
# this repo alone was measured to return ~5.6M characters - ~14x past
# the ~390,000-char limit that sank an earlier unbounded MCP result (see
# _search_files' own history).
_AST_PATTERN_MATCH_CAP = 200
_AST_PATTERN_TOTAL_CHAR_BUDGET = 100_000

# See module docstring's FIX note. 200 chosen as a starting point matching
# _AST_PATTERN_MATCH_CAP's own number for a simple mental model - the two
# bound different things (result count vs. files-per-worker-process) and
# aren't required to match; unlike a truncation threshold, a wrong guess
# here just changes how often a batch's worker gets recycled, not whether
# results are silently incomplete.
_AST_PATTERN_BATCH_SIZE = 200


def _extensions_and_languages_for(language: str) -> list[tuple[str, object]]:
    matches = [
        (ext, ts_language)
        for ext, (name, ts_language) in LANGUAGE_BY_EXTENSION.items()
        if name == language
    ]
    if not matches:
        known = sorted({name for name, _ in LANGUAGE_BY_EXTENSION.values()})
        raise UnknownLanguageError(f"unknown language {language!r} - supported: {', '.join(known)}")
    return matches


def _compile_queries(ext_languages: list[tuple[str, object]], query_source: str) -> dict:
    # A Query is compiled against one specific grammar object - typescript
    # spans two (.ts/.tsx use different grammars: TS_LANGUAGE/TSX_LANGUAGE,
    # see LANGUAGE_BY_EXTENSION), so compile once per distinct grammar the
    # requested language name actually covers, not once per language name.
    distinct_ts_languages = {ts_language for _, ts_language in ext_languages}
    queries = {}
    for ts_language in distinct_ts_languages:
        try:
            queries[ts_language] = Query(ts_language, query_source)
        except QueryError as exc:
            raise InvalidPatternError(str(exc)) from exc
    return queries


def _search_file_batch(
    repo_path_str: str,
    language: str,
    query_source: str,
    rel_paths: list[str],
    results_remaining: int,
    chars_remaining: int,
) -> tuple[list[dict], int, bool]:
    """Runs inside its own fresh worker process (see search_ast_pattern) -
    recompiles the query itself since compiled tree_sitter Query/Language
    objects wrap C pointers and aren't picklable across the process
    boundary, so every batch's worker reconstructs its own. Processes only
    `rel_paths` (one batch), honoring `results_remaining`/`chars_remaining`
    - this call's share of the cross-batch cumulative caps, so a match cap
    hit mid-batch still stops exactly where the old single-process version
    would have. Returns (matches, chars_used_by_this_batch,
    truncated_within_this_batch).
    """
    repo_path = Path(repo_path_str)
    ext_languages = _extensions_and_languages_for(language)
    queries = _compile_queries(ext_languages, query_source)
    ext_to_ts_language = dict(ext_languages)
    parser = Parser()
    results: list[dict] = []
    total_chars = 0
    truncated = False
    for rel_path in rel_paths:
        path = repo_path / rel_path
        ts_language = ext_to_ts_language.get(path.suffix)
        if ts_language is None:
            continue
        try:
            if path.stat().st_size > MAX_SOURCE_FILE_BYTES:
                continue
            _, tree = _read_and_parse(path, parser, ts_language)
        except OSError:
            # See search_ast_pattern's own history: a file vanishing or
            # becoming unreadable mid-batch must not cost the rest of this
            # batch's real results.
            continue
        cursor = QueryCursor(queries[ts_language])
        for _pattern_index, captures in cursor.matches(tree.root_node):
            if len(results) >= results_remaining:
                truncated = True
                break
            match_captures = {
                name: [
                    {
                        "text": node.text.decode("utf-8", errors="replace"),
                        "start_line": node.start_point.row + 1,
                        "end_line": node.end_point.row + 1,
                    }
                    for node in nodes
                ]
                for name, nodes in captures.items()
            }
            # Sized BEFORE appending, not checked-then-appended-regardless -
            # a match whose own captures alone exceed the remaining budget
            # is dropped, not truncated mid-capture (see search_ast_pattern's
            # own history - a partial capture's text would be misleading,
            # not just short).
            match_chars = sum(
                len(capture["text"])
                for capture_list in match_captures.values()
                for capture in capture_list
            )
            if total_chars + match_chars > chars_remaining:
                truncated = True
                break
            results.append({"file": rel_path, "captures": match_captures})
            total_chars += match_chars
        del tree, cursor
        if truncated:
            break
    return results, total_chars, truncated


def search_ast_pattern(repo_path: Path, language: str, query_source: str) -> dict:
    """Runs a tree-sitter S-expression query against every file of
    `language` under repo_path. Returns `{"matches": [...], "truncated": bool}` -
    one dict per match in "matches":
    `{"file": ..., "captures": {name: [{"text", "start_line", "end_line"}, ...]}}`
    - captures are exactly what the query itself names with `@capture`,
    nothing synthesized. A query with no captures at all matches structure
    but returns no text/location - the caller's query must name at least
    one node it cares about. "truncated" is honest, not assumed: a broad
    structural query is capped at _AST_PATTERN_MATCH_CAP matches and
    _AST_PATTERN_TOTAL_CHAR_BUDGET total captured characters, same
    reasoning as mcp_server.py's file-search tool - and also set when a
    batch's worker process crashes before finishing (see module docstring).

    Raises UnknownLanguageError for a language name LANGUAGE_BY_EXTENSION
    doesn't recognize, InvalidPatternError for a query_source that fails
    to compile against the language's grammar.
    """
    ext_languages = _extensions_and_languages_for(language)
    _compile_queries(ext_languages, query_source)  # fail fast, before spawning anything

    valid_extensions = {ext for ext, _ in ext_languages}
    rel_paths = [
        path.relative_to(repo_path).as_posix()
        for path in _iter_source_files(repo_path)
        if path.suffix in valid_extensions
    ]
    batches = [
        rel_paths[i : i + _AST_PATTERN_BATCH_SIZE]
        for i in range(0, len(rel_paths), _AST_PATTERN_BATCH_SIZE)
    ]

    results: list[dict] = []
    total_chars = 0
    truncated = False
    repo_path_str = str(repo_path)
    # max_tasks_per_child=1 is load-bearing, not a tuning knob: without it
    # ProcessPoolExecutor reuses one worker across every submitted batch,
    # which would recreate the exact unbounded in-process accumulation
    # this fix exists to bound. This forces a genuinely fresh process per
    # batch, matching "recycle before the object graph gets big enough to
    # crash" rather than "spawn once, then behave like the old code."
    with ProcessPoolExecutor(max_workers=1, max_tasks_per_child=1) as executor:
        for batch in batches:
            results_remaining = _AST_PATTERN_MATCH_CAP - len(results)
            chars_remaining = _AST_PATTERN_TOTAL_CHAR_BUDGET - total_chars
            if results_remaining <= 0 or chars_remaining <= 0:
                truncated = True
                break
            future = executor.submit(
                _search_file_batch,
                repo_path_str,
                language,
                query_source,
                batch,
                results_remaining,
                chars_remaining,
            )
            try:
                batch_results, batch_chars, batch_truncated = future.result()
            except BrokenProcessPool:
                # This batch's worker segfaulted (see module docstring's
                # FIX note). Every earlier batch's results are real and
                # already in `results` - kept, not discarded. This batch's
                # own in-flight work is lost, same as any other
                # truncation: honest, not silent.
                truncated = True
                break
            results.extend(batch_results)
            total_chars += batch_chars
            if batch_truncated:
                truncated = True
                break
    return {"matches": results, "truncated": truncated}
