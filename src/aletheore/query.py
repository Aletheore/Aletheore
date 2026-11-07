import difflib
import posixpath
import re
from collections import deque
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from aletheore.evidence_resolution import resolve_code_evidence


class ModuleNotFoundInEvidenceError(Exception):
    def __init__(self, file_path: str, suggestions: list[str] | None = None):
        message = (
            f"'{file_path}' is not a scanned file. Give a repo-relative file path "
            "such as 'src/app.py' (not a number, a directory or a symbol name)"
        )
        if suggestions:
            message += f"; did you mean: {', '.join(suggestions)}?"
        super().__init__(message)
        self.file_path = file_path
        self.suggestions = suggestions or []


class BranchNotFoundInEvidenceError(Exception):
    def __init__(self, branch_name: str):
        super().__init__(f"'{branch_name}' is not present in evidence.git.branches")
        self.branch_name = branch_name


class SymbolNotFoundInEvidenceError(Exception):
    def __init__(self, module_path: str, symbol_name: str):
        super().__init__(f"'{symbol_name}' is not present in {module_path}'s symbols")
        self.module_path = module_path
        self.symbol_name = symbol_name


def normalize_module_target(evidence: dict, file_path: str | None) -> str | None:
    """Accepts `./src/app.py`, `src//app.py` and an absolute path inside the
    scanned repo, and returns the repo-relative form evidence uses."""
    if not file_path:
        return file_path
    text = file_path.strip()
    path = Path(text)
    if path.is_absolute() or text.startswith("/"):
        root = evidence.get("repo_path")
        if root:
            # Path comparison, not string prefixes: it handles drive letters, case and backslashes on Windows.
            candidates = {path}
            try:
                candidates.add(path.resolve())
            except OSError:
                pass
            roots = {Path(root)}
            try:
                roots.add(Path(root).resolve())
            except OSError:
                pass
            for candidate in candidates:
                for candidate_root in roots:
                    try:
                        return candidate.relative_to(candidate_root).as_posix()
                    except ValueError:
                        continue
        return text
    # Backslashes are separators too (a Windows user types src\\app.py); evidence paths always use "/".
    return posixpath.normpath(text.replace("\\", "/"))


def _module_path_suggestions(evidence: dict, wanted: str) -> list[str]:
    paths = [m["path"] for m in evidence["repository"]["modules"] if m.get("path")]
    base = posixpath.basename(wanted)
    same_name = [p for p in paths if base and posixpath.basename(p) == base]
    close = difflib.get_close_matches(wanted, paths, n=3, cutoff=0.5)
    return list(dict.fromkeys(same_name + close))[:3]


def _find_module(evidence: dict, file_path: str) -> dict:
    wanted = normalize_module_target(evidence, file_path)
    for module in evidence["repository"]["modules"]:
        if module["path"] == wanted:
            return module
    raise ModuleNotFoundInEvidenceError(file_path, _module_path_suggestions(evidence, wanted or ""))


def _unique(paths: list[str]) -> list[str]:
    """Each file once, in first-seen order. Evidence lists a file once per import statement, so a module that
    imports another in ten places showed up ten times - tokens an agent paid for and then had to dedupe by hand."""
    return list(dict.fromkeys(paths))


def find_imports(evidence: dict, target: str | None) -> list[str]:
    return _unique(_find_module(evidence, target)["imports"])


def find_imported_by(evidence: dict, target: str | None) -> list[str]:
    return _unique(_find_module(evidence, target)["imported_by"])


_TRANSITIVE_IMPORTS_SHOWN = 80


def find_transitive_imports(evidence: dict, target: str | None) -> dict:
    """Everything `target` depends on through imports: the modules it imports, the modules those import, and so on.

    The forward counterpart of find_blast_radius. The walk covers the whole closure so the totals are exact;
    only the lists handed back are capped. `transitive_non_test_total` leaves out test files, which is what
    "how many modules does this depend on" usually means.
    """
    module = _find_module(evidence, target)
    by_path = {m["path"]: m for m in evidence["repository"]["modules"] if m.get("path")}
    direct = [p for p in _unique(module.get("imports") or []) if p != target]
    seen = {target, *direct}
    reached: list[str] = []
    queue = deque(direct)
    while queue:
        current = queue.popleft()
        for dependency in by_path.get(current, {}).get("imports") or []:
            if dependency in seen:
                continue
            seen.add(dependency)
            reached.append(dependency)
            queue.append(dependency)
    everything = [*direct, *reached]
    tests = [p for p in everything if _BLAST_TEST_PATH.search(p)]
    return {
        "target": target,
        "depends_on_total": len(everything),
        "depends_on_non_test_total": len(everything) - len(tests),
        "depends_on_test_files": len(tests),
        "direct_total": len(direct),
        "transitive_total": len(reached),
        "direct_imports": direct[:_TRANSITIVE_IMPORTS_SHOWN],
        "direct_imports_truncated": len(direct) > _TRANSITIVE_IMPORTS_SHOWN,
        "transitive_imports": reached[:_TRANSITIVE_IMPORTS_SHOWN],
        "transitive_imports_truncated": len(reached) > _TRANSITIVE_IMPORTS_SHOWN,
        "definition": "modules reachable by following imports from the target, not counting the target itself",
    }


def find_symbols(evidence: dict, target: str | None) -> dict:
    return _find_module(evidence, target)["symbols"]


def find_symbol_source(
    evidence: dict, repo_path: Path, module_path: str, symbol_name: str
) -> dict:
    module = _find_module(evidence, module_path)
    symbols = (
        module["symbols"]["functions"]
        + module["symbols"]["classes"]
        + module["symbols"].get("constants", [])
    )
    entry = next((symbol for symbol in symbols if symbol["name"] == symbol_name), None)
    if entry is None:
        raise SymbolNotFoundInEvidenceError(module_path, symbol_name)

    file_path = repo_path / module_path
    # split("\n"), never splitlines() - same real bug class already found
    # and fixed in the github-app side of this codebase (flash_review.py's
    # _clickable_suggestion/_line_citation_content_matches, jobs.py's
    # _fetch_symbol_source): splitlines() also breaks on \v, \f,
    # \x1c-\x1e, NEL, LS, and PS, none of which git/a real editor treat as
    # a line boundary (only "\n" is). entry["start_line"]/["end_line"] are
    # real, \n-based line numbers recorded when this file was parsed -
    # indexing them into a splitlines()-produced list silently returns the
    # WRONG symbol body the moment one of those characters appears
    # anywhere earlier in the file. This is the backing implementation for
    # both `aletheore symbol-source` (cli.py) and the aletheore_symbol_
    # source MCP tool (mcp_server.py) - a wrong result here is returned
    # directly to whoever asked, not merely mis-cited.
    lines = file_path.read_text(encoding="utf-8", errors="ignore").split("\n")
    source = "\n".join(lines[entry["start_line"] - 1 : entry["end_line"]])

    return {
        "module": module_path,
        "symbol": symbol_name,
        "start_line": entry["start_line"],
        "end_line": entry["end_line"],
        "source": source,
    }


def find_branch(evidence: dict, target: str | None) -> dict:
    # A repo with no commits yields git == {"available": False} - see
    # air_schema.py's git section docstring and list_branches' matching
    # guard. No branch named `target` can exist there, so this is a normal
    # not-found rather than a special case: same exception as any other
    # missing branch name, not a raw KeyError on a missing "branches" key.
    git = evidence["git"]
    if git.get("available") is False:
        raise BranchNotFoundInEvidenceError(target)
    for branch in git["branches"]:
        if branch["name"] == target:
            return branch
    raise BranchNotFoundInEvidenceError(target)


def find_ownership(evidence: dict, target: str | None) -> list[dict]:
    if target:
        return evidence["git"].get("file_ownership", {}).get(target, [])
    return evidence["git"].get("ownership", [])


def find_secrets_for_file(evidence: dict, target: str | None) -> list[dict]:
    return [
        finding
        for finding in evidence["security"]["secrets"]["findings"]
        if finding["path"] == target
    ]


def find_vulnerabilities(evidence: dict, target: str | None) -> dict:
    return evidence["security"]["dependency_vulnerabilities"]


def find_licenses(evidence: dict, target: str | None) -> dict:
    return evidence["security"]["dependency_licenses"]


def find_static_analysis(evidence: dict, target: str | None) -> dict:
    return evidence["security"]["static_analysis"]


def find_endpoints(evidence: dict, target: str | None) -> dict:
    return evidence["repository"]["api_endpoints"]


def find_code_evidence_for_endpoint(
    evidence: dict, target: str | None, repo_path: Path | None = None
) -> dict:
    if not target or not target.strip():
        return resolve_code_evidence(evidence, repo_path, kind="endpoint")
    text = target.strip()
    if " " not in text:
        # A bare path matches whichever method(s) serve it; they are listed.
        wanted = text if text.startswith("/") else f"/{text}"
        matches = [e for e in _endpoint_list(evidence) if _endpoint_path(e) == wanted]
        if not matches:
            return resolve_code_evidence(evidence, repo_path, kind="endpoint")
        first = matches[0]
        result = resolve_code_evidence(
            evidence, repo_path, kind="endpoint", method=(first.get("method") or "GET").upper(), path=wanted
        )
        result["methods"] = sorted({(e.get("method") or "?").upper() for e in matches})
        return result
    method, path = text.split(maxsplit=1)
    return resolve_code_evidence(evidence, repo_path, kind="endpoint", method=method, path=path)


def _endpoint_list(evidence: dict) -> list[dict]:
    return evidence.get("repository", {}).get("api_endpoints", {}).get("endpoints", [])


def _endpoint_path(endpoint: dict) -> str:
    path = endpoint.get("path") or ""
    return path if path.startswith("/") else f"/{path}"


def evidence_target_not_found_message(evidence: dict, kind: str, target: str | None) -> str | None:
    """For the evidence-for-* kinds: a message when `target` resolved to nothing
    (otherwise the result is a block of all-"unavailable" fields that reads like
    a successful answer), with up to three close matches. None when it resolved."""
    target = (target or "").strip()
    if kind == "evidence-for-symbol":
        names = []
        for module in evidence["repository"]["modules"]:
            symbols = module.get("symbols", {})
            for group in ("functions", "classes", "constants"):
                names += [e["name"] for e in symbols.get(group, []) if e.get("name")]
        close = difflib.get_close_matches(target, list(dict.fromkeys(names)), n=3, cutoff=0.6)
        return _not_found("symbol", target, close)
    if kind == "evidence-for-dependency":
        imports = sorted({i for m in evidence["repository"]["modules"] for i in m.get("imports", [])})
        close = difflib.get_close_matches(target, imports, n=3, cutoff=0.6)
        return _not_found("dependency", target, close)
    if kind == "evidence-for-endpoint":
        labels = [f"{(e.get('method') or '?').upper()} {_endpoint_path(e)}" for e in _endpoint_list(evidence)]
        close = difflib.get_close_matches(target, labels, n=3, cutoff=0.5)
        message = _not_found("endpoint", target, close)
        return message + (
            "\nExpected 'METHOD /path' (for example 'GET /users') or a bare path (for example '/users')."
        )
    return None


def _not_found(what: str, target: str, close: list[str]) -> str:
    message = f"{what} '{target}' was not found in the scan's evidence."
    if close:
        message += " Did you mean: " + ", ".join(close) + "?"
    return message


def find_code_evidence_for_symbol(
    evidence: dict, target: str | None, repo_path: Path | None = None
) -> dict:
    return resolve_code_evidence(evidence, repo_path, kind="symbol", symbol=target)


def find_code_evidence_for_dependency(
    evidence: dict, target: str | None, repo_path: Path | None = None
) -> dict:
    return resolve_code_evidence(evidence, repo_path, kind="dependency", dependency=target)


def find_cluster(evidence: dict, target: str | None) -> dict:
    wanted = normalize_module_target(evidence, target)
    for cluster in evidence["architecture"]["clusters"]:
        if wanted in cluster["modules"]:
            return cluster
    raise ModuleNotFoundInEvidenceError(target, _module_path_suggestions(evidence, wanted or ""))


def find_layer_violations(evidence: dict, target: str | None) -> dict:
    return evidence["architecture"]["layer_violations"]


# Originally matched the constants of Flash Review's build_blast_radius_context
# (MAX_BLAST_RADIUS_CANDIDATES, MAX_BLAST_RADIUS_CALLERS_SHOWN), adapted to read
# local files directly instead of fetching over the GitHub API. That function
# was removed as dead code in #982; these values now stand on their own.
_BLAST_RADIUS_CONFIRM_CANDIDATES = 40
_BLAST_RADIUS_CONFIRMED_CALLERS_SHOWN = 10
_BLAST_RADIUS_MAX_TRANSITIVE = 50
# Real bug found via audit: direct_dependents (evidence's own raw
# imported_by list) was returned verbatim with no cap at all - unlike
# transitive_dependents just below (capped, with an honest truncated
# flag) and unlike the Flash Review function this was modelled on, which
# capped its candidate list at 40 right at the start. A genuinely central
# module (a shared utils.py/models.py) can
# have hundreds-to-thousands of direct importers on a real repo -
# confirmed directly: a 500-file synthetic hub returned all 500 with no
# truncation signal, the same unbounded-MCP/CLI-result failure mode
# already hardened twice this session for aletheore_search and
# aletheore_ast_pattern.
_BLAST_RADIUS_MAX_DIRECT = 50
# Same shape as tests_for._TEST_PATH, local so the count of test files in a blast radius needs no extra import.
_BLAST_TEST_PATH = re.compile(
    r"(^|/)(tests?|testing|__tests__|spec)/|_test\.|\.test\.|\.spec\.|(^|/)test_[^/]*$|/testdata/|conftest"
    # PascalCase test files and test projects: FooTests.cs, FooTest.java, UnitTests/, Foo.Tests/
    r"|[A-Za-z0-9]Tests?\.(?:cs|java|kt|kts|php|scala|swift)$|(^|/)[\w.\-]*Tests?/"
)


def find_blast_radius(
    evidence: dict, repo_path: Path, target: str, symbol: str | None = None
) -> dict:
    """Everything that would be affected by changing `target` - direct and
    transitive dependents, and any existing layer violations already
    touching one of them.

    Direct dependents are evidence's own imported_by, unchanged. Transitive
    dependents are a real BFS over that same imported_by edge set (not
    just one hop), capped at _BLAST_RADIUS_MAX_TRANSITIVE with a truncated
    flag rather than silently cut off - a highly-central module (a shared
    utils file) can have far more transitive dependents than are useful to
    return in one call, and the flag says so rather than pretending the
    list is exhaustive.

    With `symbol` given, also confirms which of the direct dependents
    actually CALL it, not just import the file: a
    candidate only counts if its real file content contains the symbol
    name in a call-shaped position (`name(`), the same deliberately
    high-confidence-only design (a bare "imports the file" says nothing
    about which of possibly many exported names is actually used).
    Confirmed against real local content via repo_path, not evidence -
    evidence has no per-call-site data to check against.

    Also separately reports `same_file_caller`: whether `target`'s own
    content calls `symbol` from somewhere other than its definition line -
    a same-file/same-class caller (e.g. a class's __call__ invoking one of
    its own other methods) is real and confirmed_callers alone can never
    surface it, since that list only ever checks *other files* that import
    target. Found via a real gap: aletheore_get_blast_radius on Flask's
    wsgi_app returned confirmed_callers=[] (correctly - no other file calls
    it by name) while the actual caller, Flask.__call__, sits one class
    away in the same file. Excludes matches on `symbol`'s own def/class
    line so the symbol's own definition never counts as calling itself.

    layer_violations reports EXISTING violations (evidence's own
    architecture.layer_violations) that already touch a module in this
    blast radius - not a prospective simulation of what a signature change
    would newly break, which would need real symbol-level call resolution
    this scanner doesn't have.
    """
    module = _find_module(evidence, target)
    # imported_by lists a file once per import statement; counting it raw inflated every total.
    direct_dependents = _unique(module.get("imported_by") or [])
    by_path = {m["path"]: m for m in evidence["repository"]["modules"] if m.get("path")}

    visited = {target, *direct_dependents}
    # The walk covers the whole closure (it is bounded by the number of modules) so the totals are exact;
    # only the list handed back is capped. Stopping the walk at the cap, as this once did, left a hub's real
    # reach unknown and made "how many modules does this affect" unanswerable.
    all_transitive: list[str] = []
    queue = deque(direct_dependents)
    while queue:
        current = queue.popleft()
        for dependent in by_path.get(current, {}).get("imported_by") or []:
            if dependent in visited:
                continue
            visited.add(dependent)
            all_transitive.append(dependent)
            queue.append(dependent)
    transitive_dependents = all_transitive[:_BLAST_RADIUS_MAX_TRANSITIVE]
    truncated = len(all_transitive) > _BLAST_RADIUS_MAX_TRANSITIVE
    affected = [*direct_dependents, *all_transitive]

    result: dict[str, Any] = {
        "target": target,
        "affected_total": len(affected),
        "affected_test_files": sum(1 for path in affected if _BLAST_TEST_PATH.search(path)),
        "direct_total": len(direct_dependents),
        "transitive_total": len(all_transitive),
        # Truncated for DISPLAY only - the BFS above, confirmed_callers
        # below, and blast_radius_modules further down all still use the
        # full, untruncated direct_dependents list, so a large hub's real
        # transitive reach and layer-violation exposure are never
        # understated just because its own direct-importer list is long.
        "direct_dependents": direct_dependents[:_BLAST_RADIUS_MAX_DIRECT],
        "direct_dependents_truncated": len(direct_dependents) > _BLAST_RADIUS_MAX_DIRECT,
        "transitive_dependents": transitive_dependents,
        "transitive_dependents_truncated": truncated,
    }

    if symbol:
        call_pattern = re.compile(r"\b" + re.escape(symbol) + r"\s*\(")
        confirmed_callers: list[str] = []
        for candidate in direct_dependents[:_BLAST_RADIUS_CONFIRM_CANDIDATES]:
            if len(confirmed_callers) >= _BLAST_RADIUS_CONFIRMED_CALLERS_SHOWN:
                break
            try:
                content = (repo_path / candidate).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if call_pattern.search(content):
                confirmed_callers.append(candidate)

        # target's own file necessarily contains symbol's def/class line,
        # which would otherwise false-positive as a "self-call" - a
        # dependent can never define symbol itself, so no such exclusion
        # is needed in the loop above.
        def_pattern = re.compile(r"^\s*(async\s+def|def|class)\s+" + re.escape(symbol) + r"\b")
        same_file_caller = False
        try:
            target_content = (repo_path / target).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            target_content = ""
        for line in target_content.splitlines():
            if def_pattern.match(line):
                continue
            if call_pattern.search(line):
                same_file_caller = True
                break

        result["symbol"] = symbol
        result["confirmed_callers"] = confirmed_callers
        result["same_file_caller"] = same_file_caller

    blast_radius_modules = {target, *direct_dependents, *all_transitive}
    violations = evidence.get("architecture", {}).get("layer_violations", {}).get("violations") or []
    result["layer_violations"] = [
        v for v in violations
        if v.get("from") in blast_radius_modules or v.get("to") in blast_radius_modules
    ]
    return result


# Bounds a BFS over the import graph the same way _BLAST_RADIUS_MAX_TRANSITIVE
# bounds blast radius's - a real answer for most repos, an honest truncation
# signal rather than a runaway scan or a silent wrong "no path" on a huge
# monorepo's import graph.
_SYMBOL_PATH_MAX_HOPS = 12
_SYMBOL_PATH_MAX_VISITED = 5000


def _has_symbol(module: dict, symbol_name: str) -> bool:
    symbols = module["symbols"]
    return any(
        entry["name"] == symbol_name
        for entry in symbols["functions"] + symbols["classes"] + symbols.get("constants", [])
    )


def _symbol_body_or_none(
    evidence: dict, repo_path: Path, module_path: str, symbol_name: str
) -> str | None:
    try:
        return find_symbol_source(evidence, repo_path, module_path, symbol_name)["source"]
    except OSError:
        return None


def find_symbol_path(
    evidence: dict,
    repo_path: Path,
    source: str,
    source_symbol: str,
    target: str,
    target_symbol: str,
) -> dict:
    """Is there an evidence-backed path from `source_symbol` (in `source`) to
    `target_symbol` (in `target`)? The file-level analog of a symbol call
    graph, built from what this scanner actually has - the imports edge set
    and real file content - not a full symbol-level call graph (this
    scanner doesn't build per-call-site edges between arbitrary symbols).

    Same file (`source == target`): high confidence. `source_symbol`'s own
    body (the exact lines evidence records for it, not the whole file) is
    checked directly for a call-shaped reference to `target_symbol` -
    mirrors find_blast_radius's same_file_caller check, but scoped to the
    one calling symbol asked about rather than "anywhere in the file".

    Different files: a real BFS over the `imports` edge set (source's own
    imports, transitively - the direction a caller's file must chase to
    reach a callee's file) finds the shortest chain of modules from
    `source` to `target`, capped at _SYMBOL_PATH_MAX_HOPS hops and
    _SYMBOL_PATH_MAX_VISITED modules visited. Only a direct 1-hop chain
    (source imports target directly) gets the same high-confidence
    call-shape check as the same-file case; a longer chain proves an
    import path exists but NOT that source_symbol's call chain actually
    reaches target_symbol through it - this scanner has no per-hop
    symbol-usage data to confirm that, and confirmed stays False rather
    than guessing.
    """
    source_module = _find_module(evidence, source)
    target_module = _find_module(evidence, target)
    if not _has_symbol(source_module, source_symbol):
        raise SymbolNotFoundInEvidenceError(source, source_symbol)
    if not _has_symbol(target_module, target_symbol):
        raise SymbolNotFoundInEvidenceError(target, target_symbol)

    result: dict[str, Any] = {
        "source": {"file": source, "symbol": source_symbol},
        "target": {"file": target, "symbol": target_symbol},
        "same_file": source == target,
    }

    call_pattern = re.compile(r"\b" + re.escape(target_symbol) + r"\s*\(")

    def _confirm_direct_call() -> tuple[bool, str]:
        body = _symbol_body_or_none(evidence, repo_path, source, source_symbol)
        if body is None:
            return False, f"could not read {source} to verify {source_symbol}'s body"
        if call_pattern.search(body):
            return True, f"{source_symbol}'s own body contains a call to {target_symbol}"
        return False, f"{source_symbol}'s own body has no call-shaped reference to {target_symbol}"

    if source == target:
        confirmed, basis = _confirm_direct_call()
        result["hops"] = [source]
        result["hops_truncated"] = False
        result["confirmed"] = confirmed
        result["confirmation_basis"] = basis
        return result

    by_path = {m["path"]: m for m in evidence["repository"]["modules"] if m.get("path")}
    visited = {source}
    queue: list[list[str]] = [[source]]
    chain: list[str] | None = None
    # Tracks whether either cap actually fired during the search, decoupled
    # from "the queue drained" - the queue drains naturally both when the
    # graph is genuinely exhausted AND when a cap silently stops new nodes
    # from ever being queued, and those two cases need different messages.
    bounded = False
    while queue and chain is None:
        path = queue.pop(0)
        if len(path) > _SYMBOL_PATH_MAX_HOPS:
            bounded = True
            continue
        for nxt in by_path.get(path[-1], {}).get("imports") or []:
            if nxt == target:
                chain = [*path, nxt]
                break
            if nxt in visited:
                continue
            if len(visited) >= _SYMBOL_PATH_MAX_VISITED:
                bounded = True
                continue
            visited.add(nxt)
            queue.append([*path, nxt])

    if chain is None:
        result["hops"] = None
        result["hops_truncated"] = False
        result["confirmed"] = False
        result["confirmation_basis"] = (
            f"import graph search bounded at {_SYMBOL_PATH_MAX_HOPS} hops / "
            f"{_SYMBOL_PATH_MAX_VISITED} modules before finding a chain - not a "
            "definitive 'no path exists'"
            if bounded
            else f"no import chain from {source} to {target} in the reachable import graph"
        )
        return result

    result["hops"] = chain
    result["hops_truncated"] = False
    if len(chain) == 2:
        confirmed, basis = _confirm_direct_call()
        result["confirmed"] = confirmed
        result["confirmation_basis"] = f"direct import (1 hop): {basis}"
    else:
        result["confirmed"] = False
        result["confirmation_basis"] = (
            f"import chain found ({len(chain) - 1} hops) - proves {target} is reachable "
            f"from {source}'s imports, but not that {source_symbol} specifically calls "
            f"{target_symbol} through it; only a direct 1-hop import gets call-shape "
            "confirmation"
        )
    return result


def find_dead_code_evidence(evidence: dict, target: str | None) -> dict:
    return evidence["repository"]["dead_code"]


# Display caps for find_unimported_modules. Every total is counted over the whole scope first; only the lists
# are cut, and a *_truncated flag says so (the find_blast_radius lesson).
_UNIMPORTED_MAX_LISTED = 200
_UNIMPORTED_MAX_TEST_ONLY = 20

_UNIMPORTED_DEFINITION = (
    "unimported = no other module in the scan's import graph imports the file; test files and __init__.py are "
    "never listed. This is not dead_code.unreachable_modules, which also drops entry points, main-guard scripts "
    "and files named by a dotted string (settings, URLconfs), so it lists fewer files."
)
# What to do with the list. Agents asked "which modules does nothing import" took the framework conventions they
# knew (middleware, cache and email backends named in settings) and removed those files from the tool's list, which
# answered "is it used" instead of the question. Both questions are legitimate, so say which one this list answers.
_UNIMPORTED_ANSWERING_NOTE = (
    "Report `unimported` as returned when asked which modules nothing imports. A file a framework loads by name "
    "(a middleware, backend or plugin named in settings) is still unimported: it just has no importer. That is a "
    "separate question from whether it is used; mention such files beside the list rather than removing them."
)


def find_unimported_modules(
    evidence: dict,
    scope: str | None = None,
    languages: list[str] | None = None,
    ignore_test_importers: bool = False,
) -> dict:
    """Modules that no other module imports, optionally limited to a directory (or one file) and languages.

    The scan's own dead_code.unreachable_modules answers a different question ("is this reachable at all"):
    it drops entry points, main-guard scripts and dotted-string references, and it is repo-wide. This reads
    the raw imported_by edges instead, so "which files under X does nothing import" is exact. Test files are
    not candidates; a file imported only by tests is reported separately, since whether that counts as
    imported depends on the question. With `ignore_test_importers` (a "non-test" question) such a file counts
    as unimported and the separate list is empty.
    """
    prefix = (scope or "").strip("/")
    wanted = {lang.lower() for lang in languages or []}
    unimported: list[str] = []
    tests_only: list[str] = []
    for module in evidence["repository"]["modules"]:
        path = module.get("path") or ""
        if prefix and path != prefix and not path.startswith(prefix + "/"):
            continue
        if wanted and str(module.get("language") or "").lower() not in wanted:
            continue
        if _BLAST_TEST_PATH.search(path) or PurePosixPath(path).name.startswith("__init__."):
            continue
        importers = module.get("imported_by") or []
        if not importers:
            unimported.append(path)
        elif all(_BLAST_TEST_PATH.search(p) for p in importers):
            (unimported if ignore_test_importers else tests_only).append(path)
    unimported.sort()
    tests_only.sort()
    # The scan's own list gets the same scope and language filter, so the two totals are comparable.
    language_of = {m.get("path"): str(m.get("language") or "").lower() for m in evidence["repository"]["modules"]}
    scan_unreachable = []
    for entry in evidence["repository"].get("dead_code", {}).get("unreachable_modules", []):
        entry_path = entry.get("path") or ""
        if not entry_path or (prefix and entry_path != prefix and not entry_path.startswith(prefix + "/")):
            continue
        if wanted and language_of.get(entry_path, "") not in wanted:
            continue
        scan_unreachable.append(entry_path)
    return {
        "scope": prefix or "(whole repo)",
        "languages": sorted(wanted) or "any",
        "definition": _UNIMPORTED_DEFINITION,
        "answering_note": _UNIMPORTED_ANSWERING_NOTE,
        "importers_counted": "non-test only" if ignore_test_importers else "all",
        "unimported_total": len(unimported),
        "imported_only_by_tests_total": len(tests_only),
        "scan_unreachable_total": len(scan_unreachable),
        "unimported": unimported[:_UNIMPORTED_MAX_LISTED],
        "unimported_truncated": len(unimported) > _UNIMPORTED_MAX_LISTED,
        "imported_only_by_tests": tests_only[:_UNIMPORTED_MAX_TEST_ONLY],
        "imported_only_by_tests_truncated": len(tests_only) > _UNIMPORTED_MAX_TEST_ONLY,
    }


_MOST_IMPORTED_DEFAULT = 10
_MOST_IMPORTED_MAX = 50


def find_most_imported_modules(
    evidence: dict,
    scope: str | None = None,
    limit: int = _MOST_IMPORTED_DEFAULT,
    ignore_test_importers: bool = False,
) -> dict:
    """The modules the most other modules import (highest fan-in), optionally limited to a directory.

    Test files are never candidates. Every importer counts by default; with `ignore_test_importers` (a "non-test"
    question) only non-test importers do, which is the usual meaning of "depended on by the most files".
    `tied_at_cutoff` says when the module just past the cut has the same count as the last one listed, so a
    caller can tell an arbitrary tie-break from a clear top.
    """
    limit = max(1, min(int(limit), _MOST_IMPORTED_MAX))
    prefix = (scope or "").strip("/")
    counted: list[tuple[int, str]] = []
    for module in evidence["repository"]["modules"]:
        path = module.get("path") or ""
        if prefix and path != prefix and not path.startswith(prefix + "/"):
            continue
        if _BLAST_TEST_PATH.search(path):
            continue
        importers = {p for p in (module.get("imported_by") or []) if p != path}
        if ignore_test_importers:
            importers = {p for p in importers if not _BLAST_TEST_PATH.search(p)}
        if importers:
            counted.append((len(importers), path))
    counted.sort(key=lambda item: (-item[0], item[1]))
    top = counted[:limit]
    return {
        "scope": prefix or "(whole repo)",
        "definition": "fan-in: the number of distinct modules that import the file; test files are not candidates",
        "importers_counted": "non-test only" if ignore_test_importers else "all",
        "limit": limit,
        "most_imported": [{"path": path, "imported_by_count": count} for count, path in top],
        "tied_at_cutoff": len(counted) > limit and counted[limit][0] == top[-1][0] if top else False,
        "candidates_with_importers": len(counted),
    }


def _strongly_connected(nodes: set[str], graph: dict[str, list[str]]) -> list[list[str]]:
    """Tarjan's algorithm, iterative so a deep import chain cannot hit the recursion limit."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[list[str]] = []
    counter = 0
    for root in sorted(nodes):
        if root in index:
            continue
        work = [(root, iter(graph.get(root, ())))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in nodes:
                    continue
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(graph.get(child, ()))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(sorted(component))
    return components


def _shortest_cycle(component: list[str], graph: dict[str, list[str]]) -> list[str]:
    """A shortest cycle inside one strongly connected component, as an ordered list: each module imports the next
    and the last imports the first."""
    members = set(component)
    best: list[str] = []
    for start in component[:60]:  # a sample is enough to find a short one; the cost stays bounded for huge components
        parent = {start: None}
        queue = deque([start])
        found = None
        while queue and found is None:
            node = queue.popleft()
            for child in graph.get(node, ()):
                if child not in members:
                    continue
                if child == start:
                    found = node
                    break
                if child not in parent:
                    parent[child] = node
                    queue.append(child)
        if found is None:
            continue
        path = [found]
        while parent[path[-1]] is not None:
            path.append(parent[path[-1]])
        path.reverse()
        if not best or len(path) < len(best):
            best = path
        if len(best) == 2:
            break
    return best


_CYCLES_LISTED = 5
_CYCLE_MEMBERS_LISTED = 25


def import_cycle_components(
    evidence: dict, prefix: str = "", ignore_test_files: bool = False
) -> tuple[list[list[str]], list[str], dict[str, list[str]]]:
    """Uncapped cycle data behind `find_import_cycles`: (groups of 2+ mutually importing modules, self-importing
    modules, the import graph followed). Nothing here is truncated for listing."""
    modules = evidence["repository"]["modules"]
    keep: set[str] = set()
    for module in modules:
        path = module.get("path") or ""
        if prefix and path != prefix and not path.startswith(prefix + "/"):
            continue
        if ignore_test_files and _BLAST_TEST_PATH.search(path):
            continue
        keep.add(path)
    # Edges the scan marks "inferred" (build-tag alternatives, an ambiguous resolution) are not followed: a loop that
    # exists only through alternative builds of one file is not a real circular import.
    graph = {
        m["path"]: [
            i for i in (m.get("imports") or [])
            if i in keep and (m.get("import_confidence") or {}).get(i) != "inferred"
        ]
        for m in modules
        if m.get("path") in keep
    }
    groups = [c for c in _strongly_connected(keep, graph) if len(c) >= 2]
    self_importers = sorted(p for p, targets in graph.items() if p in targets)
    return groups, self_importers, graph


def find_import_cycles(evidence: dict, scope: str | None = None, ignore_test_files: bool = False) -> dict:
    """Circular imports: groups of modules that import each other, directly or through a chain.

    Each group is a strongly connected component of the import graph with at least two modules (or a module that
    imports itself). For every group an ordered `example_cycle` is given (each module imports the next, the last
    imports the first), the shortest one found. Test files are included unless `ignore_test_files` (a "non-test"
    question) removes them from the graph. `scope` limits the graph to a directory.
    """
    prefix = (scope or "").strip("/")
    groups, self_importers, graph = import_cycle_components(evidence, prefix, ignore_test_files)
    groups.sort(key=lambda c: (-len(c), c[0]))
    listed = []
    for component in groups[:_CYCLES_LISTED]:
        listed.append(
            {
                "size": len(component),
                "example_cycle": _shortest_cycle(component, graph),
                "members": component[:_CYCLE_MEMBERS_LISTED],
                "members_truncated": len(component) > _CYCLE_MEMBERS_LISTED,
            }
        )
    return {
        "scope": prefix or "(whole repo)",
        "test_files": "excluded" if ignore_test_files else "included",
        "edges_followed": "certain imports only (inferred edges, such as build-tag alternatives, are not followed)",
        "has_cycle": bool(groups or self_importers),
        "cycle_groups_total": len(groups),
        "self_importing_modules": self_importers[:20],
        "cycle_groups": listed,
        "cycle_groups_truncated": len(groups) > _CYCLES_LISTED,
    }


def find_database(evidence: dict, target: str | None) -> dict:
    return evidence["repository"]["database"]


def find_infrastructure(evidence: dict, target: str | None) -> dict:
    return evidence["repository"]["infrastructure"]


def find_error_handling(evidence: dict, target: str | None) -> dict:
    # Evidence written before 0.8.0 has no such section; say so instead of raising.
    return evidence["repository"].get(
        "error_handling", {"checked": False, "reason": "this evidence predates error-handling data; rescan"}
    )


def find_environment_variables(evidence: dict, target: str | None) -> dict:
    return evidence["repository"]["environment_variables"]


def find_hotspots(evidence: dict, target: str | None) -> list[dict]:
    return evidence["git"].get("hotspots", [])


def list_modules(evidence: dict) -> list[str]:
    return [module["path"] for module in evidence["repository"]["modules"]]


def list_clusters(evidence: dict) -> list[dict]:
    return [
        {"id": cluster["id"], "module_count": len(cluster["modules"])}
        for cluster in evidence["architecture"]["clusters"]
    ]


def list_branches(evidence: dict) -> list[str]:
    # A repo with no commits yields git == {"available": False} - see
    # air_schema.py's git section docstring. There are no branches to list
    # in that case, but that's honestly indistinguishable from "no branches
    # were found" via a plain list return; callers that need to tell those
    # apart should use aletheore_overview's git.available instead.
    git = evidence["git"]
    if git.get("available") is False:
        return []
    return [branch["name"] for branch in git["branches"]]


def find_repo_overview(evidence: dict) -> dict:
    repo = evidence["repository"]
    git = evidence["git"]
    arch = evidence["architecture"]
    dependency_graph = repo["dependency_graph"]
    # A repo with no commits yields git == {"available": False} and nothing
    # else (see air_schema.py). Only `available` is safe to read
    # unconditionally there - signal that honestly instead of indexing into
    # keys that don't exist or silently reporting zero commits, which would
    # be indistinguishable from a repo that genuinely has zero commits.
    if git.get("available") is False:
        git_summary: dict = {"available": False}
    else:
        git_summary = {
            "repo_age_days": git["repo_age_days"],
            "total_commits": git["total_commits"],
            "commit_cadence": git["commit_cadence"],
            "branch_count": len(git["branches"]),
        }
    return {
        "languages": repo["languages"],
        "frameworks": repo["frameworks"],
        "monorepo": repo["monorepo"],
        "dependency_graph_summary": {
            "node_count": len(dependency_graph["nodes"]),
            "edge_count": len(dependency_graph["edges"]),
        },
        "module_count": len(repo["modules"]),
        "cluster_count": len(arch["clusters"]),
        "cross_cluster_edge_count": len(arch["cross_cluster_edges"]),
        "git": git_summary,
    }


QUERY_FUNCTIONS: dict[str, tuple[Callable[[dict, str | None], Any], bool]] = {
    "imports": (find_imports, True),
    "imported-by": (find_imported_by, True),
    "symbols": (find_symbols, True),
    "branch": (find_branch, True),
    "ownership": (find_ownership, False),
    "secrets": (find_secrets_for_file, True),
    "vulnerabilities": (find_vulnerabilities, False),
    "licenses": (find_licenses, False),
    "static-analysis": (find_static_analysis, False),
    "endpoints": (find_endpoints, False),
    "cluster": (find_cluster, True),
    "layer-violations": (find_layer_violations, False),
    "dead-code": (find_dead_code_evidence, False),
    "hotspots": (find_hotspots, False),
    "database": (find_database, False),
    "infrastructure": (find_infrastructure, False),
    "environment-variables": (find_environment_variables, False),
    "error-handling": (find_error_handling, False),
    "evidence-for-endpoint": (find_code_evidence_for_endpoint, True),
    "evidence-for-symbol": (find_code_evidence_for_symbol, True),
    "evidence-for-dependency": (find_code_evidence_for_dependency, True),
}
