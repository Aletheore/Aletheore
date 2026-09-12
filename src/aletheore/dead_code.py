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
    # Rails' own router loads this file by convention (config/routes.rb) -
    # nothing in the app ever requires it, same category as manage.py/wsgi.py
    # above. An unambiguous, Rails-specific basename, unlike Laravel's
    # routes/web.php or routes/api.php (generic enough names elsewhere that
    # matching on basename alone risks false negatives), so only this one
    # is added here.
    "routes.rb",
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

# Compiled-language entry points, same category as the Python __main__ guard
# and Swift @main above: each language's real, unambiguous "the runtime
# invokes this, no import ever will" convention. Confirmed empirically on
# each language's own module-graph resolution (build_module_graph correctly
# resolves ordinary intra-repo imports for all four; a file matching one of
# these is specifically the one every real deploy invokes directly, which by
# definition nothing else in the repo imports): a Go package main's func
# main(), Rust's src/main.rs or any file with a top-level fn main(), a Java
# class's public static void main(String[] args), and a C# Main method - all
# looked completely unreachable without this, on every real single-binary
# repo in these four languages, not a hypothetical shape.
_GO_PACKAGE_MAIN_PATTERN = re.compile(r"^\s*package\s+main\b", re.MULTILINE)
_GO_FUNC_MAIN_PATTERN = re.compile(r"^\s*func\s+main\s*\(", re.MULTILINE)
# Anchored to column 0 (no leading whitespace), not `^\s*` - a real
# crate-root fn main() is always unindented; requiring that excludes a
# nested `mod tests { fn main() {} }`, which rustfmt always indents, from
# matching as if it were the crate's real entry point.
_RUST_FN_MAIN_PATTERN = re.compile(r"^(?:pub\s+)?(?:async\s+)?fn\s+main\s*\(", re.MULTILINE)
# Modifiers can appear in any order and any legal Java main method can carry
# extras beyond public/static (final, synchronized, strictfp) - e.g. `public
# final static void main(...)` or `public static synchronized void main(...)`.
# Two same-line lookaheads (public and static each appear somewhere before
# void main() on this line) rather than one fixed-order sequence, so real
# modifier combinations aren't missed - but still scoped to one line/
# statement, not "anywhere in the file", for the same reason the C# check
# below requires static and Main to co-occur on one declaration.
_JAVA_MAIN_METHOD_PATTERN = re.compile(
    r"^\s*(?=[^;{}\n]*\bpublic\b)(?=[^;{}\n]*\bstatic\b)[\w\s]*\bvoid\s+main\s*\(",
    re.MULTILINE,
)
# C#'s Main can carry any modifier order/return type (void/int/Task/Task<int>).
# Scoped to one line via a lookahead requiring `static` before `Main(` on the
# same statement - not two independent whole-file searches, which would also
# match a file containing an unrelated static helper elsewhere plus a
# separate instance `void Main()`.
_CSHARP_MAIN_METHOD_PATTERN = re.compile(
    r"^\s*(?=[^;{}\n]*\bstatic\b)[\w\s<>]*\bMain\s*\(", re.MULTILINE
)

# Spring (Boot/MVC) stereotype annotations mark a class for classpath
# component-scanning (@ComponentScan/@SpringBootApplication find it by
# scanning .class files at startup), never a plain Java/Kotlin import -
# same category as the Hilt/Dagger annotations above, just Spring's own
# DI container instead of Android's. Confirmed empirically: a synthetic
# @RestController with a single @GetMapping method and zero references
# anywhere else in the repo looked completely unreachable without this.
# Requiring a same-file org.springframework import alongside the bare
# annotation name (mirroring the Dagger @Module + @InstallIn pairing
# above) avoids matching an unrelated project-defined @Service/@Component
# of the same name that has nothing to do with Spring.
_SPRINGFRAMEWORK_IMPORT_PATTERN = re.compile(r"^\s*import\s+org\.springframework\b", re.MULTILINE)
_SPRING_STEREOTYPE_ANNOTATION_PATTERN = re.compile(
    r"^\s*@(?:RestController|Controller|Service|Repository|Component|Configuration|SpringBootApplication)\b",
    re.MULTILINE,
)

# ASP.NET Core discovers MVC controllers by assembly scanning
# (services.AddControllers() at startup finds every ControllerBase
# subclass/[ApiController]-attributed class), never a plain C# reference -
# same "framework-owned reflection/scanning" category as Spring above.
# Requiring a same-file Microsoft.AspNetCore.Mvc import alongside the
# attribute/base-class signal avoids matching an unrelated project-defined
# "Controller" base class that has nothing to do with ASP.NET Core.
_ASPNETCORE_MVC_IMPORT_PATTERN = re.compile(r"^\s*using\s+Microsoft\.AspNetCore\.Mvc\b", re.MULTILINE)
_ASPNET_API_CONTROLLER_ATTRIBUTE_PATTERN = re.compile(r"^\s*\[ApiController\]", re.MULTILINE)
_ASPNET_CONTROLLER_BASE_CLASS_PATTERN = re.compile(
    r"\bclass\s+\w+\s*:\s*(?:[\w.]+\.)?(?:Controller|ControllerBase)\b"
)

_HTML_SCRIPT_SRC_PATTERN = re.compile(r'<script[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)

# A module dispatched by dotted-string name (RQ's queue.enqueue("pkg.mod.func", ...),
# Celery task names, cron-style job registries) is never imported by another module
# either - the same blind spot as the __main__-guard case above, just for library-level
# dynamic dispatch instead of direct script invocation. Confirmed on this repo:
# scan_worker/jobs.py is one of the busiest modules in the
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


# A bare CamelCase constant reference, optionally namespaced with `::`
# (User, Admin::UserHistory), and optionally anchored with a LEADING `::`
# (::Jobs::TopicTimerBase - Ruby's own "start lookup at the absolute
# top-level namespace" syntax, real and common enough that a superclass
# reference is routinely written this way to disambiguate against a
# same-named nested constant). The leading `(?:::)?` is consumed but not
# captured - group(1) is always just the constant name itself, with no
# leading colons in the stored token.
#
# Real bug found via a real Discourse scan (68,183-commit clone): every
# one of its own job-hierarchy superclass references
# (`class CloseTopic < ::Jobs::TopicTimerBase`) uses this exact leading-
# `::` form, and the OLD regex's `(?<![:\w])` lookbehind rejected a match
# starting right after the leading `::`'s own second colon - not just for
# the whole "Jobs::TopicTimerBase" run, but for EVERY position inside it
# (including a later attempt at the bare "TopicTimerBase" tail, since
# that position is also colon-preceded) - so a real superclass reference
# written this way was completely invisible to this index, not just
# imprecisely captured. The lookbehind's only remaining job is
# preventing a match from starting mid-identifier (`_word` after a
# `.`/etc.) - `(?<!\w)` alone still does that; dropping `:` from it does
# not reopen the "spurious standalone User inside Admin::User" case this
# was originally written to prevent, since finditer's greedy, non-
# overlapping scan already consumes "Admin::User" as one run before
# ever revisiting "User" on its own.
#
# Known, deliberate limitation: this is a plain text scan, not a real
# parse - a class name mentioned only inside a `#` comment or a string
# literal (a log message, an error string) counts as a "reference" the
# same as real code would. Consistent with this file's existing bias
# everywhere else (favor not flagging live code dead over precision), and
# a real parse for every unreachable file's own repo would cost far more
# than this rescue pass is worth - left as a known tradeoff, not silently.
_RUBY_CONSTANT_TOKEN_RE = re.compile(r"(?<!\w)(?:::)?([A-Z][A-Za-z0-9_]*(?:::[A-Z][A-Za-z0-9_]*)*)")
# Whether a token match sits right after `class`/`module` (its own
# declaration, e.g. "class WidgetsController" or "module Admin") rather
# than a genuine reference to it. Real bug caught by this file's own
# existing ambiguous-basename test: two unrelated plugins/engines each
# defining their own same-named WidgetsController falsely "referenced"
# each other, since each file's own declaration line contains its own
# class name as a token like any other use would - without excluding the
# declaration site, any two files sharing a bare name always looked
# mutually reachable regardless of whether anything actually used either.
_RUBY_DEFINITION_KEYWORD_RE = re.compile(r"\b(?:class|module)\s*$")


def _ruby_zeitwerk_constant_candidates(path: str) -> list[str]:
    """The (possibly namespaced) constant name(s) Zeitwerk - Rails' own
    autoloader, active by default since Rails 6 - would map this file to:
    app/models/admin/user_history.rb -> ["Admin::UserHistory",
    "UserHistory"]. Every immediate subdirectory of app/ is its own
    autoload root by Rails convention regardless of what it's named
    (models, jobs, mailers, services, serializers, channels, policies,
    ... - no fixed directory allowlist needed, unlike the framework-
    specific entry-point heuristics elsewhere in this file), so only
    app/<root>/... is handled - lib/ is autoloaded only when an app
    explicitly opts in via config.autoload_paths, which this module has no
    way to see.

    The full namespaced name is the precise match; the bare last segment
    is a deliberately permissive fallback for Ruby's own lexical constant
    lookup, where code already nested under (or sitting alongside) the
    same namespace commonly refers to a sibling by its short name alone
    (Ruby itself would resolve it the same way) - e.g. code inside
    `module Admin` referring to `UserHistory` without ever spelling out
    `Admin::`. This trades a narrow false-negative risk (an unrelated
    class elsewhere in the app happening to share the same bare name)
    for closing a false-positive rate that was 100% on every real Rails
    app/ subdirectory tested (Discourse: models, jobs, mailers, services,
    serializers) - consistent with this file's existing bias everywhere
    else toward not flagging live code dead over never missing a
    genuinely dead file.
    """
    parts = path.split("/")
    if len(parts) < 3 or parts[0] != "app" or not path.endswith(".rb"):
        return []
    segments = parts[2:]
    segments[-1] = segments[-1][: -len(".rb")]
    camelized = ["".join(word.capitalize() for word in segment.split("_")) for segment in segments if segment]
    if not camelized:
        return []
    full_name = "::".join(camelized)
    return [full_name] if len(camelized) == 1 else [full_name, camelized[-1]]


def _ruby_constant_token_index(sources: dict[str, str]) -> dict[str, set[str]]:
    """Constant token -> set of file paths whose content contains it as a
    genuine reference (excludes the token's own class/module declaration
    site - see _RUBY_DEFINITION_KEYWORD_RE) - built once for the whole
    corpus, same O(total source size) shape as _dotted_string_token_index
    above, for the same reason (avoids an O(candidates x total source
    size) rescan). The trailing-window slice before each match is
    bounded (not `content[:match.start()]`) so this stays O(1) per match
    rather than O(file size) per match."""
    index: dict[str, set[str]] = {}
    for path, content in sources.items():
        for match in _RUBY_CONSTANT_TOKEN_RE.finditer(content):
            # Window before the actual identifier (group 1), not before
            # an optional leading `::` - `class ::Foo` is not idiomatic
            # Ruby, but checking here rather than at match.start(0) keeps
            # this correct even in that unusual case.
            window = content[max(0, match.start(1) - 24) : match.start(1)]
            if _RUBY_DEFINITION_KEYWORD_RE.search(window):
                continue
            index.setdefault(match.group(1), set()).add(path)
    return index


def _referenced_by_ruby_constant(path: str, token_index: dict[str, set[str]]) -> bool:
    for candidate in _ruby_zeitwerk_constant_candidates(path):
        owners = token_index.get(candidate)
        if owners and owners - {path}:
            return True
    return False


# A bare symbol literal (:bump_topic) - deliberately NOT a `key:` keyword-
# argument label, which this shape excludes on its own since the colon
# there trails the identifier instead of leading it. Symbols are Ruby's
# own idiomatic way to name-then-camelize-and-constantize a class from a
# snake_case identifier - real, common code beyond any one framework
# (`"#{name}".classify.constantize`, an STI/polymorphic type column, a
# registry keyed by symbol) - not just Discourse's own `Jobs.enqueue
# (:bump_topic, ...)`, the case that surfaced this gap. Left as a
# genuinely separate index from _RUBY_CONSTANT_TOKEN_RE's, rather than
# merged into the same one, since a symbol literal is never itself a
# class/module declaration site the way a CamelCase token can be -
# keeping them apart avoids complicating that exclusion for no benefit.
_RUBY_SYMBOL_LITERAL_RE = re.compile(r"(?<![:\w]):([a-z_][a-z0-9_]*)\b")


def _ruby_symbol_token_index(sources: dict[str, str]) -> dict[str, set[str]]:
    """Camelized symbol literal -> set of file paths containing it
    (:bump_topic -> "BumpTopic") - same O(total source size) shape as
    _ruby_constant_token_index above."""
    index: dict[str, set[str]] = {}
    for path, content in sources.items():
        for match in _RUBY_SYMBOL_LITERAL_RE.finditer(content):
            camelized = "".join(word.capitalize() for word in match.group(1).split("_"))
            index.setdefault(camelized, set()).add(path)
    return index


def _referenced_by_ruby_symbol_dispatch(path: str, symbol_index: dict[str, set[str]]) -> bool:
    """Whether some other file's bare :snake_case symbol, camelized,
    names this file's own Zeitwerk constant - the real gap this closes:
    Discourse's own `Jobs.enqueue(:bump_topic, ...)` (app/jobs/regular/
    bump_topic.rb) is dispatched by symbol name, never a class reference
    anywhere, so _referenced_by_ruby_constant alone never rescues it.
    Confirmed on a real repo (discourse/discourse): 87 additional
    app/jobs files rescued beyond what the bare-constant check alone
    found, on top of the 20 it already caught."""
    for candidate in _ruby_zeitwerk_constant_candidates(path):
        owners = symbol_index.get(candidate)
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


def _has_go_main_function(repo_path: Path, path: str) -> bool:
    if not path.endswith(".go"):
        return False
    try:
        content = (repo_path / path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return bool(_GO_PACKAGE_MAIN_PATTERN.search(content) and _GO_FUNC_MAIN_PATTERN.search(content))


def _has_rust_main_function(repo_path: Path, path: str) -> bool:
    if not path.endswith(".rs"):
        return False
    try:
        content = (repo_path / path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return bool(_RUST_FN_MAIN_PATTERN.search(content))


def _has_java_main_method(repo_path: Path, path: str) -> bool:
    if not path.endswith(".java"):
        return False
    try:
        content = (repo_path / path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return bool(_JAVA_MAIN_METHOD_PATTERN.search(content))


def _has_csharp_main_method(repo_path: Path, path: str) -> bool:
    if not path.endswith(".cs"):
        return False
    try:
        content = (repo_path / path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return bool(_CSHARP_MAIN_METHOD_PATTERN.search(content))


def _has_spring_stereotype_annotation(repo_path: Path, path: str) -> bool:
    if not path.endswith((".java", ".kt", ".kts")):
        return False
    try:
        content = (repo_path / path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return bool(
        _SPRINGFRAMEWORK_IMPORT_PATTERN.search(content)
        and _SPRING_STEREOTYPE_ANNOTATION_PATTERN.search(content)
    )


def _has_aspnet_controller_convention(repo_path: Path, path: str) -> bool:
    if not path.endswith(".cs"):
        return False
    try:
        content = (repo_path / path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    if not _ASPNETCORE_MVC_IMPORT_PATTERN.search(content):
        return False
    return bool(
        _ASPNET_API_CONTROLLER_ATTRIBUTE_PATTERN.search(content)
        or _ASPNET_CONTROLLER_BASE_CLASS_PATTERN.search(content)
    )


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


def _jvm_package_of(repo_path: Path, path: str) -> str | None:
    if not path.endswith((".kt", ".kts", ".java")):
        return None
    try:
        content = (repo_path / path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    match = _KOTLIN_PACKAGE_PATTERN.search(content)
    return match.group(1) if match else None


def _jvm_package_reachable_files(
    repo_path: Path,
    modules: list[dict],
    android_manifest_entry_points: set[str],
) -> set[str]:
    """Every Kotlin or Java file sharing a package with a file that's
    reachable some other way - the same blind spot _swift_target_reachable_files
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

    Plain Java has the identical rule (JLS 7.5.1: no import needed for a
    same-package type) - confirmed directly: two ordinary .java files
    declaring the same package, one calling the other's static method with
    zero import, produced zero edge between them in build_module_graph.
    Originally this function only grouped .kt/.kts files, so every
    same-package-only-referenced .java file (the ordinary shape for any
    small-to-medium Java package, not an edge case) still looked
    unreachable - the exact same blind spot already fixed for Kotlin,
    silently never extended to the other JVM language this scanner
    supports. Grouping Kotlin and Java files under the same package name
    together (rather than keeping two separate maps) is deliberate, not
    incidental: a real mixed-language Android/JVM project has both
    languages sharing one package's visibility, by the JVM's own rules.

    A package's own reachability is judged by the same signals
    find_dead_code already treats as reachable on their own (imported_by,
    a manifest entry point, a Hilt/Dagger annotation, or - Java only - its
    own public static void main) - propagating from an already-
    independently-reachable sibling is the whole point, so requiring
    anything more here would just miss the real cases above.
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
        package = _jvm_package_of(repo_path, path)
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
            or _has_java_main_method(repo_path, path)
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


def _controller_suffix_matches(
    controller_paths: set[str], segments: list[str], *, anchor_single_segment: bool = True
) -> set[str]:
    """Every path in controller_paths whose final path segments exactly
    equal `segments`.

    When `anchor_single_segment` and `segments` is a single (unprefixed)
    name, matches are additionally anchored at a real controllers-directory
    boundary - the whole path is just that one segment, or the segment
    immediately before it is a "controllers" directory (case-insensitive:
    Rails lowercases it, Laravel's convention capitalizes it
    "Controllers"). Confirmed by a real end-to-end test: without this, an
    unprefixed lookup for "badges" also matched
    "app/controllers/admin/badges_controller.rb" two levels down, colliding
    with the real top-level app/controllers/badges_controller.rb - Rails'
    own unqualified `resources :badges`, declared directly in
    config/routes.rb outside any namespace, can only ever mean the
    controller sitting directly in a controllers/ dir, never a nested one.

    `anchor_single_segment=False` (see the config/routes.rb-only gating in
    _rails_route_reachable_files) skips that anchor even for a single
    segment - needed for exactly the opposite real reason, also confirmed
    end-to-end against Discourse: an unprefixed `resources :workflows`
    declared inside a PLUGIN's own routes file (mounted under
    `SomeEngine.routes.draw do ... end`) does NOT mean the top-level
    controller - Rails engines implicitly namespace every controller under
    the engine's own module (isolate_namespace) regardless of anything the
    routes file itself says, a segment this extractor has no way to see
    since it isn't a namespace/scope call at all. Anchoring there would
    wrongly reject the one real controller (e.g.
    plugins/discourse-workflows/app/controllers/discourse_workflows/
    workflows_controller.rb) an unprefixed plugin route already uniquely
    identifies, just because that untracked engine-module segment sits
    between "controllers/" and it.

    A multi-segment (prefixed) query is always a plain tail match with no
    adjacency requirement, for the same untracked-engine-segment reason -
    e.g. a `namespace :api do resources :channels end` inside the chat
    plugin's own engine-mounted routes file records "api/channels", but the
    real file is .../controllers/chat/api/channels_controller.rb with an
    extra untracked "chat" engine-module segment before "api".
    """
    n = len(segments)
    matches = {p for p in controller_paths if p.split("/")[-n:] == segments}
    if n == 1 and anchor_single_segment:
        matches = {
            p for p in matches
            if len(p.split("/")) == 1 or p.split("/")[-2].lower() == "controllers"
        }
    return matches


def _controller_path_segments(controller_part: str) -> list[str]:
    """"admin/badges" -> ["admin", "badges_controller.rb"]; "badges" ->
    ["badges_controller.rb"] - controller_part may already carry a
    namespace/module prefix (see _rails_enclosing_module_prefix in
    endpoints.py), only the final segment names the controller itself."""
    parts = controller_part.split("/")
    parts[-1] = f"{parts[-1]}_controller.rb"
    return parts


def _rails_route_reachable_files(
    modules: list[dict], api_endpoints: list[dict] | None
) -> set[str]:
    """Controller files named only in config/routes.rb ("to: 'users#show'",
    "resources :users") - Rails dispatches to them by Zeitwerk
    constant-name autoloading, never a plain `require`/import, so the
    import graph can never show an edge into them no matter how well
    Ruby import resolution works. Confirmed on a real repo (Discourse):
    every one of its ~250 app/controllers/**/*.rb files looked unreachable
    without this - 13,261 files flagged dead code-wide, the overwhelming
    majority of them controllers whose only reference anywhere in the repo
    is their own routes.rb entry.

    Deliberately reuses api_endpoints (already extracted once by
    map_api_endpoints for the API Endpoints feature) instead of re-parsing
    routes.rb here - same data, no second tree-sitter pass over the repo.

    A `resources :badges` nested in a `namespace :admin do ... end` block
    now resolves as "admin/badges" (endpoints.py's Rails extractor tracks
    enclosing namespace/`scope module:` blocks - see
    _rails_enclosing_module_prefix), so it no longer collides with an
    unrelated top-level `badges` resource the way it used to. A route
    inside a bare `scope "/logs" do ... end` (URL-only, no module change -
    the far more common form on a real repo, 20 to 3 on Discourse's own
    routes.rb) still isn't prefixed here, correctly, since Rails itself
    doesn't remodule those. The suffix match above still safely leaves a
    genuinely ambiguous name (same controller basename reused across two
    unrelated engines/plugins, not a namespace collision) unresolved
    rather than guessing.
    """
    if not api_endpoints:
        return set()
    controller_paths = {m["path"] for m in modules if m["path"].endswith("_controller.rb")}
    if not controller_paths:
        return set()
    reachable: set[str] = set()
    for entry in api_endpoints:
        if entry.get("framework") != "rails":
            continue
        handler = entry.get("handler")
        if not handler:
            continue
        if handler == "resources(...)":
            controller_part = entry.get("path")
        elif "#" in handler:
            controller_part = handler.split("#", 1)[0]
        else:
            continue
        if not controller_part:
            continue
        # Only a route declared directly in the app's own top-level
        # config/routes.rb executes with no implicit engine module wrapping
        # it - a route from any other file (a plugin/gem's own routes file,
        # almost always mounted via `SomeEngine.routes.draw do ... end`)
        # can't safely assume an unprefixed name means "the top-level
        # controller" the way _controller_suffix_matches' anchor does.
        anchor = entry.get("file") == "config/routes.rb"
        matches = _controller_suffix_matches(
            controller_paths,
            _controller_path_segments(controller_part),
            anchor_single_segment=anchor,
        )
        if len(matches) == 1:
            reachable.update(matches)
    return reachable


# Laravel's legacy "'Controller@method'" route-handler string (still valid
# alongside the newer [Controller::class, 'method'] array form) never
# accompanies a `use` import of that controller - the array form does
# (PHP requires importing App\Http\Controllers\UserController to reference
# UserController::class), so the existing PHP import graph already
# resolves that shape correctly and only the bare-string legacy form needs
# this dedicated resolver.
_LARAVEL_STRING_HANDLER_PATTERN = re.compile(r"^([\w\\]+)@\w+$")


def _laravel_route_reachable_files(
    modules: list[dict], api_endpoints: list[dict] | None
) -> set[str]:
    if not api_endpoints:
        return set()
    controller_paths = {m["path"] for m in modules if m["path"].endswith(".php")}
    if not controller_paths:
        return set()
    reachable: set[str] = set()
    for entry in api_endpoints:
        if entry.get("framework") != "laravel":
            continue
        handler = entry.get("handler")
        if not handler:
            continue
        match = _LARAVEL_STRING_HANDLER_PATTERN.match(handler)
        if not match:
            continue
        # "Admin\UserController" -> ["Admin", "UserController.php"] - a
        # fully backslash-qualified handler already names its own
        # namespace segments; preserve them for a precise multi-segment
        # match instead of discarding them down to the bare class name.
        # Never anchored to a controllers/ boundary even for the bare
        # (single-segment) case, unlike Rails' config/routes.rb: Laravel's
        # legacy string handler can equally sit inside an enclosing
        # `Route::group(['namespace' => 'Admin'], function () {...})`,
        # invisible from the handler string alone, so an unqualified name
        # here carries none of the "this really is top-level" guarantee a
        # bare Rails `resources` call declared directly in config/routes.rb
        # does. Confirmed by Flash Review on #666: without this, a real
        # nested controller like app/Http/Controllers/Admin/User.php named
        # by "Admin\User@index" was wrongly left flagged as dead code.
        segments = match.group(1).split("\\")
        segments[-1] = f"{segments[-1]}.php"
        matches = _controller_suffix_matches(
            controller_paths, segments, anchor_single_segment=False
        )
        if len(matches) == 1:
            reachable.update(matches)
    return reachable


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


def _import_root(imported: str, *, dotted: bool = True) -> str:
    # A scoped npm package (`@scope/name`, e.g. `@testing-library/react`,
    # `@angular/core`) is two path segments, not one - truncating at the
    # first "/" (the plain-package case below) keeps only the bare scope
    # (`@testing_library`), which can never match _package_import_names'
    # normalization of the real package.json dependency key
    # (`@testing_library/react`, since it only replaces "-" with "_" and
    # leaves the "/" alone). That made every scoped package - a large
    # fraction of real Angular/React/Vue/NestJS dependencies - always
    # report as unused, confirmed directly: scanning a real repo with
    # `@testing-library/react` as a real, imported dependency still
    # flagged it unused. A further subpath (`@mui/material/Button`) still
    # resolves to the installed package (`@mui/material`), matching how
    # npm resolution itself works, not the file within it.
    if imported.startswith("@"):
        root = "/".join(imported.split("/", 2)[:2])
    else:
        root = imported.split("/", 1)[0]
    # `dotted=True` (the default, used for Python) treats "." as a
    # submodule separator - `import os.path` should resolve to root "os",
    # matching how requirements.txt/pyproject.toml name the top-level
    # package. That convention does not exist for npm: a real, published
    # package can have a literal dot in its own name (`normalize.css`,
    # `chart.js`, `socket.io-client`) with no submodule meaning at all -
    # applying Python's rule there truncated the real package name
    # (`normalize.css` -> `normalize`), which could then never match
    # _package_import_names' identical, unsplit normalization of the real
    # package.json key. _raw_external_import_roots passes dotted=False
    # for JS/TS imports specifically.
    if dotted:
        root = root.split(".", 1)[0]
    return root.lower().replace("-", "_")


def _import_roots(modules: list[dict]) -> set[str]:
    roots = set()
    for module in modules:
        for imported in module.get("imports", []):
            root = _import_root(imported)
            if root:
                roots.add(root)
    return roots


# module["imports"] (scanner/graph.py's resolved_imports) only ever holds
# import targets that resolved to another file INSIDE the repo - the
# resolvers there deliberately drop the raw import name entirely once
# resolution to a repo-internal file fails (an external package is never
# a resolution target, by definition). That means _import_roots() above
# structurally can never see an external package import at all, which
# made the unused_dependencies check below always report every real
# dependency as unused - confirmed directly, not hypothetical: scanning
# this repo's own Flask fixture reported werkzeug/jinja2/itsdangerous/
# click/blinker/importlib-metadata (Flask's actual, definitely-used
# runtime dependencies) as 100% unused.
#
# This re-reads each Python/JS/TS source file directly and regex-extracts
# raw import roots regardless of whether they'd resolve internally -
# scoped to dead-code.py rather than changing what scanner/graph.py's
# resolvers keep, since that field also feeds the import GRAPH (edges,
# imported_by, hotspots, MCP's aletheore_imports) where only real
# repo-internal edges belong; conflating "resolved" and "external, only
# useful for this one check" into the same field would be a much larger,
# riskier change than this narrowly-scoped, single-purpose regex pass.
#
# Known limitation, matching this file's existing Gradle-Groovy-regex
# precedent below: only the first name on a comma-separated
# `import a, b, c` line is captured (`from x import (a, b, c)` is
# unaffected - the from-clause itself is what's captured, not what's
# imported from it). PEP 8 discourages multi-import lines; accepted as a
# narrower, explicit heuristic rather than a full parse, the same
# trade-off _parse_gradle_groovy_pins documents for itself.
_PY_PLAIN_IMPORT_RE = re.compile(r"^\s*import\s+([A-Za-z_][\w.]*)", re.MULTILINE)
_PY_FROM_IMPORT_RE = re.compile(r"^\s*from\s+([A-Za-z_][\w.]*)\s+import\b", re.MULTILINE)
# `from '...'` (a normal import) and `require('...')` were the only two
# shapes recognized. Two other real, common shapes were missing entirely:
# a side-effect-only import (`import 'normalize.css';` - no `from` clause
# at all, common for CSS/polyfills) and a dynamic import
# (`import('chart.js')` / `lazy(() => import('some-pkg'))` - common for
# code-splitting/lazy-loaded libraries). A package imported only one of
# these two ways was reported as an unused dependency every time.
# `require.resolve('pkg')` (real, common for webpack aliasing and worker
# entry points, e.g. `new Worker(require.resolve('./worker'))`) has the
# same gap: `require\(` only matches the literal substring "require(",
# which never appears in "require.resolve(" - confirmed via a real
# false-positive repro (a package imported only this way was always
# flagged unused). require\(?:\.resolve)?\( covers both.
_JS_IMPORT_RE = re.compile(
    r"""(?:from|require(?:\.resolve)?\(|import\s*\()\s*['"]([^'"]+)['"]"""
)
_JS_SIDE_EFFECT_IMPORT_RE = re.compile(r"""^\s*import\s*['"]([^'"]+)['"]""", re.MULTILINE)


def _raw_external_import_roots(repo_path: Path, modules: list[dict]) -> set[str]:
    roots: set[str] = set()
    for module in modules:
        path = module["path"]
        if path.endswith(".py"):
            pattern_pairs = (_PY_PLAIN_IMPORT_RE, _PY_FROM_IMPORT_RE)
            dotted = True
        elif path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
            pattern_pairs = (_JS_IMPORT_RE, _JS_SIDE_EFFECT_IMPORT_RE)
            dotted = False
        else:
            continue
        try:
            source = (repo_path / path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern in pattern_pairs:
            for match in pattern.finditer(source):
                root = _import_root(match.group(1), dotted=dotted)
                if root:
                    roots.add(root)
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
    api_endpoints: list[dict] | None = None,
) -> dict:
    custom_entry_points = set()
    if isinstance(config, dict):
        raw_entry_points = config.get("dead_code_entry_points", [])
        if isinstance(raw_entry_points, list):
            custom_entry_points = {path for path in raw_entry_points if isinstance(path, str)}

    html_script_entry_points = _html_script_entry_points(repo_path, ignored_paths)
    android_manifest_entry_points = _android_manifest_entry_points(repo_path, ignored_paths)
    jvm_package_reachable_files = _jvm_package_reachable_files(
        repo_path, modules, android_manifest_entry_points
    )
    swift_reachable_files = _swift_target_reachable_files(repo_path, modules, ignored_paths)
    rails_route_reachable_files = _rails_route_reachable_files(modules, api_endpoints)
    laravel_route_reachable_files = _laravel_route_reachable_files(modules, api_endpoints)

    unreachable_modules = []
    entry_points_detected = []
    for module in modules:
        path = module["path"]
        if _is_entry_point(path, custom_entry_points):
            entry_points_detected.append(path)
            continue
        if is_test_file(path):
            continue
        if (
            path in swift_reachable_files
            or path in jvm_package_reachable_files
            or path in rails_route_reachable_files
            or path in laravel_route_reachable_files
        ):
            continue
        if not module.get("imported_by", []):
            if (
                path in html_script_entry_points
                or path in android_manifest_entry_points
                or _has_main_guard(repo_path, path)
                or _has_hilt_dagger_annotation(repo_path, path)
                or _has_spring_stereotype_annotation(repo_path, path)
                or _has_aspnet_controller_convention(repo_path, path)
                or _has_go_main_function(repo_path, path)
                or _has_rust_main_function(repo_path, path)
                or _has_java_main_method(repo_path, path)
                or _has_csharp_main_method(repo_path, path)
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

    # Rails' Zeitwerk autoloader (default since Rails 6) means idiomatic
    # app/ code is essentially never require'd anywhere - it's referenced
    # purely by constant name, autoloaded on first use. rails_route_
    # reachable_files above only covers controllers (dispatched from
    # routes.rb); everything else under app/ - models, jobs, mailers,
    # services, serializers, channels, policies - had no reachability
    # signal at all beyond "does some other file import this," which is
    # never true for Zeitwerk-autoloaded code. Confirmed on a real repo
    # (discourse/discourse): every one of app/models' 391, app/jobs' 241,
    # app/mailers' 10, app/services' 245, and app/serializers' 242 files
    # was flagged dead code-wide before this fix - 100% false positive
    # rate on the most fundamental Rails conventions there are, not an
    # edge case.
    if any(entry["path"].startswith("app/") and entry["path"].endswith(".rb") for entry in unreachable_modules):
        rb_sources = {}
        for module in modules:
            if not module["path"].endswith(".rb"):
                continue
            try:
                rb_sources[module["path"]] = (repo_path / module["path"]).read_text(
                    encoding="utf-8", errors="ignore"
                )
            except OSError:
                continue
        # .rake files are real Ruby (Rake tasks routinely dispatch a job
        # by symbol, e.g. `Jobs.enqueue(:prepare_nested_reply_stats, ...)`
        # - confirmed on a real repo, lib/tasks/nested_replies.rake in
        # discourse/discourse) but aren't part of `modules` at all - the
        # scanner's dependency graph doesn't track them as a language unit
        # the way .rb files are, so they're invisible to both indexes
        # below unless read here explicitly. They're a reference SOURCE
        # only, never a rescue TARGET (they can never appear in
        # unreachable_modules to begin with), so adding their content to
        # rb_sources is safe with no risk of a .rake file "rescuing
        # itself."
        rake_patterns = ignored_paths or []
        for rake_file in repo_path.rglob("*.rake"):
            rel_path = rake_file.relative_to(repo_path).as_posix()
            rel_parts = rake_file.relative_to(repo_path).parts
            if any(part in IGNORED_DIRS for part in rel_parts) or is_ignored(rel_path, rake_patterns):
                continue
            try:
                rb_sources[rel_path] = rake_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
        ruby_token_index = _ruby_constant_token_index(rb_sources)
        ruby_symbol_index = _ruby_symbol_token_index(rb_sources)
        still_unreachable = []
        for entry in unreachable_modules:
            if entry["path"].startswith("app/") and entry["path"].endswith(".rb") and (
                _referenced_by_ruby_constant(entry["path"], ruby_token_index)
                or _referenced_by_ruby_symbol_dispatch(entry["path"], ruby_symbol_index)
            ):
                entry_points_detected.append(entry["path"])
            else:
                still_unreachable.append(entry)
        unreachable_modules = still_unreachable

    imported_roots = _import_roots(modules) | _raw_external_import_roots(repo_path, modules)
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
