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
