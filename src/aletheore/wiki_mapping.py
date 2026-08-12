"""Deterministic cluster brief extraction for the Live Wiki.

Gathers, for each architecture cluster, its member files and each file's
key symbols directly from the scanner's own evidence - no LLM involved.
This is the "map" a naming/writing model is later pointed at, so it never
has to rediscover structure the scanner already knows.
"""

import os

MAX_SYMBOLS_PER_FILE = 15

# Paths that are real code but almost never what a reader means by "explain
# this codebase". Kept as a demotion rather than an exclusion: a test helper
# can still earn a page in a repo that is mostly tests, it just loses to
# application code of equal in-degree.
_DEMOTED_SEGMENTS = ("tests/", "test/", "examples/", "example/", "docs/", "benchmarks/", "fixtures/")
_DEMOTED_BASENAMES = ("conftest.py", "setup.py", "__main__.py")
_DEMOTION_FACTOR = 0.15


def _key_symbols(module: dict) -> list[dict]:
    symbols = module.get("symbols", {})
    entries = [
        {"name": s["name"], "kind": "function", "start_line": s["start_line"], "end_line": s["end_line"]}
        for s in symbols.get("functions", [])
    ] + [
        {"name": s["name"], "kind": "class", "start_line": s["start_line"], "end_line": s["end_line"]}
        for s in symbols.get("classes", [])
    ] + [
        # Module-level bindings. A file can export a whole public API without a
        # def or a class - Flask's signals.py is ten assignments - and such a
        # file previously reached the writing model with an empty symbol list,
        # so it got no wiki page at all.
        {"name": s["name"], "kind": "constant", "start_line": s["start_line"], "end_line": s["end_line"]}
        for s in symbols.get("constants", [])
        if s.get("is_public", True)
    ]
    return entries[:MAX_SYMBOLS_PER_FILE]


def _fallback_name(file_paths: list[str]) -> str:
    """A readable name derived purely from the files themselves, used if
    the naming model is unavailable - never blocks the wiki on an LLM call.
    """
    if not file_paths:
        return "Unnamed subsystem"
    common = os.path.commonpath(file_paths) if len(file_paths) > 1 else os.path.dirname(file_paths[0])
    return common or file_paths[0]


def _is_demoted(path: str) -> bool:
    normalized = path.replace(os.sep, "/")
    if os.path.basename(normalized) in _DEMOTED_BASENAMES:
        return True
    return any(seg in f"/{normalized}" for seg in (f"/{s}" for s in _DEMOTED_SEGMENTS))


def rank_files_by_importance(evidence: dict) -> list[dict]:
    """Orders a repository's files by how much a reader is likely to care.

    Deterministic, no LLM. Three signals, all already in the evidence:

    - in-degree: how many modules import this one
    - churn: how often git touches it
    - size: how many symbols it defines

    Size matters because in-degree alone systematically under-ranks the files
    a reader most wants explained. Flask's `app.py` is imported by 6 modules
    and defines the framework; on in-degree alone it placed 15th, below
    `typing.py`. Entry points and god-modules sit at the *top* of the import
    tree, so few things import them.

    Tests, examples and docs are demoted rather than dropped - a repo that is
    mostly tests should still document something.

    Returns dicts of {path, score, imported_by, churn, symbols, demoted},
    highest first. Ties break on path so the ordering is stable across runs.
    """
    modules = evidence.get("repository", {}).get("modules", [])
    hotspots = evidence.get("git", {}).get("hotspots", []) or []
    churn_by_path = {
        # The scanner emits `churn_count`; `commits` is accepted as a fallback
        # so an older air.json still contributes churn instead of silently
        # scoring every file zero.
        h["path"]: h.get("churn_count", h.get("commits", 0)) or 0
        for h in hotspots
        if isinstance(h, dict) and "path" in h
    }
    max_churn = max(churn_by_path.values(), default=0)

    def symbol_count(module: dict) -> int:
        symbols = module.get("symbols", {}) or {}
        return (
            len(symbols.get("functions", []) or [])
            + len(symbols.get("classes", []) or [])
            + len(symbols.get("constants", []) or [])
        )

    max_symbols = max((symbol_count(m) for m in modules), default=0)

    ranked = []
    for module in modules:
        path = module.get("path")
        if not path:
            continue
        in_degree = len(module.get("imported_by", []) or [])
        churn = churn_by_path.get(path, 0)
        symbols = symbol_count(module)
        # Each secondary signal is normalised to its own max before being
        # weighted, so neither swamps in-degree on repos with long histories
        # or one unusually large module.
        score = (
            in_degree
            + (churn / max_churn if max_churn else 0.0) * 5.0
            + (symbols / max_symbols if max_symbols else 0.0) * 12.0
        )
        demoted = _is_demoted(path)
        if demoted:
            score *= _DEMOTION_FACTOR
        ranked.append(
            {
                "path": path,
                "score": score,
                "imported_by": in_degree,
                "churn": churn,
                "symbols": symbols,
                "demoted": demoted,
            }
        )

    ranked.sort(key=lambda r: (-r["score"], r["path"]))
    return ranked


def build_cluster_briefs(evidence: dict) -> list[dict]:
    clusters = evidence.get("architecture", {}).get("clusters", [])
    modules_by_path = {m["path"]: m for m in evidence.get("repository", {}).get("modules", [])}

    briefs = []
    for cluster in clusters:
        member_paths = cluster.get("modules", [])
        files = []
        for path in member_paths:
            module = modules_by_path.get(path)
            if module is None:
                continue
            files.append(
                {
                    "path": path,
                    "language": module.get("language"),
                    "key_symbols": _key_symbols(module),
                }
            )
        briefs.append(
            {
                "cluster_id": cluster["id"],
                "files": files,
                "fallback_name": _fallback_name(member_paths),
            }
        )
    return briefs
