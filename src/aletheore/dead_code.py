import re
import xml.etree.ElementTree as ET
from pathlib import Path

from aletheore.repo_config import is_ignored
from aletheore.scanner.detect import IGNORED_DIRS
from aletheore.scanner.graph import _infer_swift_targets
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
    # SwiftPM's build manifest - always this exact name, read by the swift
    # toolchain itself, never imported by the repo's own application code.
    "Package.swift",
    # Swift's classic top-level-code entry point (predates the @main
    # attribute, still standard in Vapor's own project template): the one
    # file in a target the compiler allows top-level executable statements
    # in, by both language rule and universal convention its entry point.
    # Confirmed on a real repo (vapor/api-template): Sources/Run/main.swift.
    "main.swift",
}

TEST_PATH_PATTERNS = [
    re.compile(r"(^|/)test_[^/]+\.py$"),
    re.compile(r"(^|/)[^/]+_test\.py$"),
    re.compile(r"(^|/)[^/]+\.test\.[jt]sx?$"),
    re.compile(r"(^|/)[^/]+\.spec\.[jt]sx?$"),
    # Case-insensitive: SwiftPM/Xcode universally capitalize this directory
    # ("Tests/") - confirmed against a real repo (apple/swift-algorithms),
    # where the previous case-sensitive version missed every single test
    # file, flagging them all as dead code.
    re.compile(r"(^|/)(tests?|__tests__)/", re.IGNORECASE),
    # JVM (Java/Kotlin) PascalCase suffix convention - e.g. TaskDaoTest.kt,
    # StatisticsScreenTest.kt. Confirmed against a real repo
    # (android/architecture-samples): without this, every androidTest file
    # flagged as dead code purely because JUnit/instrumentation invokes them
    # by reflection, never a plain import.
    re.compile(r"(^|/)[^/]+Test\.(kt|kts|java)$"),
    # Gradle's androidTest/test source-set convention - doesn't require the
    # PascalCase suffix above (e.g. a test helper/fixture file), and
    # "androidTest" isn't matched by the tests?/__tests__ pattern above
    # since it's one fused word, not "test" as its own path segment.
    re.compile(r"(^|/)(androidTest|test)/.+\.(kt|kts|java)$"),
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

# A Swift @main type (an AWS Lambda handler, a CLI's entry struct, ...) is
# invoked by the runtime, never imported by another file - same category as
# Python's __main__ guard above. Always at file scope, so a start-of-line
# anchor (allowing leading whitespace) is enough without a full parse.
_SWIFT_MAIN_ATTRIBUTE_PATTERN = re.compile(r"^\s*@main\b", re.MULTILINE)

# Hilt/Dagger annotations that mark a Kotlin/Java class as wired into the DI
# graph by an annotation processor rather than a plain import - confirmed on
# a real repo (android/architecture-samples): @HiltAndroidApp's Application
# subclass and @HiltViewModel's ViewModels are both instantiated by
# generated Hilt code, never imported by name anywhere in the app's own
# source. This is deliberately a narrow, scoped signal, not general DI-graph
# resolution (which would require following @Binds/@Provides wiring across
# arbitrarily many files) - the same bounded-heuristic category as the
# __main__ guard and @main checks above, not a claim of full DI awareness.
# @Module alone is too weak on its own (a project's own unrelated "Module"
# concept could reuse the bare name) - real Hilt/Dagger modules always pair
# it with @InstallIn (or, for a test-only module that replaces a production
# one, @TestInstallIn - confirmed against a real repo, where every @Module
# in the shared-test source set uses @TestInstallIn instead), confirmed
# against every @Module in this same repo.
_HILT_ANDROID_APP_PATTERN = re.compile(r"^\s*@HiltAndroidApp\b", re.MULTILINE)
_HILT_VIEWMODEL_PATTERN = re.compile(r"^\s*@HiltViewModel\b", re.MULTILINE)
_DAGGER_MODULE_PATTERN = re.compile(r"^\s*@Module\b", re.MULTILINE)
_DAGGER_INSTALL_IN_PATTERN = re.compile(r"^\s*@(?:InstallIn|TestInstallIn)\b", re.MULTILINE)

# A plain regex read, not a tree-sitter parse - consistent with every other
# content check in this file (main guard, @main, Hilt/Dagger above), and
# graph.py's own _kotlin_package already needs a full parsed tree it has no
# reason to hand this module just for one line of source.
_KOTLIN_PACKAGE_PATTERN = re.compile(r"^\s*package\s+([\w.]+)", re.MULTILINE)

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
    # "pkg.mod.func" -> {"pkg", "pkg.mod"} - every STRICT prefix, i.e.
    # shorter than the full token. Each one is immediately followed by a
    # literal '.' inside the captured run itself, which is always a valid
    # boundary per the old regex's `(?=[.\'"])` lookahead, regardless of
    # what comes after the run in the source. The full token is deliberately
    # excluded here: its validity depends on what character follows the run
    # in the source (only a closing quote counts - see
    # _dotted_string_token_index), which this function has no access to.
    parts = token.split(".")
    return {".".join(parts[:k]) for k in range(1, len(parts))}


def _dotted_string_token_index(sources: dict[str, str]) -> dict[str, set[str]]:
    """dotted-string token -> set of file paths whose content references it
    (quote-adjacent, at a '.'-or-closing-quote boundary) - built once for
    the whole corpus, replacing what used to be a fresh regex scan of every
    file per candidate. O(total source size) instead of
    O(candidates x total source size).

    _QUOTED_WORD_DOT_RUN_RE greedily consumes every '.' as part of the run,
    so the run can never stop right before a '.' - it only stops at a
    non-word-non-dot character (or end of string). That means the full
    captured token's closing boundary is never a '.': it has to be checked
    against the literal next character in the source, and only a quote
    character satisfies the old regex's [.\'"] boundary class here. Without
    this check, a quoted string like "pkg.mod completed successfully" would
    wrongly register "pkg.mod" as referenced - the old regex required the
    next character to be '.', "'", or '"', and a space is none of those.
    Confirmed as a real divergence (not hypothetical) by direct comparison
    against the old per-candidate regex on that exact string.
    """
    index: dict[str, set[str]] = {}
    for path, content in sources.items():
        for match in _QUOTED_WORD_DOT_RUN_RE.finditer(content):
            token = match.group(1)
            for prefix in _dot_boundary_prefixes(token):
                index.setdefault(prefix, set()).add(path)
            next_char = content[match.end() : match.end() + 1]
            if next_char in ("'", '"'):
                index.setdefault(token, set()).add(path)
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


def _has_swift_main_attribute(repo_path: Path, path: str) -> bool:
    if not path.endswith(".swift"):
        return False
    try:
        content = (repo_path / path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return bool(_SWIFT_MAIN_ATTRIBUTE_PATTERN.search(content))


def _has_hilt_dagger_annotation(repo_path: Path, path: str) -> bool:
    if not path.endswith((".kt", ".kts", ".java")):
        return False
    try:
        content = (repo_path / path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    if _HILT_ANDROID_APP_PATTERN.search(content) or _HILT_VIEWMODEL_PATTERN.search(content):
        return True
    return bool(_DAGGER_MODULE_PATTERN.search(content) and _DAGGER_INSTALL_IN_PATTERN.search(content))


_ANDROID_NAME_ATTR = "{http://schemas.android.com/apk/res/android}name"
# Only components an app declares by class name for the OS to instantiate
# via reflection - deliberately excludes <action>/<category> (also carry
# android:name, but name Intent actions like "android.intent.action.MAIN",
# never a class) and <activity-alias> (its own android:name is the alias's
# component name, not a real class - the aliased class is either its
# separate android:targetActivity, out of this bounded heuristic's scope,
# or already covered by its own <activity> declaration elsewhere).
_ANDROID_MANIFEST_ENTRY_TAGS = {"application", "activity", "service", "receiver", "provider"}


def _android_manifest_entry_points(repo_path: Path, ignored_paths: list[str] | None = None) -> set[str]:
    """Component classes named in AndroidManifest.xml - instantiated by the
    Android OS via reflection from this XML, never a plain Kotlin/Java
    import. Confirmed on a real repo (android/architecture-samples):
    TodoApplication.kt (referenced only by
    <application android:name=".TodoApplication">) and TodoActivity.kt (the
    launcher activity, referenced only by
    <activity android:name="...TodoActivity"> with a MAIN/LAUNCHER
    intent-filter) both looked completely unreachable without this.

    Resolved the same way _infer_xcodeproj_swift_targets resolves an Xcode
    target's file membership: basename search under the repo root, keeping
    only an unambiguous single match. A manifest entry names a class, not a
    file path (and android:name's shorthand form, ".TodoApplication", isn't
    even a full class name) - reconstructing a path from it would have to
    guess which of app/src/main/java/, .../kotlin/, or a build-flavor
    source set actually holds the file, so this takes just the simple class
    name (the segment after the last '.') and lets the basename search
    handle the rest, same as the Xcode case.
    """
    entry_points: set[str] = set()
    patterns = ignored_paths or []
    for manifest_path in repo_path.rglob("AndroidManifest.xml"):
        rel_manifest = manifest_path.relative_to(repo_path).as_posix()
        if is_ignored(rel_manifest, patterns):
            continue
        try:
            root = ET.parse(manifest_path).getroot()
        except (ET.ParseError, OSError):
            continue
        for element in root.iter():
            tag = element.tag.rsplit("}", 1)[-1]
            if tag not in _ANDROID_MANIFEST_ENTRY_TAGS:
                continue
            qualified_name = element.attrib.get(_ANDROID_NAME_ATTR)
            if not qualified_name:
                continue
            simple_name = qualified_name.rsplit(".", 1)[-1]
            if not simple_name:
                continue
            candidates = [
                p for p in repo_path.rglob(f"{simple_name}.kt")
                if not is_ignored(p.relative_to(repo_path).as_posix(), patterns)
            ] or [
                p for p in repo_path.rglob(f"{simple_name}.java")
                if not is_ignored(p.relative_to(repo_path).as_posix(), patterns)
            ]
            if len(candidates) == 1:
                entry_points.add(candidates[0].relative_to(repo_path).as_posix())
    return entry_points


def _kotlin_package_of(repo_path: Path, path: str) -> str | None:
    if not path.endswith((".kt", ".kts")):
        return None
    try:
        content = (repo_path / path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    match = _KOTLIN_PACKAGE_PATTERN.search(content)
    return match.group(1) if match else None


def _kotlin_package_reachable_files(
    repo_path: Path,
    modules: list[dict],
    android_manifest_entry_points: set[str],
) -> set[str]:
    """Every Kotlin file sharing a package with a file that's reachable
    some other way - the same blind spot _swift_target_reachable_files
    above already handles for Swift's whole-target visibility, just at
    package instead of target granularity.

    Kotlin files in the same package see each other's top-level
    declarations (classes, objects, and - real repo confirmed - top-level
    functions) with no import statement at all, unlike every other
    supported language here. Confirmed on a real repo
    (android/architecture-samples): DefaultTaskRepository.kt calls
    ModelMappingExt.kt's toExternal() with zero import statement - both
    declared in the same package, and DefaultTaskRepository.kt is
    independently reachable (bound into Hilt's DI graph via
    DataModules.kt). StatisticsUtils.kt is the same shape one package
    over: unreachable itself, but sharing a package with the
    @HiltViewModel-annotated StatisticsViewModel.kt.

    A package's own reachability is judged by the same signals
    find_dead_code already treats as reachable on their own (imported_by,
    a manifest entry point, or a Hilt/Dagger annotation) - propagating
    from an already-independently-reachable sibling is the whole point,
    so requiring anything more here would just miss the real cases above.
    Test files are excluded from the grouping entirely (not just left to
    fall through) so a test file's own package-mate status can never leak
    reachability into a production sibling that happens to share its
    package name, which Android's own androidTest/test convention often
    does.
    """
    packages: dict[str, list[str]] = {}
    for module in modules:
        path = module["path"]
        if is_test_file(path):
            continue
        package = _kotlin_package_of(repo_path, path)
        if package is not None:
            packages.setdefault(package, []).append(path)

    modules_by_path = {module["path"]: module for module in modules}
    reachable_files: set[str] = set()
    for paths in packages.values():
        if len(paths) < 2:
            continue
        package_is_reachable = any(
            modules_by_path.get(path, {}).get("imported_by")
            or path in android_manifest_entry_points
            or _has_hilt_dagger_annotation(repo_path, path)
            for path in paths
        )
        if package_is_reachable:
            reachable_files.update(paths)
    return reachable_files


def _swift_target_reachable_files(
    repo_path: Path,
    modules: list[dict],
    ignored_paths: list[str] | None,
) -> set[str]:
    """Every Swift file belonging to a target that's reachable some other
    way (imported from another target, or containing a @main entry point).

    Swift files within one target see each other with no import statement
    at all - that's how Swift's compilation model works, unlike every other
    language here - so the per-file import graph can never show intra-
    target edges no matter how well cross-target import resolution works.
    Confirmed on a real repo (vapor/penny-bot): a target's @main handler
    file imported by nothing outside it, alongside sibling files (a
    repository/service layer) the handler itself references with no
    import, both looked equally unreachable before this.
    """
    swift_targets = _infer_swift_targets(repo_path, ignored_paths)
    if not swift_targets:
        return set()

    modules_by_path = {module["path"]: module for module in modules}
    reachable_files: set[str] = set()
    for name, files in swift_targets.items():
        rel_paths = [
            file_path.relative_to(repo_path).as_posix() if file_path.is_absolute() else file_path.as_posix()
            for file_path in files
        ]
        target_is_reachable = any(
            modules_by_path.get(rel, {}).get("imported_by") or _has_swift_main_attribute(repo_path, rel)
            for rel in rel_paths
        )
        if target_is_reachable:
            reachable_files.update(rel_paths)
    return reachable_files


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
    android_manifest_entry_points = _android_manifest_entry_points(repo_path, ignored_paths)
    kotlin_package_reachable_files = _kotlin_package_reachable_files(
        repo_path, modules, android_manifest_entry_points
    )
    swift_reachable_files = _swift_target_reachable_files(repo_path, modules, ignored_paths)

    unreachable_modules = []
    entry_points_detected = []
    for module in modules:
        path = module["path"]
        if _is_entry_point(path, custom_entry_points):
            entry_points_detected.append(path)
            continue
        if is_test_file(path):
            continue
        if path in swift_reachable_files or path in kotlin_package_reachable_files:
            continue
        if not module.get("imported_by", []):
            if (
                path in html_script_entry_points
                or path in android_manifest_entry_points
                or _has_main_guard(repo_path, path)
                or _has_hilt_dagger_annotation(repo_path, path)
            ):
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
