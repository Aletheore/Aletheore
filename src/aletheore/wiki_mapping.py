"""Deterministic cluster brief extraction for the Live Wiki.

Gathers, for each architecture cluster, its member files and each file's
key symbols directly from the scanner's own evidence - no LLM involved.
This is the "map" a naming/writing model is later pointed at, so it never
has to rediscover structure the scanner already knows.
"""

import os

# Raised from 15, where large files lost most of their surface before the
# writing model ever saw it - 29 of Flask's 83 files hit that cap. Briefly 50,
# then reverted: the extra depth measured no better and an oversized brief is
# what made the model stop finishing its output. What mattered was the
# ordering, not the depth - entries are sorted public-first by source span, so
# 30 trims genuine tail rather than silently dropping every class in a
# function-heavy file (Flask's app.py showed 15 symbols, all functions, with
# the Flask class itself invisible).
MAX_SYMBOLS_PER_FILE = 30

# Paths that are real code but almost never what a reader means by "explain
# this codebase". Kept as a demotion rather than an exclusion: a test helper
# can still earn a page in a repo that is mostly tests, it just loses to
# application code of equal in-degree.
_DEMOTED_SEGMENTS = ("tests/", "test/", "examples/", "example/", "docs/", "benchmarks/", "fixtures/")
_DEMOTED_BASENAMES = ("conftest.py", "setup.py", "__main__.py")
_DEMOTION_FACTOR = 0.15

# Weight for a module re-exported by a package __init__.py. Sized to lift a
# public entry point past mid-ranked internals without letting it outrank a
# module that is genuinely central by every other measure.
_PUBLIC_API_WEIGHT = 8.0


def _key_symbols(module: dict) -> list[dict]:
    """The symbols a writing model is shown for one file, most important first.

    Ordering matters as much as the cap. This used to concatenate functions,
    then classes, then constants and truncate the result, so in a
    function-heavy file no class ever survived: Flask's `app.py` has 40
    functions, 1 class and 6 constants, and the model writing its page was
    shown 15 symbols - all functions, with the `Flask` class itself invisible.

    Public symbols come first, then the largest by source span, because a
    long definition is where the behaviour lives. Ties break on start_line so
    the ordering is stable across runs.
    """
    symbols = module.get("symbols", {})
    entries = [
        {
            "name": s["name"], "kind": kind,
            "start_line": s["start_line"], "end_line": s["end_line"],
            "is_public": s.get("is_public", True),
        }
        for kind, group in (("function", "functions"), ("class", "classes"), ("constant", "constants"))
        for s in symbols.get(group, []) or []
        # A private module-level binding is an implementation detail, but a
        # private function or class can still be the bulk of a file's logic.
        if kind != "constant" or s.get("is_public", True)
    ]
    entries.sort(
        key=lambda e: (
            not e["is_public"],
            -((e.get("end_line") or 0) - (e.get("start_line") or 0)),
            e.get("start_line") or 0,
        )
    )
    for entry in entries:
        entry.pop("is_public", None)
    return entries[:MAX_SYMBOLS_PER_FILE]


def _fallback_name(file_paths: list[str]) -> str:
    """A readable name derived purely from the files themselves, used if
    the naming model is unavailable - never blocks the wiki on an LLM call.
    """
    if not file_paths:
        return "Unnamed subsystem"
    common = os.path.commonpath(file_paths) if len(file_paths) > 1 else os.path.dirname(file_paths[0])
    return common or file_paths[0]


def is_demoted_path(path: str) -> bool:
    """Whether a path is test, example or documentation code.

    Public because the wiki generator needs the same judgement to decide which
    clusters are worth an LLM call, and both must agree on what "demoted" means.
    """
    normalized = path.replace(os.sep, "/")
    if os.path.basename(normalized) in _DEMOTED_BASENAMES:
        return True
    return any(seg in f"/{normalized}" for seg in (f"/{s}" for s in _DEMOTED_SEGMENTS))


def rank_files_by_importance(evidence: dict) -> list[dict]:
    """Orders a repository's files by how much a reader is likely to care.

    Deterministic, no LLM. Four signals, all already in the evidence:

    - in-degree: how many modules import this one
    - churn: how often git touches it
    - size: how many symbols it defines
    - public API: whether a package `__init__.py` re-exports it

    The last two exist because in-degree alone systematically under-ranks the
    files a reader most wants explained. Entry points sit at the *top* of the
    import tree, so almost nothing imports them, while leaf utilities are
    imported by everything. Both failures were measured, not hypothesised:
    Flask's `app.py` placed 15th on in-degree alone, below `typing.py`; and on
    in-degree plus size, requests' `api.py` - which defines `get`/`post`/`put`
    and is the entire public API - placed 17th and received no page, while
    `compat.py`, a compatibility shim, placed 1st.

    Tests, examples and docs are demoted rather than dropped - a repo that is
    mostly tests should still document something.

    Returns dicts of {path, score, imported_by, churn, symbols, public_api,
    demoted}, highest first. Ties break on path so ordering is stable.
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

    # Modules a package's __init__.py imports are its public API surface, and
    # that is the single strongest signal of what a reader came to read. It has
    # to be scored separately because in-degree actively works against it: an
    # entry point is imported once, by the __init__ that re-exports it, while a
    # leaf utility is imported by everything. Measured on psf/requests, where
    # api.py defines get/post/put/delete and is the whole public API: on
    # in-degree plus size it ranked 17th and got no page, while compat.py - a
    # compatibility shim - ranked 1st.
    reexported: set[str] = set()
    for module in modules:
        path = (module.get("path") or "").replace(os.sep, "/")
        if os.path.basename(path) == "__init__.py":
            reexported.update(module.get("imports", []) or [])

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
        is_public_api = path in reexported
        score = (
            in_degree
            + (churn / max_churn if max_churn else 0.0) * 5.0
            + (symbols / max_symbols if max_symbols else 0.0) * 12.0
            + (_PUBLIC_API_WEIGHT if is_public_api else 0.0)
        )
        demoted = is_demoted_path(path)
        if demoted:
            score *= _DEMOTION_FACTOR
        ranked.append(
            {
                "path": path,
                "score": score,
                "imported_by": in_degree,
                "churn": churn,
                "symbols": symbols,
                "public_api": is_public_api,
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
