import json
import os
import re
import sys
from pathlib import Path, PurePath

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from aletheore.adapters.base import AgentAdapter
from aletheore.answer import answer_question
from aletheore.credentials import get_api_key
from aletheore.evidence import EVIDENCE_VERSION, is_evidence_version_compatible, scan_repository, write_evidence
from aletheore.healthcheck import run_healthcheck, save_healthcheck
from aletheore.history import compute_diff, list_snapshots, save_snapshot
from aletheore.managed_audit_client import run_managed_audit_request
from aletheore.query import (
    ModuleNotFoundInEvidenceError,
    QUERY_FUNCTIONS,
    find_code_evidence_for_dependency,
    find_code_evidence_for_endpoint,
    find_code_evidence_for_symbol,
    find_cluster,
    find_imported_by,
    find_imports,
    find_symbol_source,
)
from aletheore.secrets import iter_all_files
from aletheore.search_index import IndexNotFoundError, search_index
from aletheore.toon_encoding import to_toon


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


class IncompatibleEvidenceVersionError(Exception):
    pass


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
    evidence = json.loads(evidence_path.read_text())
    written_version = evidence.get("aletheore_version")
    if not is_evidence_version_compatible(written_version):
        raise IncompatibleEvidenceVersionError(
            f"{evidence_path} was written by aletheore_version={written_version!r}, which "
            f"isn't compatible with this build's evidence schema ({EVIDENCE_VERSION}) - "
            f"re-run 'aletheore scan {repo_path}' to refresh it"
        )
    _evidence_cache[repo_path] = (cache_key, evidence)
    return evidence


def _toon_result(data: object) -> str:
    # Every tool result is TOON-encoded rather than returned as a plain dict
    # (which MCPServer would otherwise auto-serialize to JSON) - this is the
    # actual token-cost surface for whatever agent is calling these tools,
    # and evidence's own shape (uniform arrays of same-shaped objects almost
    # everywhere) is exactly TOON's best case.
    return to_toon({"result": data})


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
    "ownership": "Per-file code ownership derived from git blame history. Takes no target.",
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
    for tool_name, kind in _TOOL_NAME_TO_QUERY_KIND.items():
        func, requires_target = QUERY_FUNCTIONS[kind]

        def make_tool(func=func, requires_target=requires_target, kind=kind):
            if requires_target:

                def tool(target: str) -> str:
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
            old = json.loads(snapshots[-2].read_text())
        except json.JSONDecodeError:
            return _toon_result({"message": f"most recent snapshot is unreadable ({snapshots[-2]})"})
        new = json.loads(snapshots[-1].read_text())
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


def _register_search_tool(mcp_instance: MCPServer, repo_path: Path) -> None:
    @mcp_instance.tool(name="aletheore_search", annotations=READ_ONLY_ANNOTATIONS)
    def aletheore_search(pattern: str, regex: bool = False, path_glob: str | None = None) -> str:
        """Deterministic literal or regex search over the repository's source files."""
        compiled = re.compile(pattern) if regex else None
        matches: list[dict] = []
        truncated = False

        for path in iter_all_files(repo_path):
            rel_path = path.relative_to(repo_path).as_posix()
            if path_glob is not None and not PurePath(rel_path).match(path_glob):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            for line_no, line in enumerate(text.splitlines(), start=1):
                found = compiled.search(line) if compiled else pattern in line
                if found:
                    if len(matches) >= _SEARCH_MATCH_CAP:
                        truncated = True
                        break
                    matches.append({"path": rel_path, "line": line_no, "text": line})
            if truncated:
                break

        return _toon_result({"matches": matches, "truncated": truncated})


def _register_symbol_source_tool(mcp_instance: MCPServer, repo_path: Path) -> None:
    @mcp_instance.tool(name="aletheore_symbol_source", annotations=READ_ONLY_ANNOTATIONS)
    def aletheore_symbol_source(module: str, symbol: str) -> str:
        """Exact source text for one named function/class, with resolved line bounds."""
        evidence = read_evidence(repo_path)
        return _toon_result(find_symbol_source(evidence, repo_path, module, symbol))


def _register_code_evidence_tools(mcp_instance: MCPServer, repo_path: Path) -> None:
    @mcp_instance.tool(name="aletheore_find_evidence_for_endpoint", annotations=READ_ONLY_ANNOTATIONS)
    def aletheore_find_evidence_for_endpoint(method: str, path: str) -> str:
        """Resolve an API endpoint to source evidence: file, line, symbol, owner, commit, dependency, and risk."""
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


def _register_index_tool(mcp_instance: MCPServer, repo_path: Path) -> None:
    @mcp_instance.tool(
        name="aletheore_index",
        # Writes the vector index and sends code chunks to the embedding
        # provider - local Ollama when it is up, OpenAI on fallback.
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
        ),
    )
    def aletheore_index() -> str:
        """Build the semantic search index this repo's evidence, required
        before aletheore_search_codebase or aletheore_answer can be used.
        Embeds via a local Ollama instance, falling back to OpenAI if
        unavailable."""
        from aletheore.search_index import build_index

        evidence = read_evidence(repo_path)
        try:
            count = build_index(repo_path, evidence)
        except Exception as exc:  # noqa: BLE001
            return _toon_result({"error": str(exc)})
        return _toon_result({"indexed_chunks": count})


_NO_INDEX_ERROR = {
    "error": "no semantic index built yet for this repository - call the aletheore_index tool first"
}


def _register_search_codebase_tool(mcp_instance: MCPServer, repo_path: Path) -> None:
    @mcp_instance.tool(
        name="aletheore_search_codebase",
        # Writes nothing, but embeds the query text through the configured
        # provider, so it can reach the network.
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
        ),
    )
    def aletheore_search_codebase(query: str, k: int = 10) -> str:
        """Semantic search over the repository's indexed code."""
        try:
            return _toon_result(search_index(repo_path, query, k=k))
        except IndexNotFoundError:
            return _toon_result(_NO_INDEX_ERROR)


def _register_answer_tool(
    mcp_instance: MCPServer, repo_path: Path, answer_adapter: AgentAdapter
) -> None:
    @mcp_instance.tool(
        name="aletheore_answer",
        # Sends retrieved code chunks to the configured LLM adapter.
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=False, openWorldHint=True
        ),
    )
    def aletheore_answer(question: str, k: int = 5) -> str:
        """Answer a natural-language question about this repository from the semantic index."""
        try:
            return _toon_result(answer_question(repo_path, question, answer_adapter, k=k))
        except IndexNotFoundError:
            return _toon_result(_NO_INDEX_ERROR)


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

    mcp_instance = MCPServer("aletheore")
    _register_query_wrapper_tools(mcp_instance, repo_path)
    _register_changes_tool(mcp_instance, repo_path)
    _register_neighborhood_tool(mcp_instance, repo_path)
    _register_search_tool(mcp_instance, repo_path)
    _register_symbol_source_tool(mcp_instance, repo_path)
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
        _register_index_tool(mcp_instance, repo_path)
    if permitted("aletheore_search_codebase"):
        _register_search_codebase_tool(mcp_instance, repo_path)
    if permitted("aletheore_managed_audit"):
        _register_managed_audit_tool(mcp_instance, repo_path)
    if answer_adapter is not None and permitted("aletheore_answer"):
        _register_answer_tool(mcp_instance, repo_path, answer_adapter)

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
