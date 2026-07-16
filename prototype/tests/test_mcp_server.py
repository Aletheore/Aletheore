import json
import subprocess
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
async def test_build_server_registers_all_11_wrapper_tools(tmp_path):
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
        "veridion_licenses",
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


def make_repo_with_files(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "search_repo"
    repo.mkdir()
    (repo / ".veridion").mkdir()
    (repo / ".veridion" / "evidence.json").write_text(json.dumps({"repository": {"modules": []}}))
    for rel_path, content in files.items():
        full_path = repo / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)
    return repo


@pytest.mark.asyncio
async def test_veridion_search_finds_a_literal_match(tmp_path):
    repo = make_repo_with_files(tmp_path, {"app/main.py": "def hello():\n    return 'world'\n"})
    server = build_server(repo)

    result = await server.call_tool("veridion_search", {"pattern": "def hello"})

    matches = tool_result_body(result)["result"]["matches"]
    assert len(matches) == 1
    assert matches[0] == {"path": "app/main.py", "line": 1, "text": "def hello():"}


@pytest.mark.asyncio
async def test_veridion_search_regex_mode(tmp_path):
    repo = make_repo_with_files(tmp_path, {"a.py": "x = 1\ny = 2\nz = 3\n"})
    server = build_server(repo)

    result = await server.call_tool("veridion_search", {"pattern": r"^[xy] = \d", "regex": True})

    matches = tool_result_body(result)["result"]["matches"]
    assert len(matches) == 2


@pytest.mark.asyncio
async def test_veridion_search_respects_path_glob(tmp_path):
    repo = make_repo_with_files(
        tmp_path,
        {"src/a.py": "TARGET\n", "tests/b.py": "TARGET\n"},
    )
    server = build_server(repo)

    result = await server.call_tool(
        "veridion_search", {"pattern": "TARGET", "path_glob": "src/*"}
    )

    matches = tool_result_body(result)["result"]["matches"]
    assert len(matches) == 1
    assert matches[0]["path"] == "src/a.py"


@pytest.mark.asyncio
async def test_veridion_search_ignores_ignored_dirs(tmp_path):
    repo = make_repo_with_files(
        tmp_path,
        {"node_modules/lib.js": "TARGET\n", "app.js": "TARGET\n"},
    )
    server = build_server(repo)

    result = await server.call_tool("veridion_search", {"pattern": "TARGET"})

    matches = tool_result_body(result)["result"]["matches"]
    assert len(matches) == 1
    assert matches[0]["path"] == "app.js"


@pytest.mark.asyncio
async def test_veridion_search_caps_at_200_and_flags_truncated(tmp_path):
    content = "\n".join(f"MATCH_ME line {i}" for i in range(250))
    repo = make_repo_with_files(tmp_path, {"big.py": content})
    server = build_server(repo)

    result = await server.call_tool("veridion_search", {"pattern": "MATCH_ME"})

    result_body = tool_result_body(result)["result"]
    assert len(result_body["matches"]) == 200
    assert result_body["truncated"] is True


def make_git_repo_with_source(tmp_path: Path) -> Path:
    repo = tmp_path / "git_repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "a@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Alice"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


@pytest.mark.asyncio
async def test_veridion_scan_returns_compact_summary(tmp_path):
    repo = make_git_repo_with_source(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("veridion_scan", {})

    summary = tool_result_body(result)["result"]
    assert summary["module_count"] == 1
    assert "scanned_at" in summary
    assert summary["secrets"] == {
        "total_findings": 0,
        "real_findings": 0,
        "history_findings": 0,
    }
    assert summary["vulnerabilities"]["checked"] is True
    assert summary["layer_violations"]["convention_detected"] is False
    assert "cluster_count" in summary


@pytest.mark.asyncio
async def test_veridion_scan_writes_a_history_snapshot(tmp_path):
    repo = make_git_repo_with_source(tmp_path)
    server = build_server(repo)

    await server.call_tool("veridion_scan", {})

    history_files = list((repo / ".veridion" / "history").glob("*.json"))
    assert len(history_files) == 1


@pytest.mark.asyncio
async def test_veridion_scan_real_findings_excludes_placeholders(tmp_path):
    repo = make_git_repo_with_source(tmp_path)
    (repo / "tests").mkdir()
    (repo / "tests" / "fixture.py").write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')
    server = build_server(repo)

    result = await server.call_tool("veridion_scan", {})

    summary = tool_result_body(result)["result"]
    assert summary["secrets"]["total_findings"] == 1
    assert summary["secrets"]["real_findings"] == 0
