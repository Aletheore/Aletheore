import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import toon
from mcp.server.mcpserver.exceptions import ToolError

from aletheore.mcp_server import build_server
from aletheore.search_index import IndexNotFoundError
from aletheore.schema_map import skipped_schema
from tests.air_fixtures import minimal_air_evidence


def tool_result_body(result):
    # mcp 2.x's call_tool() returns a CallToolResult object (with a .content
    # list of content blocks) instead of the raw (content_list, ...) tuple
    # FastMCP 1.x returned - this helper is the one place that shape leaks
    # into these tests, so it's the only place that needs to know about it.
    return toon.decode(result.content[0].text)


def make_repo_with_evidence(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    aletheore_dir = repo / ".aletheore"
    aletheore_dir.mkdir(parents=True)
    # Starts from minimal_air_evidence() (schema-valid, every collection
    # empty) rather than a hand-rolled dict - read_evidence now routes
    # through load_evidence_file, which validates full AIR schema shape,
    # not just the version stamp. A hand-rolled dict here previously got
    # away with omitting several required repository.* keys (ai_usage,
    # build_tools, frameworks, monorepo, policy_docs) because nothing ever
    # checked for them.
    evidence = minimal_air_evidence()
    evidence["scanned_at"] = "2026-07-15T10:00:00+00:00"
    evidence["repo_path"] = str(repo)
    evidence["repository"].update({
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
            "api_endpoints": {
                "checked": True,
                "endpoints": [
                    {
                        "method": "GET",
                        "path": "/health",
                        "framework": "fastapi",
                        "file": "a.py",
                        "line": 1,
                        "handler": "foo",
                        "unresolved": False,
                    }
                ],
            },
            "dead_code": {
                "unreachable_modules": [{"path": "unused.py", "reason": "no imports"}],
                "unused_dependencies": [],
                "entry_points_detected": ["a.py"],
            },
            "database": {
                "orm_frameworks": [],
                "migration_directories": [{"path": "migrations", "file_count": 4}],
                "schema_files": [],
                # Unchecked rather than omitted: this override replaces the
                # whole `database` object, so it has to restate every key
                # minimal_air_evidence() would have supplied - and the gated
                # shape is what an unentitled scan actually writes.
                "schema": skipped_schema("requires a paid plan"),
            },
            "infrastructure": {
                "docker_compose_services": [{"file": "docker-compose.yml", "services": ["web"]}],
                "kubernetes_manifests": [],
                "terraform_files": [],
                "helm_charts": [],
            },
            "environment_variables": {
                "declared": [{"name": "FOO", "source": ".env.example"}],
            },
    })
    evidence["git"].update({
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
    })
    evidence["security"]["secrets"].update({
        "findings": [],
        "history_scanned_commits": 0,
        "history_findings": [],
    })
    evidence["security"]["dependency_vulnerabilities"] = {
        "checked": True,
        "reason": None,
        "findings": [],
    }
    evidence["architecture"].update({
        "clusters": [{"id": 0, "modules": ["a.py", "b.py"]}],
        "cross_cluster_edges": [],
        "layer_violations": {"convention_detected": False, "layers": [], "violations": []},
    })
    (aletheore_dir / "air.json").write_text(json.dumps(evidence))
    (repo / "a.py").write_text("def foo():\n    return 1\n")
    return repo


def test_read_evidence_caches_parsed_result_until_the_file_changes(tmp_path, monkeypatch):
    # Before this fix, every single MCP tool call re-read and re-parsed the
    # whole evidence file from disk, with no caching across calls in the
    # same long-lived MCP server process - real, entirely avoidable latency
    # on a large repo's multi-hundred-MB evidence file.
    import time

    from aletheore.mcp_server import read_evidence

    monkeypatch.setattr("aletheore.mcp_server._evidence_cache", {})
    repo = make_repo_with_evidence(tmp_path)
    evidence_path = repo / ".aletheore" / "air.json"

    read_count = {"n": 0}
    real_read_text = Path.read_text

    def counting_read_text(self, *args, **kwargs):
        if self == evidence_path:
            read_count["n"] += 1
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)

    first = read_evidence(repo)
    second = read_evidence(repo)

    assert read_count["n"] == 1
    assert first is second

    time.sleep(0.01)
    updated = json.loads(real_read_text(evidence_path))
    updated["scanned_at"] = "2026-07-16T00:00:00+00:00 - a longer value to change file size too"
    evidence_path.write_text(json.dumps(updated))

    third = read_evidence(repo)

    assert read_count["n"] == 2
    assert third["scanned_at"].startswith("2026-07-16")


def test_read_evidence_rejects_a_malformed_but_version_compatible_file(tmp_path, monkeypatch):
    # read_evidence used to only check the version stamp, via its own bare
    # json.loads - a truncated or hand-edited air.json with a *compatible*
    # version passed straight through and only failed later as a raw
    # KeyError deep inside whichever tool first touched the missing/wrong
    # field, instead of one clear, actionable error naming what's wrong.
    from aletheore.evidence import MalformedEvidenceError
    from aletheore.mcp_server import read_evidence

    monkeypatch.setattr("aletheore.mcp_server._evidence_cache", {})
    repo = make_repo_with_evidence(tmp_path)
    evidence_path = repo / ".aletheore" / "air.json"
    evidence = json.loads(evidence_path.read_text())
    del evidence["repository"]
    evidence_path.write_text(json.dumps(evidence))

    with pytest.raises(MalformedEvidenceError):
        read_evidence(repo)


def test_read_evidence_rejects_an_incompatible_schema_version(tmp_path, monkeypatch):
    from aletheore.evidence import IncompatibleEvidenceVersionError
    from aletheore.mcp_server import read_evidence

    monkeypatch.setattr("aletheore.mcp_server._evidence_cache", {})
    repo = make_repo_with_evidence(tmp_path)
    evidence_path = repo / ".aletheore" / "air.json"
    evidence = json.loads(evidence_path.read_text())
    evidence["aletheore_version"] = "999.999.999"
    evidence_path.write_text(json.dumps(evidence))

    with pytest.raises(IncompatibleEvidenceVersionError):
        read_evidence(repo)


def test_read_evidence_rejects_evidence_with_no_version_field(tmp_path, monkeypatch):
    from aletheore.evidence import IncompatibleEvidenceVersionError
    from aletheore.mcp_server import read_evidence

    monkeypatch.setattr("aletheore.mcp_server._evidence_cache", {})
    repo = make_repo_with_evidence(tmp_path)
    evidence_path = repo / ".aletheore" / "air.json"
    evidence = json.loads(evidence_path.read_text())
    del evidence["aletheore_version"]
    evidence_path.write_text(json.dumps(evidence))

    with pytest.raises(IncompatibleEvidenceVersionError):
        read_evidence(repo)


@pytest.mark.asyncio
async def test_build_server_registers_expected_tools(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    # Every effect permitted, so this stays an inventory of the full tool
    # surface. What the *default* posture registers is covered in
    # tests/test_mcp_consent.py.
    server = build_server(repo, allow=frozenset({"write", "network", "external"}))

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
        "aletheore_database",
        "aletheore_infrastructure",
        "aletheore_environment_variables",
        "aletheore_changes",
        "aletheore_neighborhood",
        "aletheore_search",
        "aletheore_symbol_source",
        "aletheore_scan",
        "aletheore_healthcheck",
        "aletheore_index",
        "aletheore_search_codebase",
        "aletheore_managed_audit",
        "aletheore_find_evidence_for_endpoint",
        "aletheore_find_evidence_for_symbol",
        "aletheore_find_evidence_for_dependency",
    }
    assert expected.issubset(names)
    assert len(names) == 28
    assert "aletheore_answer" not in names


@pytest.mark.asyncio
async def test_dynamic_query_tools_have_distinct_non_generic_descriptions(tmp_path):
    # Before this fix, every one of these 16 tools shared the same templated
    # description ("Query 'X' from the scanned repository's evidence.") with
    # no indication of what `target` means for that specific tool - an LLM
    # caller had no way to tell "target is a file path" from "target is a
    # branch name" from "this tool takes no target at all".
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    tools = await server.list_tools()
    by_name = {t.name: t for t in tools}

    dynamic_tool_names = [
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
        "aletheore_database",
        "aletheore_infrastructure",
        "aletheore_environment_variables",
    ]
    descriptions = [by_name[name].description for name in dynamic_tool_names]

    assert len(set(descriptions)) == len(dynamic_tool_names)
    assert not any(d.startswith("Query '") for d in descriptions)
    assert "file path" in by_name["aletheore_imports"].description
    assert "branch name" in by_name["aletheore_branch"].description
    assert "Takes no target" in by_name["aletheore_vulnerabilities"].description


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
async def test_aletheore_search_codebase_returns_friendly_error_when_index_not_built(tmp_path):
    # Before this fix, this raised IndexNotFoundError straight through MCPServer's
    # own exception wrapping, which reused the CLI's own message ("run
    # 'aletheore index <path>' first") - correct advice for a human at a
    # terminal, useless for an agent that only has MCP tools and can't run
    # shell commands. It should be told to call aletheore_index instead.
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    with patch(
        "aletheore.mcp_server.search_index",
        side_effect=IndexNotFoundError("no index found"),
    ):
        result = await server.call_tool("aletheore_search_codebase", {"query": "where is foo"})

    assert tool_result_body(result)["result"] == {
        "error": "no semantic index built yet for this repository - call the aletheore_index tool first"
    }


@pytest.mark.asyncio
async def test_aletheore_search_codebase_returns_friendly_error_on_dimension_mismatch(tmp_path):
    from aletheore.mcp_server import IndexDimensionMismatchError

    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    with patch(
        "aletheore.mcp_server.search_index",
        side_effect=IndexDimensionMismatchError("the index at ... holds 1536-dimension vectors ..."),
    ):
        result = await server.call_tool("aletheore_search_codebase", {"query": "where is foo"})

    assert "1536-dimension" in tool_result_body(result)["result"]["error"]


@pytest.mark.asyncio
async def test_aletheore_answer_returns_friendly_error_when_index_not_built(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo, answer_adapter=MagicMock())

    with patch(
        "aletheore.mcp_server.answer_question",
        side_effect=IndexNotFoundError("no index found"),
    ):
        result = await server.call_tool("aletheore_answer", {"question": "what does foo do"})

    assert tool_result_body(result)["result"] == {
        "error": "no semantic index built yet for this repository - call the aletheore_index tool first"
    }


@pytest.mark.asyncio
async def test_aletheore_index_tool_builds_the_search_index(tmp_path):
    # Before this fix, aletheore_search_codebase/aletheore_answer required
    # .aletheore/index.lancedb, buildable only via the CLI's `aletheore
    # index` command - no MCP tool existed to build it, forcing an agent
    # using only MCP tools to shell out anyway.
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    with patch("aletheore.search_index.build_index", return_value=7) as mock_build_index:
        result = await server.call_tool("aletheore_index", {})

    assert tool_result_body(result)["result"] == {"indexed_chunks": 7}
    mock_build_index.assert_called_once()


@pytest.mark.asyncio
async def test_aletheore_index_tool_forbids_hosted_embeddings_by_default(tmp_path):
    # Default MCP posture is EFFECT_WRITE + EFFECT_NETWORK only (mcp_server.py's
    # _DEFAULT_ALLOWED_EFFECTS) - EFFECT_EXTERNAL is off, so a logged-in user's
    # code must not silently reach the hosted embedding endpoint through MCP.
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)  # default effects, no `allow=` override

    with patch("aletheore.search_index.build_index", return_value=3) as mock_build_index:
        await server.call_tool("aletheore_index", {})

    _, kwargs = mock_build_index.call_args
    assert kwargs["allow_hosted"] is False


@pytest.mark.asyncio
async def test_aletheore_index_tool_permits_hosted_embeddings_when_external_is_allowed(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo, allow=frozenset({"write", "network", "external"}))

    with patch("aletheore.search_index.build_index", return_value=3) as mock_build_index:
        await server.call_tool("aletheore_index", {})

    _, kwargs = mock_build_index.call_args
    assert kwargs["allow_hosted"] is True


@pytest.mark.asyncio
async def test_aletheore_index_tool_returns_error_instead_of_raising(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    def _raise(*args, **kwargs):
        raise RuntimeError("no embedding provider available")

    with patch("aletheore.search_index.build_index", side_effect=_raise):
        result = await server.call_tool("aletheore_index", {})

    assert tool_result_body(result)["result"] == {"error": "no embedding provider available"}


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
async def test_aletheore_find_evidence_for_endpoint_returns_location(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool(
        "aletheore_find_evidence_for_endpoint", {"method": "GET", "path": "/health"}
    )

    body = tool_result_body(result)["result"]
    assert body["file"] == "a.py"
    assert body["line"] == 1
    assert body["symbol"] == "foo"


@pytest.mark.asyncio
async def test_aletheore_find_evidence_for_symbol_returns_location(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("aletheore_find_evidence_for_symbol", {"symbol": "foo"})

    body = tool_result_body(result)["result"]
    assert body["file"] == "a.py"
    assert body["line"] == 1


@pytest.mark.asyncio
async def test_aletheore_find_evidence_for_dependency_returns_location(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("aletheore_find_evidence_for_dependency", {"dependency": "b.py"})

    body = tool_result_body(result)["result"]
    assert body["file"] == "a.py"
    assert body["dependency"] == "b.py"


@pytest.mark.asyncio
async def test_aletheore_dead_code_tool_returns_toon_results(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("aletheore_dead_code", {})

    assert tool_result_body(result)["result"]["unreachable_modules"][0]["path"] == "unused.py"


@pytest.mark.asyncio
async def test_aletheore_database_tool_returns_toon_results(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("aletheore_database", {})

    assert tool_result_body(result)["result"]["migration_directories"][0]["path"] == "migrations"


@pytest.mark.asyncio
async def test_aletheore_infrastructure_tool_returns_toon_results(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("aletheore_infrastructure", {})

    assert tool_result_body(result)["result"]["docker_compose_services"][0]["file"] == (
        "docker-compose.yml"
    )


@pytest.mark.asyncio
async def test_aletheore_environment_variables_tool_returns_toon_results(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("aletheore_environment_variables", {})

    assert tool_result_body(result)["result"]["declared"][0]["name"] == "FOO"


@pytest.mark.asyncio
async def test_aletheore_hotspots_tool_returns_toon_results(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("aletheore_hotspots", {})

    assert tool_result_body(result)["result"][0]["path"] == "a.py"


@pytest.mark.asyncio
async def test_aletheore_managed_audit_tool_calls_client(tmp_path, monkeypatch):
    repo = make_repo_with_evidence(tmp_path)
    # This tool uploads evidence off-machine, so it is withheld by default -
    # opt in explicitly to exercise it.
    server = build_server(repo, allow=frozenset({"write", "network", "external"}))
    monkeypatch.setattr(
        "aletheore.mcp_server.run_managed_audit_request",
        lambda evidence, token: "# Report\n\nmanaged audit text",
    )

    result = await server.call_tool("aletheore_managed_audit", {"token": "real-token"})

    assert tool_result_body(result)["result"]["report"].startswith("# Report")


@pytest.mark.asyncio
async def test_aletheore_managed_audit_tool_falls_back_to_saved_credential(tmp_path, monkeypatch):
    # Before this fix, this tool only checked os.environ.get("ALETHEORE_API_TOKEN")
    # directly - a user who ran `aletheore login` (saved to the OS
    # keychain/credentials file, no env var set) got a false "no token
    # available" error through MCP even though the CLI's own `audit --managed`
    # worked fine for the exact same saved credential.
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo, allow=frozenset({"write", "network", "external"}))
    monkeypatch.delenv("ALETHEORE_API_TOKEN", raising=False)
    monkeypatch.setattr(
        "aletheore.mcp_server.get_api_key",
        lambda env_var, provider_name, **kwargs: "token-from-keychain",
    )
    captured = {}

    def fake_run_managed_audit_request(evidence, token):
        captured["token"] = token
        return "# Report"

    monkeypatch.setattr(
        "aletheore.mcp_server.run_managed_audit_request", fake_run_managed_audit_request
    )

    result = await server.call_tool("aletheore_managed_audit", {})

    assert captured["token"] == "token-from-keychain"
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
    evidence_path = repo / ".aletheore" / "air.json"
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
    (repo / ".aletheore" / "air.json").write_text(json.dumps({"repository": {"modules": []}}))
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
async def test_aletheore_search_rejects_invalid_regex_pattern(tmp_path):
    # Previously an invalid pattern raised an uncaught re.error - a crash,
    # not a normal tool result.
    repo = make_repo_with_files(tmp_path, {"a.py": "x = 1\n"})
    server = build_server(repo)

    result = await server.call_tool(
        "aletheore_search", {"pattern": "(unbalanced", "regex": True}
    )

    body = tool_result_body(result)["result"]
    assert "invalid regex" in body["error"]


@pytest.mark.asyncio
async def test_aletheore_search_times_out_on_catastrophic_backtracking(tmp_path):
    # (a+)+$ against a run of a's with no trailing match is the textbook
    # ReDoS case - measured ~23s for one 29-char line in the audit that
    # found this. The call must return within the search's time budget
    # (the worker process gets killed) rather than hang, and must say so
    # rather than silently reporting no match.
    evil_line = "a" * 30 + "!"
    repo = make_repo_with_files(tmp_path, {"evil.py": evil_line + "\n"})
    server = build_server(repo)

    result = await server.call_tool(
        "aletheore_search", {"pattern": r"(a+)+$", "regex": True}
    )

    body = tool_result_body(result)["result"]
    assert "time budget" in body["error"]


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
    # AWS's own docs use this exact value as their example key - a value
    # that actually looks like a placeholder, not just a path that does
    # (a random-looking key under tests/ is a real finding, see
    # test_find_secrets_does_not_downgrade_a_real_looking_secret_under_a_test_path).
    (repo / "tests" / "fixture.py").write_text('AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')
    server = build_server(repo)

    result = await server.call_tool("aletheore_scan", {})

    summary = tool_result_body(result)["result"]
    assert summary["secrets"]["total_findings"] == 1
    assert summary["secrets"]["real_findings"] == 0


@pytest.mark.asyncio
async def test_aletheore_healthcheck_tool_returns_results(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    evidence_path = repo / ".aletheore" / "air.json"
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

    with patch("aletheore.healthcheck._NO_REDIRECT_OPENER.open", return_value=response):
        result = await server.call_tool(
            "aletheore_healthcheck", {"base_url": "http://localhost:5000"}
        )

    body = tool_result_body(result)["result"]
    assert body["results"][0]["status_code"] == 200


@pytest.mark.asyncio
async def test_aletheore_healthcheck_tool_rejects_file_scheme_base_url(tmp_path):
    # An LLM agent driven by untrusted content in a scanned repo (e.g. a
    # malicious README) could try to point this tool at file:// to read
    # local files instead of probing a real HTTP endpoint - this must
    # surface as a normal tool error, not silently open the local file.
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool(
        "aletheore_healthcheck", {"base_url": "file:///etc/passwd"}
    )

    body = tool_result_body(result)["result"]
    assert "http or https" in body["error"]
