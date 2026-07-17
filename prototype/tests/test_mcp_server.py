import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import toon
from mcp.server.fastmcp.exceptions import ToolError

from aletheore.mcp_server import build_server


def tool_result_body(result):
    return toon.decode(result[0][0].text)


def make_repo_with_evidence(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    aletheore_dir = repo / ".aletheore"
    aletheore_dir.mkdir(parents=True)
    evidence = {
        "aletheore_version": "0.1.0",
        "scanned_at": "2026-07-15T10:00:00+00:00",
        "repo_path": str(repo),
        "repository": {
            "languages": [{"name": "python", "file_count": 2}],
            "modules": [
                {
                    "path": "a.py",
                    "imports": ["b.py"],
                    "imported_by": [],
                    "symbols": {
                        "functions": [{"name": "foo", "start_line": 1, "end_line": 1}],
                        "classes": [],
                    },
                },
                {
                    "path": "b.py",
                    "imports": [],
                    "imported_by": ["a.py"],
                    "symbols": {"functions": [], "classes": []},
                },
            ],
            "dependency_graph": {"nodes": ["a.py", "b.py"], "edges": [["a.py", "b.py"]]},
            "api_endpoints": {"checked": True, "endpoints": []},
            "dead_code": {
                "unreachable_modules": [{"path": "unused.py", "reason": "no imports"}],
                "unused_dependencies": [],
                "entry_points_detected": ["a.py"],
            },
        },
        "git": {
            "branches": [{"name": "main", "ahead_of_main": 0}],
            "ownership": [{"path": "a.py", "top_author": "alice"}],
            "total_commits": 5,
            "hotspots": [
                {
                    "path": "a.py",
                    "churn_count": 3,
                    "co_change_partners": [{"path": "b.py", "co_occurrences": 2}],
                    "dependents_count": 0,
                }
            ],
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
    (aletheore_dir / "evidence.json").write_text(json.dumps(evidence))
    (repo / "a.py").write_text("def foo():\n    return 1\n")
    return repo


@pytest.mark.asyncio
async def test_build_server_registers_expected_tools(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    tools = await server.list_tools()
    names = {t.name for t in tools}

    expected = {
        "aletheore_imports",
        "aletheore_imported_by",
        "aletheore_symbols",
        "aletheore_branch",
        "aletheore_ownership",
        "aletheore_secrets",
        "aletheore_vulnerabilities",
        "aletheore_licenses",
        "aletheore_endpoints",
        "aletheore_cluster",
        "aletheore_layer_violations",
        "aletheore_dead_code",
        "aletheore_hotspots",
        "aletheore_changes",
        "aletheore_neighborhood",
        "aletheore_search",
        "aletheore_symbol_source",
        "aletheore_scan",
        "aletheore_healthcheck",
        "aletheore_search_codebase",
        "aletheore_managed_audit",
    }
    assert expected.issubset(names)
    assert len(names) == 21
    assert "aletheore_answer" not in names


@pytest.mark.asyncio
async def test_answer_tool_present_with_adapter(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo, answer_adapter=MagicMock())

    tools = await server.list_tools()
    names = {t.name for t in tools}

    assert "aletheore_answer" in names


@pytest.mark.asyncio
async def test_aletheore_search_codebase_returns_toon_results(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    with patch(
        "aletheore.mcp_server.search_index",
        return_value=[{"module_path": "a.py", "symbol_name": "foo"}],
    ):
        result = await server.call_tool(
            "aletheore_search_codebase", {"query": "where is foo", "k": 1}
        )

    assert tool_result_body(result)["result"] == [{"module_path": "a.py", "symbol_name": "foo"}]


@pytest.mark.asyncio
async def test_aletheore_imports_tool_returns_correct_result(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("aletheore_imports", {"target": "a.py"})

    assert tool_result_body(result) == {"result": ["b.py"]}


@pytest.mark.asyncio
async def test_aletheore_symbol_source_returns_exact_source(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("aletheore_symbol_source", {"module": "a.py", "symbol": "foo"})

    assert tool_result_body(result)["result"]["source"] == "def foo():"


@pytest.mark.asyncio
async def test_aletheore_dead_code_tool_returns_toon_results(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("aletheore_dead_code", {})

    assert tool_result_body(result)["result"]["unreachable_modules"][0]["path"] == "unused.py"


@pytest.mark.asyncio
async def test_aletheore_hotspots_tool_returns_toon_results(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("aletheore_hotspots", {})

    assert tool_result_body(result)["result"][0]["path"] == "a.py"


@pytest.mark.asyncio
async def test_aletheore_managed_audit_tool_calls_client(tmp_path, monkeypatch):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)
    monkeypatch.setattr(
        "aletheore.mcp_server.run_managed_audit_request",
        lambda evidence, token: "# Report\n\nmanaged audit text",
    )

    result = await server.call_tool("aletheore_managed_audit", {"token": "real-token"})

    assert tool_result_body(result)["result"]["report"].startswith("# Report")


@pytest.mark.asyncio
async def test_aletheore_ownership_tool_needs_no_target(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("aletheore_ownership", {})

    assert tool_result_body(result) == {"result": [{"path": "a.py", "top_author": "alice"}]}


@pytest.mark.asyncio
async def test_aletheore_imports_tool_raises_for_unknown_module(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    with pytest.raises(ToolError):
        await server.call_tool("aletheore_imports", {"target": "does/not/exist.py"})


@pytest.mark.asyncio
async def test_aletheore_changes_tool_reports_no_prior_snapshot(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("aletheore_changes", {})

    assert tool_result_body(result)["result"]["message"].startswith("no prior snapshot")


@pytest.mark.asyncio
async def test_aletheore_neighborhood_combines_imports_imported_by_and_cluster(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("aletheore_neighborhood", {"target": "a.py"})

    assert tool_result_body(result)["result"] == {
        "target": "a.py",
        "imports": ["b.py"],
        "imported_by": [],
        "cluster": {"id": 0, "modules": ["a.py", "b.py"]},
    }


@pytest.mark.asyncio
async def test_aletheore_neighborhood_cluster_is_null_when_unclustered(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    evidence_path = repo / ".aletheore" / "evidence.json"
    evidence = json.loads(evidence_path.read_text())
    evidence["architecture"]["clusters"] = []
    evidence_path.write_text(json.dumps(evidence))
    server = build_server(repo)

    result = await server.call_tool("aletheore_neighborhood", {"target": "a.py"})

    assert tool_result_body(result)["result"]["cluster"] is None


@pytest.mark.asyncio
async def test_aletheore_neighborhood_raises_for_unknown_module(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    with pytest.raises(ToolError):
        await server.call_tool("aletheore_neighborhood", {"target": "does/not/exist.py"})


def make_repo_with_files(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "search_repo"
    repo.mkdir()
    (repo / ".aletheore").mkdir()
    (repo / ".aletheore" / "evidence.json").write_text(json.dumps({"repository": {"modules": []}}))
    for rel_path, content in files.items():
        full_path = repo / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)
    return repo


@pytest.mark.asyncio
async def test_aletheore_search_finds_a_literal_match(tmp_path):
    repo = make_repo_with_files(tmp_path, {"app/main.py": "def hello():\n    return 'world'\n"})
    server = build_server(repo)

    result = await server.call_tool("aletheore_search", {"pattern": "def hello"})

    matches = tool_result_body(result)["result"]["matches"]
    assert len(matches) == 1
    assert matches[0] == {"path": "app/main.py", "line": 1, "text": "def hello():"}


@pytest.mark.asyncio
async def test_aletheore_search_regex_mode(tmp_path):
    repo = make_repo_with_files(tmp_path, {"a.py": "x = 1\ny = 2\nz = 3\n"})
    server = build_server(repo)

    result = await server.call_tool("aletheore_search", {"pattern": r"^[xy] = \d", "regex": True})

    matches = tool_result_body(result)["result"]["matches"]
    assert len(matches) == 2


@pytest.mark.asyncio
async def test_aletheore_search_respects_path_glob(tmp_path):
    repo = make_repo_with_files(
        tmp_path,
        {"src/a.py": "TARGET\n", "tests/b.py": "TARGET\n"},
    )
    server = build_server(repo)

    result = await server.call_tool(
        "aletheore_search", {"pattern": "TARGET", "path_glob": "src/*"}
    )

    matches = tool_result_body(result)["result"]["matches"]
    assert len(matches) == 1
    assert matches[0]["path"] == "src/a.py"


@pytest.mark.asyncio
async def test_aletheore_search_ignores_ignored_dirs(tmp_path):
    repo = make_repo_with_files(
        tmp_path,
        {"node_modules/lib.js": "TARGET\n", "app.js": "TARGET\n"},
    )
    server = build_server(repo)

    result = await server.call_tool("aletheore_search", {"pattern": "TARGET"})

    matches = tool_result_body(result)["result"]["matches"]
    assert len(matches) == 1
    assert matches[0]["path"] == "app.js"


@pytest.mark.asyncio
async def test_aletheore_search_caps_at_200_and_flags_truncated(tmp_path):
    content = "\n".join(f"MATCH_ME line {i}" for i in range(250))
    repo = make_repo_with_files(tmp_path, {"big.py": content})
    server = build_server(repo)

    result = await server.call_tool("aletheore_search", {"pattern": "MATCH_ME"})

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
async def test_aletheore_scan_returns_compact_summary(tmp_path):
    repo = make_git_repo_with_source(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("aletheore_scan", {})

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
async def test_aletheore_scan_writes_a_history_snapshot(tmp_path):
    repo = make_git_repo_with_source(tmp_path)
    server = build_server(repo)

    await server.call_tool("aletheore_scan", {})

    history_files = list((repo / ".aletheore" / "history").glob("*.json"))
    assert len(history_files) == 1


@pytest.mark.asyncio
async def test_aletheore_scan_real_findings_excludes_placeholders(tmp_path):
    repo = make_git_repo_with_source(tmp_path)
    (repo / "tests").mkdir()
    (repo / "tests" / "fixture.py").write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')
    server = build_server(repo)

    result = await server.call_tool("aletheore_scan", {})

    summary = tool_result_body(result)["result"]
    assert summary["secrets"]["total_findings"] == 1
    assert summary["secrets"]["real_findings"] == 0


@pytest.mark.asyncio
async def test_aletheore_healthcheck_tool_returns_results(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    evidence_path = repo / ".aletheore" / "evidence.json"
    evidence = json.loads(evidence_path.read_text())
    evidence["repository"]["api_endpoints"] = {
        "checked": True,
        "endpoints": [
            {
                "method": "GET",
                "path": "/health",
                "framework": "flask",
                "file": "app.py",
                "line": 1,
                "handler": "health",
                "unresolved": False,
            }
        ],
    }
    evidence_path.write_text(json.dumps(evidence))
    server = build_server(repo)

    response = MagicMock()
    response.status = 200
    response.__enter__.return_value = response
    response.__exit__.return_value = False

    with patch("aletheore.healthcheck.urllib.request.urlopen", return_value=response):
        result = await server.call_tool(
            "aletheore_healthcheck", {"base_url": "http://localhost:5000"}
        )

    body = tool_result_body(result)["result"]
    assert body["results"][0]["status_code"] == 200
