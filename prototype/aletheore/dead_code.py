import re
from pathlib import Path

from aletheore.vulnerabilities import _parse_npm_pins, _parse_pip_pins

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


def _is_entry_point(path: str, custom_entry_points: set[str]) -> bool:
    if path in custom_entry_points:
        return True
    return path.rsplit("/", 1)[-1] in ENTRY_POINT_FILENAMES


def _is_test_file(path: str) -> bool:
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


def find_dead_code(repo_path: Path, modules: list[dict], config: dict | None) -> dict:
    custom_entry_points = set()
    if isinstance(config, dict):
        raw_entry_points = config.get("dead_code_entry_points", [])
        if isinstance(raw_entry_points, list):
            custom_entry_points = {path for path in raw_entry_points if isinstance(path, str)}

    unreachable_modules = []
    entry_points_detected = []
    for module in modules:
        path = module["path"]
        if _is_entry_point(path, custom_entry_points):
            entry_points_detected.append(path)
            continue
        if _is_test_file(path):
            continue
        if not module.get("imported_by", []):
            unreachable_modules.append(
                {"path": path, "reason": "no other module imports this file"}
            )

    imported_roots = _import_roots(modules)
    unused_dependencies = []
    for name, _version, ecosystem in _parse_pip_pins(repo_path) + _parse_npm_pins(repo_path):
        # Static import-name matching is intentionally conservative. Some packages expose
        # different import roots than their package names; known common aliases live above.
        if imported_roots.isdisjoint(_package_import_names(name)):
            unused_dependencies.append({"ecosystem": ecosystem, "package": name})

    return {
        "unreachable_modules": unreachable_modules,
        "unused_dependencies": unused_dependencies,
        "entry_points_detected": sorted(entry_points_detected),
    }
