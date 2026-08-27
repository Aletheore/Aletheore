import re
from pathlib import Path

from aletheore.repo_config import is_ignored
from aletheore.scanner.detect import IGNORED_DIRS
from aletheore.vulnerabilities import _parse_npm_direct_pins, _parse_pip_pins

ENTRY_POINT_FILENAMES = {
    "__init__.py",
    "__main__.py",
    "app.py",
    "asgi.py",
    "cli.py",
    "conftest.py",
    "index.js",
    "index.jsx",
    "index.ts",
    "index.tsx",
    "main.py",
    "manage.py",
    "server.py",
    "wsgi.py",
}

TEST_PATH_PATTERNS = [
    re.compile(r"(^|/)test_[^/]+\.py$"),
    re.compile(r"(^|/)[^/]+_test\.py$"),
    re.compile(r"(^|/)[^/]+\.test\.[jt]sx?$"),
    re.compile(r"(^|/)[^/]+\.spec\.[jt]sx?$"),
    re.compile(r"(^|/)(tests?|__tests__)/"),
]

PACKAGE_IMPORT_ALIASES = {
    "beautifulsoup4": {"bs4"},
    "pillow": {"pil"},
    "pyyaml": {"yaml"},
    "python-dotenv": {"dotenv"},
    "scikit-learn": {"sklearn"},
}

# A file run directly (`python worker.py`, `python -m pkg.worker`) is never
# imported by another module, so it always looks unreachable by that signal
# alone - but a __main__ guard means it's deliberately invoked, not dead.
# Confirmed on this repo: RQ worker processes and standalone scripts/*.py
# CLI tools all follow this convention regardless of filename.
_MAIN_GUARD_PATTERN = re.compile(r"if\s+__name__\s*==\s*[\'\"]__main__[\'\"]\s*:")

_HTML_SCRIPT_SRC_PATTERN = re.compile(r'<script[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)

# A module dispatched by dotted-string name (RQ's queue.enqueue("pkg.mod.func", ...),
# Celery task names, cron-style job registries) is never imported by another module
# either - the same blind spot as the __main__-guard case above, just for library-level
# dynamic dispatch instead of direct script invocation. Confirmed on this repo:
# scan_worker/jobs.py and scan_worker/demo_scan.py are the busiest modules in the
# worker, dispatched exclusively via `queue.enqueue("scan_worker.jobs.<fn>", ...)`
# string literals from scheduler.py and friends, and looked completely unreachable
# without this check. Minimum 2 dotted segments required - a single bare segment
# (just "jobs") collides with too many unrelated identifiers to be a reliable signal.
_DOTTED_STRING_REF_MIN_SEGMENTS = 2


def _dotted_path_candidates(path: str) -> list[str]:
    if not path.endswith(".py"):
        return []
    parts = [part for part in path[: -len(".py")].split("/") if part and part != "__init__"]
    span = len(parts) - _DOTTED_STRING_REF_MIN_SEGMENTS + 1
    return [".".join(parts[start:]) for start in range(max(span, 0))]


# Matches a quote character immediately followed by a run of identifier/dot
# characters - the same shape _referenced_by_dotted_string used to look for
# per (candidate, file) pair (a literal candidate string immediately after
# an opening quote, ending at the next '.' or closing quote), captured once
# per file instead. Confirmed by direct profile (2026-08-27): the old
# per-candidate approach spent 180 of 184 seconds in re.Pattern.search,
# 6.9M calls, on a real ~1M LOC repo (ERPNext) where most files have no
# static importer (Frappe's ORM loads doctype controllers by dotted-string
# name, not `import`) and so become dotted-string-check candidates.
_QUOTED_WORD_DOT_RUN_RE = re.compile(r'["\']([\w][\w.]*)')


def _dot_boundary_prefixes(token: str) -> set[str]:
    # "pkg.mod.func" -> {"pkg", "pkg.mod", "pkg.mod.func"} - every prefix
    # ending exactly at a '.' boundary, the same set of strings the old
    # regex's `(?=[.\'"])` lookahead would each independently match against
    # this same quoted run.
    parts = token.split(".")
    return {".".join(parts[:k]) for k in range(1, len(parts) + 1)}


def _dotted_string_token_index(sources: dict[str, str]) -> dict[str, set[str]]:
    """dotted-string token -> set of file paths whose content references it
    (quote-adjacent, at a '.'-or-closing-quote boundary) - built once for
    the whole corpus, replacing what used to be a fresh regex scan of every
    file per candidate. O(total source size) instead of
    O(candidates x total source size)."""
    index: dict[str, set[str]] = {}
    for path, content in sources.items():
        for match in _QUOTED_WORD_DOT_RUN_RE.finditer(content):
            for prefix in _dot_boundary_prefixes(match.group(1)):
                index.setdefault(prefix, set()).add(path)
    return index


def _referenced_by_dotted_string(path: str, token_index: dict[str, set[str]]) -> bool:
    for candidate in _dotted_path_candidates(path):
        owners = token_index.get(candidate)
        if owners and owners - {path}:
            return True
    return False


def _is_entry_point(path: str, custom_entry_points: set[str]) -> bool:
    if path in custom_entry_points:
        return True
    return path.rsplit("/", 1)[-1] in ENTRY_POINT_FILENAMES


def _has_main_guard(repo_path: Path, path: str) -> bool:
    if not path.endswith(".py"):
        return False
    try:
        content = (repo_path / path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return bool(_MAIN_GUARD_PATTERN.search(content))


def _html_script_entry_points(repo_path: Path, ignored_paths: list[str] | None = None) -> set[str]:
    # Plain <script src="..."> tags (no bundler, no ES module imports) are
    # invisible to the JS import graph - confirmed on this repo's website/:
    # every JS file loaded that way looked unreachable despite being the
    # actual entry point a browser executes.
    entry_points = set()
    patterns = ignored_paths or []
    for html_file in repo_path.rglob("*.html"):
        rel_path = html_file.relative_to(repo_path).as_posix()
        rel_parts = html_file.relative_to(repo_path).parts
        if any(part in IGNORED_DIRS for part in rel_parts) or is_ignored(rel_path, patterns):
            continue
        try:
            content = html_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in _HTML_SCRIPT_SRC_PATTERN.finditer(content):
            src = match.group(1)
            if src.startswith(("http://", "https://", "//")):
                continue
            resolved = (html_file.parent / src).resolve()
            try:
                rel = resolved.relative_to(repo_path.resolve())
            except ValueError:
                continue
            entry_points.add(rel.as_posix())
    return entry_points


def is_test_file(path: str) -> bool:
    return any(pattern.search(path) for pattern in TEST_PATH_PATTERNS)


def _import_roots(modules: list[dict]) -> set[str]:
    roots = set()
    for module in modules:
        for imported in module.get("imports", []):
            root = imported.split("/", 1)[0].split(".", 1)[0].lower()
            if root:
                roots.add(root.replace("-", "_"))
    return roots


def _package_import_names(package: str) -> set[str]:
    normalized = package.lower().replace("-", "_")
    names = {normalized}
    names.update(PACKAGE_IMPORT_ALIASES.get(package.lower(), set()))
    return names


def find_dead_code(
    repo_path: Path,
    modules: list[dict],
    config: dict | None,
    ignored_paths: list[str] | None = None,
) -> dict:
    custom_entry_points = set()
    if isinstance(config, dict):
        raw_entry_points = config.get("dead_code_entry_points", [])
        if isinstance(raw_entry_points, list):
            custom_entry_points = {path for path in raw_entry_points if isinstance(path, str)}

    html_script_entry_points = _html_script_entry_points(repo_path, ignored_paths)

    unreachable_modules = []
    entry_points_detected = []
    for module in modules:
        path = module["path"]
        if _is_entry_point(path, custom_entry_points):
            entry_points_detected.append(path)
            continue
        if is_test_file(path):
            continue
        if not module.get("imported_by", []):
            if path in html_script_entry_points or _has_main_guard(repo_path, path):
                entry_points_detected.append(path)
                continue
            unreachable_modules.append(
                {"path": path, "reason": "no other module imports this file"}
            )

    if any(entry["path"].endswith(".py") for entry in unreachable_modules):
        py_sources = {}
        for module in modules:
            if not module["path"].endswith(".py"):
                continue
            try:
                py_sources[module["path"]] = (repo_path / module["path"]).read_text(
                    encoding="utf-8", errors="ignore"
                )
            except OSError:
                continue
        token_index = _dotted_string_token_index(py_sources)
        still_unreachable = []
        for entry in unreachable_modules:
            if entry["path"].endswith(".py") and _referenced_by_dotted_string(entry["path"], token_index):
                entry_points_detected.append(entry["path"])
            else:
                still_unreachable.append(entry)
        unreachable_modules = still_unreachable

    imported_roots = _import_roots(modules)
    unused_dependencies = []
    for name, _version, ecosystem in _parse_pip_pins(repo_path) + _parse_npm_direct_pins(repo_path):
        # Static import-name matching is intentionally conservative. Some packages expose
        # different import roots than their package names; known common aliases live above.
        if imported_roots.isdisjoint(_package_import_names(name)):
            unused_dependencies.append({"ecosystem": ecosystem, "package": name})

    return {
        "unreachable_modules": unreachable_modules,
        "unused_dependencies": unused_dependencies,
        "entry_points_detected": sorted(entry_points_detected),
    }
