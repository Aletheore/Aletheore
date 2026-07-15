import json
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from veridion.mcp_server import build_server


def tool_result_body(result):
    return json.loads(result[0].text)


def make_repo_with_evidence(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    veridion_dir = repo / ".veridion"
    veridion_dir.mkdir(parents=True)
    evidence = {
        "veridion_version": "0.1.0",
        "scanned_at": "2026-07-15T10:00:00+00:00",
        "repo_path": str(repo),
        "repository": {
            "languages": [{"name": "python", "file_count": 2}],
            "modules": [
                {
                    "path": "a.py",
                    "imports": ["b.py"],
                    "imported_by": [],
                    "symbols": {"functions": ["foo"], "classes": []},
                },
                {
                    "path": "b.py",
                    "imports": [],
                    "imported_by": ["a.py"],
                    "symbols": {"functions": [], "classes": []},
                },
            ],
            "dependency_graph": {"nodes": ["a.py", "b.py"], "edges": [["a.py", "b.py"]]},
        },
        "git": {
            "branches": [{"name": "main", "ahead_of_main": 0}],
            "ownership": [{"path": "a.py", "top_author": "alice"}],
            "total_commits": 5,
        },
        "security": {
            "secrets": {
                "findings": [],
                "history_scanned_commits": 0,
                "history_findings": [],
            },
            "dependency_vulnerabilities": {"checked": True, "reason": None, "findings": []},
        },
        "architecture": {
            "clusters": [{"id": 0, "modules": ["a.py", "b.py"]}],
            "cross_cluster_edges": [],
            "layer_violations": {"convention_detected": False, "layers": [], "violations": []},
        },
    }
    (veridion_dir / "evidence.json").write_text(json.dumps(evidence))
    return repo


@pytest.mark.asyncio
async def test_build_server_registers_all_10_wrapper_tools(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    tools = await server.list_tools()
    names = {t.name for t in tools}

    expected = {
        "veridion_imports",
        "veridion_imported_by",
        "veridion_symbols",
        "veridion_branch",
        "veridion_ownership",
        "veridion_secrets",
        "veridion_vulnerabilities",
        "veridion_cluster",
        "veridion_layer_violations",
        "veridion_changes",
    }
    assert expected.issubset(names)


@pytest.mark.asyncio
async def test_veridion_imports_tool_returns_correct_result(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("veridion_imports", {"target": "a.py"})

    assert tool_result_body(result) == {"result": ["b.py"]}


@pytest.mark.asyncio
async def test_veridion_ownership_tool_needs_no_target(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("veridion_ownership", {})

    assert tool_result_body(result) == {"result": [{"path": "a.py", "top_author": "alice"}]}


@pytest.mark.asyncio
async def test_veridion_imports_tool_raises_for_unknown_module(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    with pytest.raises(ToolError):
        await server.call_tool("veridion_imports", {"target": "does/not/exist.py"})


@pytest.mark.asyncio
async def test_veridion_changes_tool_reports_no_prior_snapshot(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("veridion_changes", {})

    assert tool_result_body(result)["result"]["message"].startswith("no prior snapshot")


@pytest.mark.asyncio
async def test_veridion_neighborhood_combines_imports_imported_by_and_cluster(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("veridion_neighborhood", {"target": "a.py"})

    assert tool_result_body(result)["result"] == {
        "target": "a.py",
        "imports": ["b.py"],
        "imported_by": [],
        "cluster": {"id": 0, "modules": ["a.py", "b.py"]},
    }


@pytest.mark.asyncio
async def test_veridion_neighborhood_cluster_is_null_when_unclustered(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    evidence_path = repo / ".veridion" / "evidence.json"
    evidence = json.loads(evidence_path.read_text())
    evidence["architecture"]["clusters"] = []
    evidence_path.write_text(json.dumps(evidence))
    server = build_server(repo)

    result = await server.call_tool("veridion_neighborhood", {"target": "a.py"})

    assert tool_result_body(result)["result"]["cluster"] is None


@pytest.mark.asyncio
async def test_veridion_neighborhood_raises_for_unknown_module(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    with pytest.raises(ToolError):
        await server.call_tool("veridion_neighborhood", {"target": "does/not/exist.py"})
