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


def search_ast_pattern(repo_path: Path, language: str, query_source: str) -> list[dict]:
    """Runs a tree-sitter S-expression query against every file of
    `language` under repo_path. One dict per match:
    `{"file": ..., "captures": {name: [{"text", "start_line", "end_line"}, ...]}}`
    - captures are exactly what the query itself names with `@capture`,
    nothing synthesized. A query with no captures at all matches structure
    but returns no text/location - the caller's query must name at least
    one node it cares about.

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
    for path in _iter_source_files(repo_path):
        ts_language = ext_to_ts_language.get(path.suffix)
        if ts_language is None:
            continue
        if path.stat().st_size > MAX_SOURCE_FILE_BYTES:
            continue
        _, tree = _read_and_parse(path, parser, ts_language)
        cursor = QueryCursor(queries[ts_language])
        rel_path = path.relative_to(repo_path).as_posix()
        for _pattern_index, captures in cursor.matches(tree.root_node):
            results.append(
                {
                    "file": rel_path,
                    "captures": {
                        name: [
                            {
                                "text": node.text.decode("utf-8", errors="replace"),
                                "start_line": node.start_point.row + 1,
                                "end_line": node.end_point.row + 1,
                            }
                            for node in nodes
                        ]
                        for name, nodes in captures.items()
                    },
                }
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
        # keeps the cycle count at most one at a time.
        del tree, cursor
    return results
