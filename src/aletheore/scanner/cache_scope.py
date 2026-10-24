"""Which cached modules a git diff can have made stale.

A cached module keeps the import list resolved the last time its own file was parsed. Those imports were resolved
against OTHER files, so they can go stale without the file itself changing: a C# type moves to another file, a
Java package gains a class, a PHP class is renamed, a file the module imported is deleted.

The local scan cache handles this with per-language fingerprints (see graph._compute_resolution_signatures). The
hosted worker rebuilds its cache from database rows and has no fingerprints, but it does have the git diff between
the last synced commit and the current one. This module turns that diff into the set of languages whose cached
modules must be parsed again. It errs toward re-parsing: a wrong answer here costs a little scan time, never a
wrong edge. Pure functions, standard library only, so the worker can import it cheaply.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import PurePosixPath

# extension -> module language, the same values as scanner.graph.LANGUAGE_BY_EXTENSION (a test keeps them equal)
LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin", ".kts": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".h": "cpp", ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp", ".cxx": "cpp", ".cpp": "cpp", ".cc": "cpp",
    ".cs": "csharp",
    ".swift": "swift",
}

# Languages that resolve each other's files (a Java file imports Kotlin, a header belongs to C or C++, TypeScript
# imports JavaScript), so one changing invalidates the family.
_FAMILIES: tuple[frozenset[str], ...] = (
    frozenset({"javascript", "typescript"}),
    frozenset({"java", "kotlin"}),
    frozenset({"c", "cpp"}),
)

# Languages whose imports are resolved against what other files DECLARE (types, namespaces, classes, modules), not
# only against which files exist. Any change to a file of these languages can alter another file's edges.
_DECLARATION_LANGUAGES = frozenset({"java", "kotlin", "csharp", "php", "ruby", "c", "cpp", "rust", "go", "swift"})

# Build and package files that change how a language resolves imports.
_CONFIG_FILES: dict[str, frozenset[str]] = {
    "python": frozenset({"pyproject.toml", "setup.py", "setup.cfg"}),
    "javascript": frozenset({"package.json", "pnpm-workspace.yaml", "lerna.json", "deno.json", "deno.jsonc"}),
    "typescript": frozenset({"package.json", "pnpm-workspace.yaml", "lerna.json", "deno.json", "deno.jsonc"}),
    "go": frozenset({"go.mod", "go.work"}),
    "rust": frozenset({"Cargo.toml"}),
    "java": frozenset({"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"}),
    "kotlin": frozenset({"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"}),
    "csharp": frozenset({"Directory.Build.props", "Directory.Packages.props", "global.json"}),
    "php": frozenset({"composer.json"}),
    "ruby": frozenset({"Gemfile"}),
    "c": frozenset({"CMakeLists.txt", "Makefile", "meson.build"}),
    "cpp": frozenset({"CMakeLists.txt", "Makefile", "meson.build"}),
    "swift": frozenset({"Package.swift"}),
}
_CONFIG_SUFFIXES: dict[str, tuple[str, ...]] = {
    "csharp": (".csproj", ".sln", ".props", ".targets"),
    "typescript": (),
    "javascript": (),
    "ruby": (".gemspec",),
}
_TSCONFIG_PREFIXES = ("tsconfig", "jsconfig")

# These decide which files the scan sees at all, so a change to one can make any cached module wrong.
_REPO_WIDE_FILES = frozenset({".gitignore", ".gitattributes", ".gitmodules", ".aletheore.json"})

ALL_LANGUAGES = frozenset(LANGUAGE_BY_SUFFIX.values())


def _family(language: str) -> frozenset[str]:
    for family in _FAMILIES:
        if language in family:
            return family
    return frozenset({language})


_GIT_ESCAPES = {"n": 10, "t": 9, '"': 34, "\\": 92, "a": 7, "b": 8, "f": 12, "r": 13, "v": 11}


def _unquote_git_path(path: str) -> str:
    """Git prints a path with non-ASCII or special characters as a C-style quoted string ("src/\\316\\bb.py");
    return the real path. An unquoted path is returned as is."""
    if len(path) < 2 or not (path.startswith('"') and path.endswith('"')):
        return path
    body, out, i = path[1:-1], bytearray(), 0
    while i < len(body):
        ch = body[i]
        if ch != "\\" or i + 1 >= len(body):
            out += ch.encode("utf-8")
            i += 1
        elif body[i + 1] in "01234567":
            j = i + 1
            while j < min(i + 4, len(body)) and body[j] in "01234567":
                j += 1
            out.append(int(body[i + 1 : j], 8) & 0xFF)
            i = j
        else:
            out.append(_GIT_ESCAPES.get(body[i + 1], ord(body[i + 1])))
            i += 2
    return out.decode("utf-8", errors="replace")


def parse_name_status(text: str) -> list[tuple[str, str]]:
    """(status letter, path) pairs from `git diff --name-status --no-renames`; a rename or copy is not expected
    with that flag, but if one appears both paths are returned so nothing is missed. Quoted paths are unquoted."""
    changes: list[tuple[str, str]] = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 2 or not parts[0]:
            continue
        parts = [parts[0], *(_unquote_git_path(p) for p in parts[1:])]
        status = parts[0][0]
        if status in ("R", "C") and len(parts) >= 3:
            changes.append(("D", parts[1]))
            changes.append(("A", parts[2]))
        else:
            changes.append((status, parts[-1]))
    return changes


def languages_to_reextract(changes: Iterable[tuple[str, str]]) -> frozenset[str]:
    """Languages whose cached modules must be parsed again after these changes.

    - A changed file of a language that resolves against declarations (Java, C#, PHP, Ruby, C/C++, Rust, Go,
      Kotlin, Swift) invalidates that language: its types and names may have moved.
    - An added, deleted or type-changed file invalidates its language in every case, because other files' imports
      depend on whether the file exists (Python and JS/TS are only affected this way).
    - A changed build or package file (go.mod, package.json, tsconfig.json, composer.json, *.csproj ...)
      invalidates the languages it configures.
    - A change to a file that decides what the scan sees (.gitignore, .aletheore.json) invalidates everything.
    """
    stale: set[str] = set()
    for status, raw_path in changes:
        path = PurePosixPath(raw_path)
        name = path.name
        if name in _REPO_WIDE_FILES:
            return ALL_LANGUAGES
        language = LANGUAGE_BY_SUFFIX.get(path.suffix)
        if language is not None and (status != "M" or language in _DECLARATION_LANGUAGES):
            stale |= _family(language)
        for config_language, names in _CONFIG_FILES.items():
            is_config = name in names or name.endswith(_CONFIG_SUFFIXES.get(config_language, ()))
            if config_language in ("javascript", "typescript") and name.startswith(_TSCONFIG_PREFIXES) and name.endswith(".json"):
                is_config = True
            if is_config:
                stale |= _family(config_language)
    return frozenset(stale)
