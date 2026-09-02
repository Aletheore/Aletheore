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
resulting Node/Tree/QueryCursor object graph (reproduced directly, same
code runs clean on 3.12 - see pyproject.toml's `requires-python` upper
bound). Not fixed by explicit `del`, `gc.disable()`, or forced
`gc.collect()` per iteration - tried all three, none prevented it. If the
`<3.14` bound is ever loosened, re-verify this module specifically against
a real, many-file repo before trusting it, not just the unit tests (which
use single-digit-file fixtures too small to trigger it).
"""

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
# _search_files' own history). No timeout/subprocess isolation needed
# here unlike the regex-search sibling: QueryCursor.matches() is a
# deterministic tree walk, not backtracking regex, so there's no
# pathological-slowness risk to guard against, only pathological size.
_AST_PATTERN_MATCH_CAP = 200
_AST_PATTERN_TOTAL_CHAR_BUDGET = 100_000


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
    reasoning as mcp_server.py's file-search tool.

    Raises UnknownLanguageError for a language name LANGUAGE_BY_EXTENSION
    doesn't recognize, InvalidPatternError for a query_source that fails
    to compile against the language's grammar.
    """
    ext_languages = _extensions_and_languages_for(language)

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

    ext_to_ts_language = dict(ext_languages)
    parser = Parser()
    results: list[dict] = []
    total_chars = 0
    truncated = False
    for path in _iter_source_files(repo_path):
        ts_language = ext_to_ts_language.get(path.suffix)
        if ts_language is None:
            continue
        try:
            if path.stat().st_size > MAX_SOURCE_FILE_BYTES:
                continue
            _, tree = _read_and_parse(path, parser, ts_language)
        except OSError:
            # A file can vanish or become unreadable between
            # _iter_source_files listing its full path set upfront and this
            # read - deleted mid-query, permissions changed, a concurrent
            # `aletheore watch` touching it. Skipping it must not cost
            # every other file's results, which an unhandled crash here
            # previously did (verified: a chmod-000 file in a two-file repo
            # lost the OTHER, readable file's real match too, not just its
            # own - the whole call raised before returning anything).
            continue
        cursor = QueryCursor(queries[ts_language])
        rel_path = path.relative_to(repo_path).as_posix()
        for _pattern_index, captures in cursor.matches(tree.root_node):
            if len(results) >= _AST_PATTERN_MATCH_CAP or total_chars >= _AST_PATTERN_TOTAL_CHAR_BUDGET:
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
            results.append({"file": rel_path, "captures": match_captures})
            total_chars += sum(
                len(capture["text"])
                for capture_list in match_captures.values()
                for capture in capture_list
            )
        # Explicit, not left to reassignment next iteration: Node/Tree form
        # a reference cycle in this tree-sitter binding, so plain refcounting
        # never frees them - only the cyclic GC does, on its own schedule.
        # Left alone, cyclic garbage from every file in a large repo (100+)
        # piles up until an eventual mass collection (or process exit)
        # frees many Tree objects at once, which segfaults - reproduced
        # directly running this function against this project's own 116
        # source files: a build with `del` here never crashes, one without
        # it does, deterministically, every time. Deleting each iteration
        # keeps the cycle count at most one at a time. Runs even on the
        # truncating iteration, before the outer break below - this file's
        # tree/cursor were still created and still need the same cleanup.
        del tree, cursor
        if truncated:
            break
    return {"matches": results, "truncated": truncated}
