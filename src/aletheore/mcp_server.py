import json
import multiprocessing
import os
import queue
import re
import sys
from pathlib import Path, PurePath

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from aletheore.adapters.base import AgentAdapter
from aletheore.answer import answer_question
from aletheore.credentials import get_api_key
from aletheore.evidence import (
    IncompatibleEvidenceVersionError,
    MalformedEvidenceError,
    load_evidence_file,
    scan_repository,
    write_evidence,
)
from aletheore.healthcheck import run_healthcheck, save_healthcheck
from aletheore.history import compute_diff, list_snapshots, save_snapshot
from aletheore.managed_audit_client import run_managed_audit_request
from aletheore.query import (
    ModuleNotFoundInEvidenceError,
    QUERY_FUNCTIONS,
    find_blast_radius,
    find_code_evidence_for_dependency,
    find_code_evidence_for_endpoint,
    find_code_evidence_for_symbol,
    find_cluster,
    find_imported_by,
    find_imports,
    find_repo_overview,
    find_symbol_source,
    list_branches,
    list_clusters,
    list_modules,
)
from aletheore.repo_config import load_repo_config
from aletheore.secrets import iter_all_files
from aletheore.search_index import IndexDimensionMismatchError, IndexNotFoundError, search_index
from aletheore.toon_encoding import ToonEncodingError, to_toon


# repo_path -> ((mtime, size) of the evidence file at load time, parsed
# evidence dict). An MCP server process lives for the whole session, and
# every tool call was re-reading and re-parsing the entire evidence file
# from scratch - for a large repo (hundreds of MB of JSON) that's real,
# entirely avoidable latency on every single tool call. Keyed by (mtime,
# size) rather than unconditional for the process lifetime so a fresh
# `aletheore scan` (or the aletheore_scan tool) invalidates it automatically
# - mtime alone can be too coarse-grained on some filesystems to catch a
# rewrite that lands in the same second, size is a cheap extra check from
# the same stat() call. Nothing here ever mutates the returned dict, so
# sharing one parsed copy across calls is safe.
_evidence_cache: dict[Path, tuple[tuple[float, int], dict]] = {}


def read_evidence(repo_path: Path) -> dict:
    evidence_path = repo_path / ".aletheore" / "air.json"
    if not evidence_path.exists():
        raise FileNotFoundError(
            f"no evidence found at {evidence_path} - run 'aletheore scan {repo_path}' first "
            "or call the aletheore_scan tool"
        )
    stat = evidence_path.stat()
    cache_key = (stat.st_mtime, stat.st_size)
    cached = _evidence_cache.get(repo_path)
    if cached is not None and cached[0] == cache_key:
        return cached[1]
    # Routes through the same version-compatibility AND schema-shape checks
    # every other evidence reader (CLI query/index/diff/healthcheck) uses,
    # rather than a bare json.loads - a truncated or hand-edited air.json
    # with a *compatible* version used to pass straight through here and
    # only surface as a raw KeyError deep inside whichever tool first
    # touched the missing/wrong field, instead of one clear, actionable
    # error up front (IncompatibleEvidenceVersionError / MalformedEvidenceError,
    # both re-exported from aletheore.evidence so callers of either module
    # catch the same exception types).
    evidence = load_evidence_file(evidence_path)
    _evidence_cache[repo_path] = (cache_key, evidence)
    return evidence


# Surfaced in the MCP `initialize` handshake itself - every client shows
# this to the connecting agent before any tool is called, unlike a resource
# the agent would have to separately think to fetch. Content sourced from
# real, measured facts rather than generic advice: the vocabulary claim is
# our own benchmark's finding (aletheore-benchmarks, "Where we lose" -
# several corpora scored below 35% top-1 under vocabulary-avoiding phrasing,
# recovering 20-47 points when the same questions used the project's own
# terms), not a guess. Kept to what changes agent behavior, not a full tool
# catalog - each tool's own docstring is already visible to the client.
SERVER_INSTRUCTIONS = """Aletheore is a deterministic, evidence-grounded code intelligence tool for \
this repository - every result cites a real file:line, nothing is invented.

Getting started on an unfamiliar repo: call aletheore_overview first. If no \
evidence exists yet, run aletheore_scan once - the deterministic parse and \
dependency-graph pass every other tool reads from. aletheore_search_codebase \
and aletheore_answer additionally require aletheore_index to have run first \
(builds the semantic index on top of scan evidence); every other tool works \
straight off scan evidence alone.

Timing: aletheore_scan and aletheore_index both report live progress while \
running, not a silent hang - a small repo finishes in seconds, a large \
monorepo can take several minutes. Don't assume either has failed just \
because it's still running; check the reported progress before retrying.

Before your first aletheore_search or aletheore_search_codebase/aletheore_answer \
call: if you don't already know the exact identifier, file name, or term \
you're looking for, do not guess a paraphrase and query with it first. \
aletheore_search is literal/regex - a paraphrase there doesn't score lower, \
it matches nothing at all, since the tool never sees your intent, only the \
literal string. aletheore_search_codebase and aletheore_answer are semantic \
and degrade more gracefully, but our own published benchmark still measured \
real, large accuracy drops from paraphrasing - several corpora scored under \
35% top-1 accuracy on vocabulary-avoiding phrasing, recovering 20-47 points \
when the same question used the project's own terms instead. So: call \
aletheore_overview, aletheore_symbols, or aletheore_list FIRST to find the \
repo's actual identifiers, file names, and terminology, THEN query with \
those - not a generic description of what you think the code might be \
called. This ordering matters even when you're fairly confident in a guess; \
confirming the real name first is cheap, a wasted or degraded query is not.

Prefer exact tools when the target is already known: aletheore_imports, \
aletheore_imported_by, aletheore_symbols, aletheore_symbol_source, \
aletheore_neighborhood, and the aletheore_find_evidence_for_* tools are \
exact (not approximate) and need no semantic index. So are the security/ \
quality tools (aletheore_secrets, aletheore_vulnerabilities, \
aletheore_licenses, aletheore_dead_code, aletheore_hotspots, \
aletheore_layer_violations). Reach for aletheore_search_codebase/ \
aletheore_answer only for open-ended "how does this work" questions the \
exact tools can't answer directly."""


def _toon_result(data: object) -> str:
    # Every tool result is TOON-encoded rather than returned as a plain dict
    # (which MCPServer would otherwise auto-serialize to JSON) - this is the
    # actual token-cost surface for whatever agent is calling these tools,
    # and evidence's own shape (uniform arrays of same-shaped objects almost
    # everywhere) is exactly TOON's best case. Falls back to plain JSON on a
    # TOON encoding failure rather than raising - a tool call returning a
    # slightly less compact result beats it crashing outright.
    try:
        return to_toon({"result": data})
    except ToonEncodingError:
        return json.dumps({"result": data})


_TOOL_NAME_TO_QUERY_KIND = {
    "aletheore_imports": "imports",
    "aletheore_imported_by": "imported-by",
    "aletheore_symbols": "symbols",
    "aletheore_branch": "branch",
    "aletheore_ownership": "ownership",
    "aletheore_secrets": "secrets",
    "aletheore_vulnerabilities": "vulnerabilities",
    "aletheore_licenses": "licenses",
    "aletheore_endpoints": "endpoints",
    "aletheore_cluster": "cluster",
    "aletheore_layer_violations": "layer-violations",
    "aletheore_dead_code": "dead-code",
    "aletheore_hotspots": "hotspots",
    "aletheore_database": "database",
    "aletheore_infrastructure": "infrastructure",
    "aletheore_environment_variables": "environment-variables",
}

# One real description per query kind, naming exactly what `target` expects
# where the underlying query function actually takes one (see
# QUERY_FUNCTIONS' requires_target flag in query.py) - a shared templated
# docstring left every one of these indistinguishable to a calling LLM,
# which had no way to tell "target is a file path" from "target is a
# branch name" from "this tool takes no target at all".
_QUERY_TOOL_DESCRIPTIONS = {
    "imports": "This module's own list of imports. target: file path exactly as it appears in evidence (e.g. 'src/app.py').",
    "imported-by": "Which modules import this one. target: file path exactly as it appears in evidence.",
    "symbols": "This module's extracted functions and classes. target: file path exactly as it appears in evidence.",
    "branch": "Git branch metadata (head commit, tracking info). target: branch name (e.g. 'main').",
    "ownership": "Code ownership derived from git blame history. Optional target: a file path "
    "exactly as it appears in evidence, for that file's ownership; omit for repo-wide aggregate.",
    "secrets": "Secret-scanner findings for one file. target: file path exactly as it appears in evidence.",
    "vulnerabilities": "All dependency vulnerability findings for this repo. Takes no target.",
    "licenses": "All dependency license findings for this repo. Takes no target.",
    "endpoints": "All API endpoints mapped from source. Takes no target.",
    "cluster": "The architecture cluster containing this module. target: file path exactly as it appears in evidence.",
    "layer-violations": "Layer-convention violations detected in the architecture. Takes no target.",
    "dead-code": "Unreferenced functions/classes and unused dependencies. Takes no target.",
    "hotspots": "Files with the most git churn/co-change activity. Takes no target.",
    "database": "Detected database usage - ORMs, connection strings, migrations. Takes no target.",
    "infrastructure": "Detected infrastructure config - Docker, CI, IaC files. Takes no target.",
    "environment-variables": "Environment variables referenced in the codebase. Takes no target.",
}

_SEARCH_MATCH_CAP = 200
# Guards the whole regex search against catastrophic backtracking (e.g.
# (a+)+$) on a crafted line - measured ~23s for one such 29-char line
# against every line of every file. Two timeout mechanisms were tried and
# rejected before this one, both confirmed empirically rather than assumed:
#   - A thread-based timeout (submit to a worker, future.result(timeout=..))
#     does not work: CPython's _sre C extension holds the GIL for the whole
#     match, so the waiting thread can't wake up to check its own clock
#     until the runaway match finally releases it. A 1s future.result
#     timeout still blocked for the full ~45s match.
#   - signal.alarm does interrupt a runaway match (SIGALRM delivery is
#     checked by the interpreter even mid-match), but signal.signal() only
#     works on the process's main thread - and this tool is invoked from a
#     worker thread the MCP framework dispatches sync tool calls onto, not
#     the main thread, so it raised ValueError every time in practice.
# A separate process is the only mechanism that isn't at the mercy of the
# GIL or which thread called in: the OS can terminate it regardless of what
# it's doing. The whole search runs as one worker (not one process per
# line - that overhead would dominate for any real search) under a single
# overall deadline; on timeout the process is killed and the tool reports
# what happened rather than returning results.
_SEARCH_TIMEOUT_SECONDS = 5.0

# _SEARCH_MATCH_CAP alone doesn't bound the result's total size - 200
# matches of long lines (a minified bundle, a generated file, a single huge
# JSON line) can still produce an oversized result even under the count
# cap. Confirmed live: an unscoped literal search on a common word returned
# a result the calling MCP client rejected outright for exceeding its own
# ~390,000-char limit, with 200 matches well under the count cap. Both caps
# below exist because either alone is insufficient - a handful of huge
# lines defeats the count cap (this repro: 200 matches x 3000-char lines =
# ~600,000 chars), and a huge number of merely-long lines would defeat a
# per-line cap without an aggregate budget too.
_SEARCH_LINE_MAX_CHARS = 500
_SEARCH_TOTAL_CHAR_BUDGET = 100_000


def _search_files(repo_path: Path, pattern: str, regex: bool, path_glob: str | None) -> dict:
    """The actual search. Literal (non-regex) mode has no backtracking risk
    and is called directly in-process; regex mode is only ever called
    through _run_search in a subprocess (see _SEARCH_TIMEOUT_SECONDS)."""
    compiled = re.compile(pattern) if regex else None
    matches: list[dict] = []
    truncated = False
    total_chars = 0
    ignored_paths = load_repo_config(repo_path)["ignored_paths"]

    for path in iter_all_files(repo_path, ignored_paths):
        rel_path = path.relative_to(repo_path).as_posix()
        if path_glob is not None and not PurePath(rel_path).match(path_glob):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for line_no, line in enumerate(text.splitlines(), start=1):
            found = compiled.search(line) is not None if compiled else pattern in line
            if found:
                if len(matches) >= _SEARCH_MATCH_CAP or total_chars >= _SEARCH_TOTAL_CHAR_BUDGET:
                    truncated = True
                    break
                line_text = (
                    line[:_SEARCH_LINE_MAX_CHARS] + "... (line truncated)"
                    if len(line) > _SEARCH_LINE_MAX_CHARS
                    else line
                )
                matches.append({"path": rel_path, "line": line_no, "text": line_text})
                total_chars += len(line_text)
        if truncated:
            break

    return {"matches": matches, "truncated": truncated}


def _run_search(
    repo_path: Path, pattern: str, regex: bool, path_glob: str | None, result_queue: "multiprocessing.Queue"
) -> None:
    """Runs in a child process - must stay a top-level function so the
    spawn start method can pickle and import it."""
    result_queue.put(_search_files(repo_path, pattern, regex, path_glob))


# ---------------------------------------------------------------------------
# Effect classes and the consent boundary.
#
# Most tools here just read .aletheore/air.json. A few do more: write files,
# reach the network, or transmit this repository's evidence to a third party.
# Until this, nothing distinguished them - an agent driving the server saw one
# undifferentiated list and could trigger a scan, a live HTTP probe, or an
# upload to the hosted audit service without anyone having agreed to it.
#
# Two mechanisms, because one alone isn't enough:
#
#   1. ToolAnnotations on every tool. This is MCP's own vocabulary, so clients
#      already know how to read and display it. But the spec is explicit that
#      annotations are *hints* - "clients should never make tool use decisions
#      based on ToolAnnotations received from untrusted servers" - so they
#      describe behavior, they don't constrain it.
#
#   2. ALETHEORE_MCP_ALLOW, below. This is the part that actually binds: a
#      tool whose effects aren't permitted is never registered, so it isn't in
#      the tool list and cannot be called at all.
# ---------------------------------------------------------------------------

EFFECT_WRITE = "write"  # writes files under the repo
EFFECT_NETWORK = "network"  # outbound requests (OSV, registries, health probes, embeddings)
EFFECT_EXTERNAL = "external"  # transmits repository evidence to a third-party service

_ALL_EFFECTS = frozenset({EFFECT_WRITE, EFFECT_NETWORK, EFFECT_EXTERNAL})

# Reading evidence is the server's reason to exist and is always permitted, so
# it isn't an effect class - the empty set means "read-only".
#
# `external` is the one class off by default. Scanning and indexing are what a
# tool called "aletheore" is for, and their effects stay on this machine; the
# genuinely surprising action is this repository's evidence leaving it. That
# happens with no launch-time consent step today, because the managed-audit
# tool silently resolves a token from the OS keychain - a user who once ran
# `aletheore login` has an agent that can upload without ever being asked.
#
# (aletheore_answer also reaches an LLM, but it is registered only when the
# operator passes `aletheore mcp --agent`, which is itself the consent step.)
_DEFAULT_ALLOWED_EFFECTS = frozenset({EFFECT_WRITE, EFFECT_NETWORK})

_ALLOW_ENV_VAR = "ALETHEORE_MCP_ALLOW"


class UnknownEffectError(ValueError):
    pass


def allowed_effects(raw: str | None) -> frozenset[str]:
    """Parse ALETHEORE_MCP_ALLOW into the permitted effect classes.

    Unset uses the default. An explicit value replaces it wholesale rather
    than adding to it, so `ALETHEORE_MCP_ALLOW=read` is a genuinely read-only
    server rather than the default plus a redundant token. An unrecognized
    name is an error rather than a silent no-op: a typo like "extenral" that
    quietly left evidence upload disabled would be merely confusing, but one
    that quietly left it *enabled* would be a security hole with a plausible
    explanation attached.
    """
    if raw is None:
        return _DEFAULT_ALLOWED_EFFECTS
    names = {part.strip().lower() for part in raw.split(",") if part.strip()}
    # "read" is always implied; accept it as a spelling of "nothing else".
    names.discard("read")
    unknown = names - _ALL_EFFECTS
    if unknown:
        raise UnknownEffectError(
            f"{_ALLOW_ENV_VAR} contains unknown effect(s): {', '.join(sorted(unknown))} - "
            f"valid values are read, {', '.join(sorted(_ALL_EFFECTS))}"
        )
    return frozenset(names)


READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def _register_query_wrapper_tools(mcp_instance: MCPServer, repo_path: Path) -> None:
    # Query kinds whose function treats `target` as an optional refinement (None ->
    # repo-wide aggregate, a real value -> scoped result) rather than an unused
    # parameter kept only for signature uniformity - find_ownership branches on
    # target to return evidence["git"]["file_ownership"][target] instead of the
    # aggregate. The CLI's _query already forwards target for every kind
    # regardless of requires_target, so this only affects the generated MCP tool's
    # signature: without it, requires_target=False (correct - target was never
    # mandatory for ownership) meant the wrapper generated a zero-argument tool(),
    # so an MCP caller could never pass a target at all, even though the
    # underlying query already supported one.
    optional_target_kinds = {"ownership"}

    for tool_name, kind in _TOOL_NAME_TO_QUERY_KIND.items():
        func, requires_target = QUERY_FUNCTIONS[kind]

        def make_tool(func=func, requires_target=requires_target, kind=kind):
            if requires_target:

                def tool(target: str) -> str:
                    evidence = read_evidence(repo_path)
                    return _toon_result(func(evidence, target))

            elif kind in optional_target_kinds:

                def tool(target: str | None = None) -> str:
                    evidence = read_evidence(repo_path)
                    return _toon_result(func(evidence, target))

            else:

                def tool() -> str:
                    evidence = read_evidence(repo_path)
                    return _toon_result(func(evidence, None))

            return tool

        tool_func = make_tool()
        tool_func.__name__ = tool_name
        tool_func.__doc__ = _QUERY_TOOL_DESCRIPTIONS[kind]
        mcp_instance.tool(name=tool_name, annotations=READ_ONLY_ANNOTATIONS)(tool_func)


def _register_changes_tool(mcp_instance: MCPServer, repo_path: Path) -> None:
    @mcp_instance.tool(name="aletheore_changes", annotations=READ_ONLY_ANNOTATIONS)
    def aletheore_changes(full: bool = False) -> str:
        """What changed between the two most recent scans of this repo."""
        snapshots = list_snapshots(repo_path)
        if len(snapshots) < 2:
            return _toon_result({"message": "no prior snapshot to compare against"})
        try:
            old = load_evidence_file(snapshots[-2])
            new = load_evidence_file(snapshots[-1])
        except json.JSONDecodeError:
            return _toon_result({"message": f"most recent snapshot is unreadable ({snapshots[-2]})"})
        except (IncompatibleEvidenceVersionError, MalformedEvidenceError) as exc:
            return _toon_result({"error": str(exc)})
        return _toon_result(compute_diff(old, new, full=full))


def _register_neighborhood_tool(mcp_instance: MCPServer, repo_path: Path) -> None:
    @mcp_instance.tool(name="aletheore_neighborhood", annotations=READ_ONLY_ANNOTATIONS)
    def aletheore_neighborhood(target: str) -> str:
        """A module's imports, dependents, and cluster in one call."""
        evidence = read_evidence(repo_path)
        imports = find_imports(evidence, target)
        imported_by = find_imported_by(evidence, target)
        try:
            cluster = find_cluster(evidence, target)
        except ModuleNotFoundInEvidenceError:
            cluster = None
        return _toon_result(
            {
                "target": target,
                "imports": imports,
                "imported_by": imported_by,
                "cluster": cluster,
            }
        )


def _register_blast_radius_tool(mcp_instance: MCPServer, repo_path: Path) -> None:
    @mcp_instance.tool(name="aletheore_get_blast_radius", annotations=READ_ONLY_ANNOTATIONS)
    def aletheore_get_blast_radius(target: str, symbol: str | None = None) -> str:
        """Everything that would be affected by changing `target` (a file
        path) - one call instead of manually chasing aletheore_imported_by
        recursively. Direct AND transitive dependents (a real multi-hop
        walk, not just one level like aletheore_neighborhood), plus any
        existing layer-boundary violations already touching a module in
        that blast radius.

        Pass `symbol` (a function/class name defined in target) to also get
        confirmed_callers: which of the direct dependents actually CALL
        that symbol, verified against their real file content - not just
        "imports the file", which says nothing about which of possibly
        many exported names is actually used.

        layer_violations are EXISTING violations already on record, not a
        prediction of what a signature change to `symbol` would newly
        break - this repo has no per-call-site type/signature data to
        simulate that against.
        """
        evidence = read_evidence(repo_path)
        return _toon_result(find_blast_radius(evidence, repo_path, target, symbol))


_LIST_KIND_TO_FUNCTION = {
    "modules": list_modules,
    "clusters": list_clusters,
    "branches": list_branches,
}


def _register_list_tool(mcp_instance: MCPServer, repo_path: Path) -> None:
    @mcp_instance.tool(name="aletheore_list", annotations=READ_ONLY_ANNOTATIONS)
    def aletheore_list(kind: str) -> str:
        """Lists the valid names/identifiers for one evidence collection, so
        other tools' exact-match `target` arguments can be filled in
        correctly. kind: one of 'modules' (file paths, for aletheore_imports/
        _imported_by/_symbols/_secrets/_cluster/_neighborhood/_symbol_source's
        module argument), 'clusters' (architecture cluster ids), or
        'branches' (git branch names, for aletheore_branch)."""
        func = _LIST_KIND_TO_FUNCTION.get(kind)
        if func is None:
            return _toon_result(
                {"error": f"unknown kind {kind!r} - expected one of {sorted(_LIST_KIND_TO_FUNCTION)}"}
            )
        evidence = read_evidence(repo_path)
        return _toon_result(func(evidence))


def _register_overview_tool(mcp_instance: MCPServer, repo_path: Path) -> None:
    @mcp_instance.tool(name="aletheore_overview", annotations=READ_ONLY_ANNOTATIONS)
    def aletheore_overview() -> str:
        """A repo-level summary: languages, frameworks, monorepo structure,
        dependency-graph size, module/cluster counts, and git age/commit
        cadence/branch count. The starting point for 'what is this repo?' -
        call this before anything else on an unfamiliar repository."""
        evidence = read_evidence(repo_path)
        return _toon_result(find_repo_overview(evidence))


def _register_search_tool(mcp_instance: MCPServer, repo_path: Path) -> None:
    @mcp_instance.tool(name="aletheore_search", annotations=READ_ONLY_ANNOTATIONS)
    def aletheore_search(pattern: str, regex: bool = False, path_glob: str | None = None) -> str:
        """Deterministic literal or regex search over the repository's source files."""
        if not regex:
            return _toon_result(_search_files(repo_path, pattern, regex, path_glob))

        try:
            re.compile(pattern)
        except re.error as exc:
            return _toon_result({"error": f"invalid regex pattern: {exc}"})

        ctx = multiprocessing.get_context("spawn")
        result_queue: multiprocessing.Queue = ctx.Queue()
        process = ctx.Process(
            target=_run_search, args=(repo_path, pattern, regex, path_glob, result_queue)
        )
        process.start()
        try:
            result = result_queue.get(timeout=_SEARCH_TIMEOUT_SECONDS)
        except queue.Empty:
            process.terminate()
            process.join(timeout=1.0)
            if process.is_alive():
                process.kill()
            return _toon_result(
                {
                    "error": (
                        f"search exceeded its {_SEARCH_TIMEOUT_SECONDS:.0f}s time budget - "
                        "likely a catastrophic-backtracking regex pattern; try a simpler "
                        "pattern or a literal (non-regex) search"
                    )
                }
            )
        process.join()
        return _toon_result(result)


def _register_ast_pattern_tool(mcp_instance: MCPServer, repo_path: Path) -> None:
    @mcp_instance.tool(name="aletheore_ast_pattern", annotations=READ_ONLY_ANNOTATIONS)
    def aletheore_ast_pattern(language: str, query: str) -> str:
        """Structural search: find code by shape rather than by words, via a
        raw tree-sitter S-expression query - e.g. functions that catch a
        specific exception type, or classes implementing a given interface,
        regardless of naming. language is one of the scanner's supported
        languages (python, javascript, typescript, go, rust, java, kotlin,
        ruby, php, c, cpp, csharp, swift). Re-parses source from disk at
        call time - unlike most other tools here, this does not read
        air.json, since a structural match needs the actual parse tree,
        which air.json never stores. Only whatever the query itself names
        with @capture is returned - a query with no captures matches
        structure but reports no text or location, so name at least one
        node you care about. Returns {matches, truncated} - a broad
        structural query is capped by match count and total captured
        characters; truncated=true means real results exist beyond what's
        returned, so narrow the query rather than assume it's exhaustive."""
        from aletheore.ast_pattern import InvalidPatternError, UnknownLanguageError, search_ast_pattern

        try:
            return _toon_result(search_ast_pattern(repo_path, language, query))
        except UnknownLanguageError as exc:
            return _toon_result({"error": str(exc)})
        except InvalidPatternError as exc:
            return _toon_result({"error": f"invalid tree-sitter query: {exc}"})


def _register_symbol_source_tool(mcp_instance: MCPServer, repo_path: Path) -> None:
    @mcp_instance.tool(name="aletheore_symbol_source", annotations=READ_ONLY_ANNOTATIONS)
    def aletheore_symbol_source(module: str, symbol: str) -> str:
        """Exact source text for one named function/class, with resolved line bounds.

        Two separate arguments, not a combined "path::name" - module: the
        file path exactly as it appears in evidence (e.g. "src/app.py").
        symbol: the function or class name alone (e.g. "my_function")."""
        evidence = read_evidence(repo_path)
        return _toon_result(find_symbol_source(evidence, repo_path, module, symbol))


def _register_verify_citations_tool(mcp_instance: MCPServer, repo_path: Path) -> None:
    @mcp_instance.tool(name="aletheore_verify_citations", annotations=READ_ONLY_ANNOTATIONS)
    def aletheore_verify_citations(report_text: str) -> str:
        """Checks every `file:line` citation in report_text against this
        repo's real evidence and real file line counts. Call this on any
        report you write before presenting it - a citation naming a file
        that isn't in evidence, or a line beyond the file's real length, is
        flagged as unverified rather than trusted."""
        from aletheore.citation_verifier import local_line_count_fetcher, verify_citations

        evidence = read_evidence(repo_path)
        result = verify_citations(
            report_text, evidence, fetch_line_count=local_line_count_fetcher(repo_path)
        )
        return _toon_result(result)


def _register_code_evidence_tools(mcp_instance: MCPServer, repo_path: Path) -> None:
    @mcp_instance.tool(name="aletheore_find_evidence_for_endpoint", annotations=READ_ONLY_ANNOTATIONS)
    def aletheore_find_evidence_for_endpoint(method: str, path: str) -> str:
        """Resolve an API endpoint to source evidence: file, line, symbol, owner, commit, dependency, and risk.

        Two separate arguments - method: the HTTP verb (e.g. "GET").
        path: the route path exactly as it appears in evidence (e.g.
        "/users/{id}"), not combined with the method into one string."""
        evidence = read_evidence(repo_path)
        return _toon_result(find_code_evidence_for_endpoint(evidence, f"{method} {path}", repo_path))

    @mcp_instance.tool(name="aletheore_find_evidence_for_symbol", annotations=READ_ONLY_ANNOTATIONS)
    def aletheore_find_evidence_for_symbol(symbol: str) -> str:
        """Resolve a function or class symbol to source evidence."""
        evidence = read_evidence(repo_path)
        return _toon_result(find_code_evidence_for_symbol(evidence, symbol, repo_path))

    @mcp_instance.tool(name="aletheore_find_evidence_for_dependency", annotations=READ_ONLY_ANNOTATIONS)
    def aletheore_find_evidence_for_dependency(dependency: str) -> str:
        """Resolve a dependency or import to source evidence."""
        evidence = read_evidence(repo_path)
        return _toon_result(find_code_evidence_for_dependency(evidence, dependency, repo_path))


def _scan_summary(evidence: dict) -> dict:
    secret_findings = evidence["security"]["secrets"]["findings"]
    history_findings = evidence["security"]["secrets"]["history_findings"]
    return {
        "scanned_at": evidence["scanned_at"],
        "module_count": len(evidence["repository"]["modules"]),
        "cluster_count": len(evidence["architecture"]["clusters"]),
        "secrets": {
            "total_findings": len(secret_findings),
            "real_findings": len(
                [
                    finding
                    for finding in secret_findings
                    if not finding.get("likely_placeholder") and not finding.get("accepted")
                ]
            ),
            "history_findings": len(history_findings),
        },
        "vulnerabilities": {
            "checked": evidence["security"]["dependency_vulnerabilities"]["checked"],
            "finding_count": len(evidence["security"]["dependency_vulnerabilities"]["findings"]),
        },
        "layer_violations": {
            "convention_detected": evidence["architecture"]["layer_violations"][
                "convention_detected"
            ],
            "violation_count": len(evidence["architecture"]["layer_violations"]["violations"]),
        },
    }


def _register_scan_tool(mcp_instance: MCPServer, repo_path: Path) -> None:
    @mcp_instance.tool(
        name="aletheore_scan",
        # Rewrites .aletheore/ and appends a snapshot, and reaches OSV.dev and
        # package registries unless those checks are disabled. Not destructive
        # (everything it overwrites it derived) and not idempotent (each run
        # adds a snapshot and a fresh timestamp).
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
        ),
    )
    def aletheore_scan(
        check_vulnerabilities: bool = True,
        scan_git_history: bool = True,
        check_licenses: bool = True,
        map_endpoints: bool = True,
    ) -> str:
        """Run the deterministic Aletheore scanner and save evidence for this repository."""
        evidence = scan_repository(
            repo_path,
            check_vulnerabilities=check_vulnerabilities,
            scan_git_history=scan_git_history,
            check_licenses=check_licenses,
            map_endpoints=map_endpoints,
        )
        write_evidence(evidence, repo_path)
        save_snapshot(evidence, repo_path)
        return _toon_result(_scan_summary(evidence))


def _register_healthcheck_tool(mcp_instance: MCPServer, repo_path: Path) -> None:
    @mcp_instance.tool(
        name="aletheore_healthcheck",
        # Issues live GETs against a caller-supplied base_url and writes the
        # result. The most openly world-affecting tool here: the target is an
        # argument, so it can be pointed anywhere the host can reach.
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
        ),
    )
    def aletheore_healthcheck(base_url: str) -> str:
        """GET-only live health check of mapped API endpoints against a running instance."""
        evidence = read_evidence(repo_path)
        endpoints = evidence["repository"].get("api_endpoints", {}).get("endpoints", [])
        try:
            result = run_healthcheck(endpoints, base_url)
        except ValueError as exc:
            return _toon_result({"error": str(exc)})
        save_healthcheck(result, repo_path)
        return _toon_result(result)


def _register_index_tool(mcp_instance: MCPServer, repo_path: Path, effects: frozenset[str]) -> None:
    @mcp_instance.tool(
        name="aletheore_index",
        # Writes the vector index and sends code chunks to the embedding
        # provider - Aletheore's hosted endpoint if entitled and permitted,
        # else local Ollama, else OpenAI on fallback.
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
        ),
    )
    def aletheore_index() -> str:
        """Build the semantic search index for this repo's evidence, required
        before aletheore_search_codebase or aletheore_answer can be used.
        Embeds via Aletheore's hosted endpoint if this session has permission
        to transmit evidence externally and a token is available, else a
        local Ollama instance, falling back to OpenAI if that's unavailable
        too."""
        from aletheore.search_index import build_index

        evidence = read_evidence(repo_path)
        try:
            count = build_index(repo_path, evidence, allow_hosted=EFFECT_EXTERNAL in effects)
        except Exception as exc:  # noqa: BLE001
            return _toon_result({"error": str(exc)})
        return _toon_result({"indexed_chunks": count})


_NO_INDEX_ERROR = {
    "error": "no semantic index built yet for this repository - call the aletheore_index tool first"
}


def _register_search_codebase_tool(
    mcp_instance: MCPServer, repo_path: Path, effects: frozenset[str]
) -> None:
    @mcp_instance.tool(
        name="aletheore_search_codebase",
        # Writes nothing, but embeds the query text through the configured
        # provider, so it can reach the network.
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
        ),
    )
    def aletheore_search_codebase(query: str, k: int = 10, language: str | None = None) -> str:
        """Hybrid search (meaning + exact identifiers) over the repository's
        indexed code. language: optional filter, e.g. 'python', 'typescript' -
        use it on a polyglot repo when the question is about one stack, since
        an unfiltered search ranks every language's chunks against each other.
        Names must match evidence's own repository.languages values."""
        try:
            return _toon_result(
                search_index(
                    repo_path,
                    query,
                    k=k,
                    language=language,
                    allow_hosted=EFFECT_EXTERNAL in effects,
                )
            )
        except IndexNotFoundError:
            return _toon_result(_NO_INDEX_ERROR)
        except IndexDimensionMismatchError as exc:
            return _toon_result({"error": str(exc)})


def _register_answer_tool(
    mcp_instance: MCPServer, repo_path: Path, answer_adapter: AgentAdapter, effects: frozenset[str]
) -> None:
    @mcp_instance.tool(
        name="aletheore_answer",
        # Sends retrieved code chunks to the configured LLM adapter, and -
        # same as aletheore_search_codebase - embeds the question text
        # through the configured provider, so it can reach the network too.
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=False, openWorldHint=True
        ),
    )
    def aletheore_answer(question: str, k: int = 5) -> str:
        """Answer a natural-language question about this repository from the semantic index."""
        try:
            return _toon_result(
                answer_question(
                    repo_path, question, answer_adapter, k=k, allow_hosted=EFFECT_EXTERNAL in effects
                )
            )
        except IndexNotFoundError:
            return _toon_result(_NO_INDEX_ERROR)
        except IndexDimensionMismatchError as exc:
            return _toon_result({"error": str(exc)})


def _register_managed_audit_tool(mcp_instance: MCPServer, repo_path: Path) -> None:
    @mcp_instance.tool(
        name="aletheore_managed_audit",
        # Uploads this repository's full evidence to the hosted service.
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=False, openWorldHint=True
        ),
    )
    def aletheore_managed_audit(token: str | None = None) -> str:
        """Run a full audit report using Aletheore's managed audit service."""
        # Same resolution the CLI's own `aletheore audit --managed` uses -
        # before this fix, this only checked the raw env var, so a user who
        # ran `aletheore login` (saved to the OS keychain/credentials file,
        # no env var set) got a false "no token available" through MCP even
        # though the CLI itself worked. prompt_fn is a no-op: an MCP tool
        # call is driven by an agent, not a human at a terminal, and must
        # never block waiting for interactive input.
        resolved_token = token or get_api_key(
            "ALETHEORE_API_TOKEN", "aletheore-managed-audit", prompt_fn=lambda _: ""
        )
        if not resolved_token:
            return _toon_result(
                {"error": "no managed-audit token available (run 'aletheore login', set ALETHEORE_API_TOKEN, or pass token)"}
            )
        evidence = read_evidence(repo_path)
        return _toon_result({"report": run_managed_audit_request(evidence, resolved_token)})


"""What each effectful tool needs permission to do.

Read-only tools are absent from this map: reading evidence is the server's
purpose and is never gated.
"""
TOOL_REQUIRED_EFFECTS: dict[str, frozenset[str]] = {
    "aletheore_search_codebase": frozenset({EFFECT_NETWORK}),
    "aletheore_scan": frozenset({EFFECT_WRITE, EFFECT_NETWORK}),
    "aletheore_healthcheck": frozenset({EFFECT_WRITE, EFFECT_NETWORK}),
    "aletheore_index": frozenset({EFFECT_WRITE, EFFECT_NETWORK}),
    "aletheore_answer": frozenset({EFFECT_NETWORK}),
    "aletheore_managed_audit": frozenset({EFFECT_EXTERNAL, EFFECT_NETWORK}),
}


def build_server(
    repo_path: Path,
    answer_adapter: AgentAdapter | None = None,
    allow: frozenset[str] | None = None,
) -> MCPServer:
    """Assemble the MCP server, registering only tools whose effects are permitted.

    Withheld tools are not registered rather than registered-and-refusing.
    A tool absent from the list cannot be invoked at all, which is the actual
    boundary; a registered tool that returns "not permitted" is only a
    convention, and it still spends the agent's context advertising something
    it may not do.
    """
    effects = allowed_effects(os.environ.get(_ALLOW_ENV_VAR)) if allow is None else allow

    mcp_instance = MCPServer("aletheore", instructions=SERVER_INSTRUCTIONS)
    _register_query_wrapper_tools(mcp_instance, repo_path)
    _register_changes_tool(mcp_instance, repo_path)
    _register_neighborhood_tool(mcp_instance, repo_path)
    _register_blast_radius_tool(mcp_instance, repo_path)
    _register_list_tool(mcp_instance, repo_path)
    _register_overview_tool(mcp_instance, repo_path)
    _register_search_tool(mcp_instance, repo_path)
    _register_ast_pattern_tool(mcp_instance, repo_path)
    _register_symbol_source_tool(mcp_instance, repo_path)
    _register_verify_citations_tool(mcp_instance, repo_path)
    _register_code_evidence_tools(mcp_instance, repo_path)

    withheld: list[str] = []

    def permitted(tool_name: str) -> bool:
        if TOOL_REQUIRED_EFFECTS[tool_name] <= effects:
            return True
        withheld.append(tool_name)
        return False

    if permitted("aletheore_scan"):
        _register_scan_tool(mcp_instance, repo_path)
    if permitted("aletheore_healthcheck"):
        _register_healthcheck_tool(mcp_instance, repo_path)
    if permitted("aletheore_index"):
        _register_index_tool(mcp_instance, repo_path, effects)
    if permitted("aletheore_search_codebase"):
        _register_search_codebase_tool(mcp_instance, repo_path, effects)
    if permitted("aletheore_managed_audit"):
        _register_managed_audit_tool(mcp_instance, repo_path)
    if answer_adapter is not None and permitted("aletheore_answer"):
        _register_answer_tool(mcp_instance, repo_path, answer_adapter, effects)

    if withheld:
        # stderr, not stdout: stdout is the MCP transport. The operator is the
        # one who grants consent, so they need to see what was withheld and
        # how to allow it - otherwise a missing tool looks like a bug.
        missing = sorted(set().union(*(TOOL_REQUIRED_EFFECTS[name] for name in withheld)) - effects)
        print(
            f"aletheore: withholding {len(withheld)} tool(s) needing "
            f"{', '.join(missing)}: {', '.join(sorted(withheld))}. "
            f"Set {_ALLOW_ENV_VAR}={','.join(sorted(effects | set(missing)))} to enable.",
            file=sys.stderr,
        )
    return mcp_instance
