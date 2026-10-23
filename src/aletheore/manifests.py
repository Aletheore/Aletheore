"""Dependency manifests at any depth of a repository.

The vulnerability check, the licence check, the unused-dependency check and
monorepo detection used to read manifests at the repository root only, so a
`web/package.json` or a `services/api/requirements.txt` was invisible to them.
This module finds every directory that holds a manifest, skipping vendored,
build, dependency and ignored paths, so each check can run its existing
per-directory parsers on each of them.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from aletheore.repo_config import is_ignored, load_repo_config
from aletheore.scanner.detect import IGNORED_DIRS, _nested_git_roots, git_visible_paths

# Exact file names the dependency parsers read (a directory holding any of
# them is a manifest directory), plus the two suffix families.
MANIFEST_FILENAMES = frozenset(
    {
        "package.json", "package-lock.json",
        "requirements.txt", "pyproject.toml",
        "go.mod",
        "Cargo.toml", "Cargo.lock",
        "pom.xml",
        "Gemfile", "Gemfile.lock",
        "composer.json", "composer.lock",
        "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
        "Package.resolved",
        "Directory.Packages.props", "packages.lock.json",
    }
)
MANIFEST_SUFFIXES = (".csproj", ".gemspec")

# "Project" manifests: ones that declare a package/project of their own (a
# lock file or a settings file alone does not make a package root).
_PROJECT_MANIFESTS = frozenset(
    {
        "package.json", "requirements.txt", "pyproject.toml", "go.mod", "Cargo.toml", "pom.xml",
        "Gemfile", "composer.json", "build.gradle", "build.gradle.kts",
    }
)
_ECOSYSTEM_BY_MANIFEST = {
    "package.json": "npm", "requirements.txt": "PyPI", "pyproject.toml": "PyPI",
    "go.mod": "Go", "Cargo.toml": "crates.io", "pom.xml": "Maven", "Gemfile": "RubyGems",
    "composer.json": "Packagist", "build.gradle": "Maven", "build.gradle.kts": "Maven",
}

# Directories whose manifests are never the project's own dependencies, on top
# of the scanner-wide IGNORED_DIRS (node_modules, dist, build, .venv, ...).
EXTRA_SKIPPED_DIRS = frozenset(
    {
        "vendor", "vendors", "third_party", "third-party", "thirdparty", "Pods", "target",
        "bower_components", "site-packages", "testdata", "fixtures", "__fixtures__",
        "node_modules", "Carthage",
    }
)

# A repository with more manifest directories than this (a vendored mirror of
# hundreds of packages, a generated tree) is capped: the shallowest ones are
# kept and the cap is reported, rather than parsing thousands of manifests.
MAX_MANIFEST_DIRS = 200


@lru_cache(maxsize=32)
def _discover(repo_path: Path, ignored_key: tuple[str, ...]) -> dict:
    patterns = list(ignored_key)
    visible = git_visible_paths(repo_path)
    found: dict[str, set[str]] = {}

    def note(rel_dir: str, name: str) -> None:
        if name in MANIFEST_FILENAMES or name.endswith(MANIFEST_SUFFIXES):
            found.setdefault(rel_dir, set()).add(name)

    def dir_skipped(rel_dir_parts: list[str]) -> bool:
        return any(part in IGNORED_DIRS or part in EXTRA_SKIPPED_DIRS for part in rel_dir_parts)

    if visible is not None:
        for rel in visible[0]:
            parent, _, name = rel.rpartition("/")
            if not (name in MANIFEST_FILENAMES or name.endswith(MANIFEST_SUFFIXES)):
                continue
            if parent and dir_skipped(parent.split("/")):
                continue
            if is_ignored(rel, patterns):
                continue
            note(parent or ".", name)
    else:
        nested = [str(root) for root in _nested_git_roots(repo_path)]
        root_str = str(repo_path)
        for dirpath, dirnames, filenames in os.walk(repo_path, followlinks=False):
            rel_dir = "." if dirpath == root_str else Path(dirpath).relative_to(repo_path).as_posix()
            dirnames[:] = [
                d for d in dirnames
                if d not in IGNORED_DIRS and d not in EXTRA_SKIPPED_DIRS
                and not is_ignored(f"{rel_dir}/{d}" if rel_dir != "." else d, patterns)
            ]
            if any(dirpath == r or dirpath.startswith(r + os.sep) for r in nested):
                dirnames[:] = []
                continue
            for filename in filenames:
                if is_ignored(f"{rel_dir}/{filename}" if rel_dir != "." else filename, patterns):
                    continue
                note(rel_dir, filename)

    dirs = sorted(found, key=lambda d: ((0, "") if d == "." else (1 + d.count("/"), d)))
    total = len(dirs)
    truncated = total > MAX_MANIFEST_DIRS
    if truncated:
        dirs = dirs[:MAX_MANIFEST_DIRS]
    return {
        "dirs": dirs,
        "files": {d: sorted(found[d]) for d in dirs},
        "total": total,
        "truncated": truncated,
    }


def discover_manifests(repo_path: Path, ignored_paths: list[str] | None = None) -> dict:
    """{"dirs": [repo-relative dir, "." for the root, shallowest first],
    "files": {dir: [manifest file names]}, "total": int, "truncated": bool}.

    `ignored_paths` defaults to the repository's own .aletheore.json setting.
    """
    repo_path = Path(repo_path)
    if ignored_paths is None:
        ignored_paths = load_repo_config(repo_path)["ignored_paths"]
    return _discover(repo_path, tuple(ignored_paths))


def manifest_dir_paths(repo_path: Path, ignored_paths: list[str] | None = None) -> list[tuple[str, Path]]:
    """[(relative dir, absolute dir)] with the repository root always first, even
    when it holds no manifest (the existing root-level behaviour is unchanged)."""
    repo_path = Path(repo_path)
    dirs = list(discover_manifests(repo_path, ignored_paths)["dirs"])
    if "." not in dirs:
        dirs.insert(0, ".")
    return [(d, repo_path if d == "." else repo_path / d) for d in dirs]


def clear_manifest_cache() -> None:
    _discover.cache_clear()


def manifest_ecosystems(file_names: list[str]) -> list[str]:
    seen: list[str] = []
    for name in file_names:
        eco = _ECOSYSTEM_BY_MANIFEST.get(name)
        if eco is None and name.endswith(".csproj"):
            eco = "NuGet"
        if eco is None and name.endswith(".gemspec"):
            eco = "RubyGems"
        if eco and eco not in seen:
            seen.append(eco)
    return seen


def is_project_manifest(name: str) -> bool:
    return name in _PROJECT_MANIFESTS or name.endswith((".csproj", ".gemspec"))
