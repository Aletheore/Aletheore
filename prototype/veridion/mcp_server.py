import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from veridion.history import compute_diff, list_snapshots
from veridion.query import QUERY_FUNCTIONS


def _read_evidence(repo_path: Path) -> dict:
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
    "veridion_cluster": "cluster",
    "veridion_layer_violations": "layer-violations",
}


def _register_query_wrapper_tools(mcp_instance: FastMCP, repo_path: Path) -> None:
    for tool_name, kind in _TOOL_NAME_TO_QUERY_KIND.items():
        func, requires_target = QUERY_FUNCTIONS[kind]

        def make_tool(func=func, requires_target=requires_target, kind=kind):
            if requires_target:

                def tool(target: str) -> dict:
                    evidence = _read_evidence(repo_path)
                    return {"result": func(evidence, target)}

            else:

                def tool() -> dict:
                    evidence = _read_evidence(repo_path)
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


def build_server(repo_path: Path) -> FastMCP:
    mcp_instance = FastMCP("veridion")
    _register_query_wrapper_tools(mcp_instance, repo_path)
    _register_changes_tool(mcp_instance, repo_path)
    return mcp_instance
