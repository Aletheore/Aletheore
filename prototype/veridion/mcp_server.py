import json
import re
from pathlib import Path, PurePath

from mcp.server.fastmcp import FastMCP

from veridion.evidence import scan_repository, write_evidence
from veridion.healthcheck import run_healthcheck, save_healthcheck
from veridion.history import compute_diff, list_snapshots, save_snapshot
from veridion.query import (
    ModuleNotFoundInEvidenceError,
    QUERY_FUNCTIONS,
    find_cluster,
    find_imported_by,
    find_imports,
)
from veridion.secrets import iter_all_files


def read_evidence(repo_path: Path) -> dict:
    evidence_path = repo_path / ".veridion" / "evidence.json"
    if not evidence_path.exists():
        raise FileNotFoundError(
            f"no evidence found at {evidence_path} - run 'veridion scan {repo_path}' first "
            "or call the veridion_scan tool"
        )
    return json.loads(evidence_path.read_text())


_TOOL_NAME_TO_QUERY_KIND = {
    "veridion_imports": "imports",
    "veridion_imported_by": "imported-by",
    "veridion_symbols": "symbols",
    "veridion_branch": "branch",
    "veridion_ownership": "ownership",
    "veridion_secrets": "secrets",
    "veridion_vulnerabilities": "vulnerabilities",
    "veridion_licenses": "licenses",
    "veridion_endpoints": "endpoints",
    "veridion_cluster": "cluster",
    "veridion_layer_violations": "layer-violations",
}

_SEARCH_MATCH_CAP = 200


def _register_query_wrapper_tools(mcp_instance: FastMCP, repo_path: Path) -> None:
    for tool_name, kind in _TOOL_NAME_TO_QUERY_KIND.items():
        func, requires_target = QUERY_FUNCTIONS[kind]

        def make_tool(func=func, requires_target=requires_target, kind=kind):
            if requires_target:

                def tool(target: str) -> dict:
                    evidence = read_evidence(repo_path)
                    return {"result": func(evidence, target)}

            else:

                def tool() -> dict:
                    evidence = read_evidence(repo_path)
                    return {"result": func(evidence, None)}

            return tool

        tool_func = make_tool()
        tool_func.__name__ = tool_name
        tool_func.__doc__ = f"Query '{kind}' from the scanned repository's evidence."
        mcp_instance.tool(name=tool_name)(tool_func)


def _register_changes_tool(mcp_instance: FastMCP, repo_path: Path) -> None:
    @mcp_instance.tool(name="veridion_changes")
    def veridion_changes(full: bool = False) -> dict:
        """What changed between the two most recent scans of this repo."""
        snapshots = list_snapshots(repo_path)
        if len(snapshots) < 2:
            return {"result": {"message": "no prior snapshot to compare against"}}
        try:
            old = json.loads(snapshots[-2].read_text())
        except json.JSONDecodeError:
            return {"result": {"message": f"most recent snapshot is unreadable ({snapshots[-2]})"}}
        new = json.loads(snapshots[-1].read_text())
        return {"result": compute_diff(old, new, full=full)}


def _register_neighborhood_tool(mcp_instance: FastMCP, repo_path: Path) -> None:
    @mcp_instance.tool(name="veridion_neighborhood")
    def veridion_neighborhood(target: str) -> dict:
        """A module's imports, dependents, and cluster in one call."""
        evidence = read_evidence(repo_path)
        imports = find_imports(evidence, target)
        imported_by = find_imported_by(evidence, target)
        try:
            cluster = find_cluster(evidence, target)
        except ModuleNotFoundInEvidenceError:
            cluster = None
        return {
            "result": {
                "target": target,
                "imports": imports,
                "imported_by": imported_by,
                "cluster": cluster,
            }
        }


def _register_search_tool(mcp_instance: FastMCP, repo_path: Path) -> None:
    @mcp_instance.tool(name="veridion_search")
    def veridion_search(pattern: str, regex: bool = False, path_glob: str | None = None) -> dict:
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

        return {"result": {"matches": matches, "truncated": truncated}}


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


def _register_scan_tool(mcp_instance: FastMCP, repo_path: Path) -> None:
    @mcp_instance.tool(name="veridion_scan")
    def veridion_scan(
        check_vulnerabilities: bool = True,
        scan_git_history: bool = True,
        check_licenses: bool = True,
        map_endpoints: bool = True,
    ) -> dict:
        """Run the deterministic Veridion scanner and save evidence for this repository."""
        evidence = scan_repository(
            repo_path,
            check_vulnerabilities=check_vulnerabilities,
            scan_git_history=scan_git_history,
            check_licenses=check_licenses,
            map_endpoints=map_endpoints,
        )
        write_evidence(evidence, repo_path)
        save_snapshot(evidence, repo_path)
        return {"result": _scan_summary(evidence)}


def _register_healthcheck_tool(mcp_instance: FastMCP, repo_path: Path) -> None:
    @mcp_instance.tool(name="veridion_healthcheck")
    def veridion_healthcheck(base_url: str) -> dict:
        """GET-only live health check of mapped API endpoints against a running instance."""
        evidence = read_evidence(repo_path)
        endpoints = evidence["repository"].get("api_endpoints", {}).get("endpoints", [])
        result = run_healthcheck(endpoints, base_url)
        save_healthcheck(result, repo_path)
        return {"result": result}


def build_server(repo_path: Path) -> FastMCP:
    mcp_instance = FastMCP("veridion")
    _register_query_wrapper_tools(mcp_instance, repo_path)
    _register_changes_tool(mcp_instance, repo_path)
    _register_neighborhood_tool(mcp_instance, repo_path)
    _register_search_tool(mcp_instance, repo_path)
    _register_scan_tool(mcp_instance, repo_path)
    _register_healthcheck_tool(mcp_instance, repo_path)
    return mcp_instance
