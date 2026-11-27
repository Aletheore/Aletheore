"""Path scoping shared by `aletheore dependents` and the repo-wide `aletheore query` kinds: `--under <dir>`,
`--exclude <name>` and `--no-tests`, with one meaning everywhere.

Only kinds whose answer is a long list of paths can be scoped (see SCOPABLE). The scoped result says how much was left
out, so nothing disappears silently.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath, PureWindowsPath

_TEST_DIRS = {"test", "tests", "__tests__", "testdata", "spec", "specs", "e2e", "fixtures", "__mocks__"}
_TEST_NAME = re.compile(
    r"(^test_.*\.py$|_test\.(py|go)$|^conftest\.py$|\.(test|spec)\.[cm]?[jt]sx?$|(Test|Tests|IT)\.(java|kt)$|_spec\.rb$)"
)


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """'1 file', '2 files'."""
    return f"{count} {singular if count == 1 else (plural_form or singular + 's')}"


_RUNTIME_IMPORT_EXAMPLES = {
    "python": "importlib, __import__, string-based plugin loading",
    "javascript": "dynamic import() or require() with a computed path, bundler aliases",
    "typescript": "dynamic import() or require() with a computed path, bundler aliases",
    "go": "plugin loading, reflection, generated code",
}


def runtime_import_examples(languages: set[str]) -> str:
    """What 'imports built at run time' means for these languages, so a Go or TypeScript answer does not cite
    Python's importlib. Empty or unknown languages are ignored (Python plus a few unlabelled files still gets the
    Python wording); two different known mechanisms get the generic one."""
    examples = {
        _RUNTIME_IMPORT_EXAMPLES[lang.lower()] for lang in languages if lang and lang.lower() in _RUNTIME_IMPORT_EXAMPLES
    }
    if len(examples) == 1:
        return next(iter(examples))
    return "dynamic loading, reflection, generated code"


def is_test_path(path: str) -> bool:
    """True for files that are tests by the common conventions of the supported languages: a tests/ style directory
    or a test-shaped file name (test_x.py, x_test.go, x.test.ts, x.spec.js, XTest.java, ...)."""
    parts = path.split("/")
    return bool(_TEST_DIRS & set(parts[:-1])) or bool(_TEST_NAME.search(parts[-1]))


def matches(path: str, pattern: str) -> bool:
    """`pattern` is a whole directory name or file name ("tests", "locale"), not a substring: "locale" must not drop
    a module called django/middleware/locale.py."""
    return pattern in path.split("/")


def posix_rel(path: str) -> str:
    """A user-typed repo-relative path as the forward-slash form the evidence uses: backslashes become '/' (a Windows
    shell hands over `src\\app\\x.py`), repeated leading './' go, and '//' collapses."""
    text = path.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return re.sub(r"/{2,}", "/", text)


def resolve_target(target: str, known: set[str] | dict, repo_path: Path | None = None) -> str:
    """The entry of `known` (repo-relative, forward-slash paths) that a user-typed `target` means, or the normalized
    spelling when none matches, so the caller's own 'not found' message still has something sensible to show.

    Tried in order: the text as typed, its forward-slash form (Windows backslashes, './'), an absolute path inside
    `repo_path`, then a unique case-insensitive match (Windows and default macOS file systems ignore case, so
    `SRC\\Query.py` means `src/query.py` there). A case-insensitive match is used only when exactly one entry matches,
    never to guess between two files that differ only by case."""
    raw = target.strip()
    candidates = [raw.removeprefix("./")]
    normalized = posix_rel(raw)
    if repo_path is not None and (PureWindowsPath(raw).is_absolute() or PurePosixPath(normalized).is_absolute()):
        try:
            rel = Path(raw).resolve().relative_to(Path(repo_path).resolve())
        except (OSError, ValueError):
            pass
        else:
            candidates.append(rel.as_posix())
    candidates.append(normalized)
    trimmed = [c.rstrip("/") or "." for c in candidates]
    for candidate in [*candidates, *trimmed]:
        if candidate in known:
            return candidate
    folded = {c.casefold() for c in trimmed}
    hits = [k for k in known if k.casefold() in folded]
    if len(hits) == 1:
        return hits[0]
    return trimmed[-1]


def ignored_test_patterns(repo_path: Path) -> list[str]:
    """The repo's own `ignored_paths` entries (from .aletheore.json) that point at test code. Those files are never
    scanned, so an answer about tests is blind to them and must say so rather than report "no tests"."""
    from aletheore.repo_config import load_repo_config

    try:
        patterns = load_repo_config(Path(repo_path))["ignored_paths"]
    except (OSError, KeyError, TypeError):
        return []
    found = []
    for pattern in patterns:
        body = pattern.rstrip("/")
        if body and (is_test_path(f"{body}/x") or is_test_path(body)):
            found.append(pattern)
    return found


def normalize_under(under: list[str] | None) -> list[str]:
    """Directory prefixes ending in '/', with './' and trailing '/' removed; '' and '.' mean the whole repo."""
    cleaned = [posix_rel(u).strip("/") for u in (under or [])]
    return [u + "/" for u in cleaned if u not in ("", ".")]


def in_scope(path: str, exclude: list[str], under: list[str], no_tests: bool) -> bool:
    """`under` must already be normalized (see normalize_under)."""
    if any(matches(path, e) for e in exclude) or (no_tests and is_test_path(path)):
        return False
    return not under or path.startswith(tuple(under))


# kind -> {list key in the result: key holding that entry's path, or None when the entry is the path string itself}
SCOPABLE: dict[str, dict[str, str | None]] = {
    "dead-code": {"unreachable_modules": "path", "entry_points_detected": None},
    "error-handling": {"error_types": "file", "raise_sites": "file", "handlers": "file"},
}


def scope_result(kind: str, result: dict, exclude: list[str], under: list[str], no_tests: bool) -> dict:
    if kind not in SCOPABLE:
        raise ValueError(f"cannot scope {kind!r}; supported kinds: {', '.join(sorted(SCOPABLE))}")
    prefixes = normalize_under(under)
    out = dict(result)
    left_out: dict[str, int] = {}
    no_path: dict[str, int] = {}
    for key, path_key in SCOPABLE[kind].items():
        items = result.get(key) or []
        kept = []
        for item in items:
            path = item if path_key is None else (item.get(path_key) if isinstance(item, dict) else None)
            if not path:
                # An entry with no file cannot be placed inside or outside a directory, so it is kept and counted
                # rather than dropped for looking out of scope.
                kept.append(item)
                no_path[key] = no_path.get(key, 0) + 1
            elif in_scope(path, exclude, prefixes, no_tests):
                kept.append(item)
        out[key] = kept
        left_out[key] = len(items) - len(kept)
    note = (
        "lists were filtered by file path after the scan; counts in other fields (unused_dependencies) "
        "are repo-wide"
    )
    if kind == "error-handling":
        out["by_error_type"] = _recount_error_types(out)
        note = (
            "lists were filtered by file path after the scan; by_error_type was recounted from the "
            "filtered lists, so it agrees with them"
        )
    out["scope"] = {
        "applied": {"under": under, "exclude": exclude, "no_tests": no_tests},
        "left_out": left_out,
        "note": note,
    }
    if no_path:
        out["scope"]["kept_without_a_path"] = no_path
    return out


def _recount_error_types(scoped: dict) -> list[dict]:
    """by_error_type rebuilt from the already-filtered lists, so a scope that
    leaves no handlers cannot still report caught counts."""
    from aletheore.error_handling import NON_TYPES as _NON_TYPES, short_error_name as _last

    summary: dict[str, dict] = {}

    def entry(name: str) -> dict:
        return summary.setdefault(name, {"name": name, "defined_in": "", "raised": 0, "caught": 0})

    for definition in scoped.get("error_types") or []:
        if definition.get("name"):
            entry(definition["name"])["defined_in"] = definition.get("file") or ""
    for site in scoped.get("raise_sites") or []:
        raised = site.get("error_type")
        if raised and raised not in _NON_TYPES:
            entry(_last(raised))["raised"] += 1
    for handler in scoped.get("handlers") or []:
        for caught in handler.get("catches") or []:
            if caught not in _NON_TYPES:
                entry(_last(caught))["caught"] += 1
    ranked = sorted(summary.values(), key=lambda e: (-(e["raised"] + e["caught"]), e["name"]))
    return [e for e in ranked if e["raised"] or e["caught"] or e["defined_in"]]
