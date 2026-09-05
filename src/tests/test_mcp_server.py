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
        "repo_age_days": 30,
        "commit_cadence": {"weekly_counts": [1, 2, 1], "trend": "stable", "most_recent_week_partial": False},
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
        "aletheore_get_blast_radius",
        "aletheore_list",
        "aletheore_overview",
        "aletheore_search",
        "aletheore_ast_pattern",
        "aletheore_symbol_source",
        "aletheore_verify_citations",
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
    assert len(names) == 33
    assert "aletheore_answer" not in names


def test_build_server_surfaces_instructions_in_the_handshake(tmp_path):
    # Unlike a resource the connecting agent would have to separately think
    # to fetch, `instructions` is surfaced in the MCP `initialize` handshake
    # itself - every client shows it to the agent before any tool is called.
    repo = make_repo_with_evidence(tmp_path)

    server = build_server(repo)

    assert server.instructions
    assert "aletheore_overview" in server.instructions
    assert "vocabulary" in server.instructions.lower()


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


def test_toon_result_falls_back_to_json_on_encoding_failure(monkeypatch):
    from aletheore.mcp_server import _toon_result
    from aletheore.toon_encoding import ToonEncodingError

    def _boom(_data):
        raise ToonEncodingError("simulated failure")

    monkeypatch.setattr("aletheore.mcp_server.to_toon", _boom)

    result = _toon_result({"symbols": ["a", "b"]})

    assert json.loads(result) == {"result": {"symbols": ["a", "b"]}}


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
async def test_aletheore_answer_returns_friendly_error_on_dimension_mismatch(tmp_path):
    # Same class of bug as aletheore_search_codebase above: answer_question
    # calls search_index too (see aletheore/answer.py), so it can raise the
    # same IndexDimensionMismatchError. Before this fix, aletheore_answer
    # only caught IndexNotFoundError and let this one escape as a raw
    # traceback.
    from aletheore.mcp_server import IndexDimensionMismatchError

    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo, answer_adapter=MagicMock())

    with patch(
        "aletheore.mcp_server.answer_question",
        side_effect=IndexDimensionMismatchError("the index at ... holds 1536-dimension vectors ..."),
    ):
        result = await server.call_tool("aletheore_answer", {"question": "what does foo do"})

    assert "1536-dimension" in tool_result_body(result)["result"]["error"]


@pytest.mark.asyncio
async def test_aletheore_answer_tool_forbids_hosted_embeddings_by_default(tmp_path):
    # Regression test: aletheore_answer used to call answer_question with no
    # allow_hosted argument at all, so the question (and retrieved chunks)
    # still reached Aletheore's hosted embedding endpoint whenever a token
    # was configured, regardless of the operator's actual EFFECT_EXTERNAL
    # grant - unlike aletheore_index/aletheore_search_codebase, which this
    # same default-posture test already covers for their own hosted calls.
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo, answer_adapter=MagicMock())  # default effects, no `allow=` override

    with patch("aletheore.mcp_server.answer_question", return_value={"answer": "", "cited_chunks": [], "confidence_gated": True}) as mock_answer:
        await server.call_tool("aletheore_answer", {"question": "what does foo do"})

    assert mock_answer.call_args.kwargs["allow_hosted"] is False


@pytest.mark.asyncio
async def test_aletheore_answer_tool_permits_hosted_embeddings_when_external_is_allowed(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(
        repo, answer_adapter=MagicMock(), allow=frozenset({"write", "network", "external"})
    )

    with patch("aletheore.mcp_server.answer_question", return_value={"answer": "", "cited_chunks": [], "confidence_gated": True}) as mock_answer:
        await server.call_tool("aletheore_answer", {"question": "what does foo do"})

    assert mock_answer.call_args.kwargs["allow_hosted"] is True


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
async def test_aletheore_overview_returns_repo_summary(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("aletheore_overview", {})

    body = tool_result_body(result)["result"]
    assert body["module_count"] == 2
    assert "languages" in body
    assert "git" in body


@pytest.mark.asyncio
async def test_aletheore_symbol_source_returns_exact_source(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("aletheore_symbol_source", {"module": "a.py", "symbol": "foo"})

    assert tool_result_body(result)["result"]["source"] == "def foo():"


@pytest.mark.asyncio
async def test_aletheore_verify_citations_reports_verified_and_unverified(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    (repo / "a.py").write_text("def foo():\n    pass\n")
    server = build_server(repo)

    report = "See `a.py:1` for the real one and `nonexistent.py:5` for the fake one."
    result = await server.call_tool("aletheore_verify_citations", {"report_text": report})

    body = tool_result_body(result)["result"]
    assert body["total_citations"] == 2
    assert len(body["verified"]) == 1
    assert len(body["unverified"]) == 1
    assert body["unverified"][0]["file"] == "nonexistent.py"


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
async def test_aletheore_ownership_tool_accepts_an_optional_target(tmp_path):
    # Regression test: aletheore_ownership was wired as a zero-argument MCP
    # tool (requires_target=False), so it could never forward a target even
    # though find_ownership already branches on one for file-scoped ownership
    # via evidence["git"]["file_ownership"]. Confirms both ends of the fix -
    # a target is now accepted at all, and it actually reaches find_ownership.
    repo = make_repo_with_evidence(tmp_path)
    evidence_path = repo / ".aletheore" / "air.json"
    evidence = json.loads(evidence_path.read_text())
    evidence["git"]["file_ownership"] = {"a.py": [{"author": "alice", "commits": 3}]}
    evidence_path.write_text(json.dumps(evidence))
    server = build_server(repo)

    scoped = await server.call_tool("aletheore_ownership", {"target": "a.py"})
    aggregate = await server.call_tool("aletheore_ownership", {})

    assert tool_result_body(scoped) == {"result": [{"author": "alice", "commits": 3}]}
    assert tool_result_body(aggregate) == {"result": [{"path": "a.py", "top_author": "alice"}]}


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
async def test_aletheore_changes_returns_clear_error_for_incompatible_snapshot(tmp_path):
    from aletheore.history import save_snapshot
    from tests.air_fixtures import minimal_air_evidence

    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    evidence = minimal_air_evidence()
    save_snapshot(evidence, repo)  # first snapshot, compatible version

    bad = minimal_air_evidence()
    bad["aletheore_version"] = "0.0.1-does-not-exist"
    save_snapshot(bad, repo)  # second snapshot, incompatible

    result = await server.call_tool("aletheore_changes", {})

    body = tool_result_body(result)["result"]
    assert "error" in body
    assert "0.0.1-does-not-exist" in body["error"]


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


@pytest.mark.asyncio
async def test_aletheore_get_blast_radius_returns_direct_dependents(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("aletheore_get_blast_radius", {"target": "b.py"})

    body = tool_result_body(result)["result"]
    assert body["target"] == "b.py"
    assert body["direct_dependents"] == ["a.py"]
    assert "confirmed_callers" not in body


@pytest.mark.asyncio
async def test_aletheore_get_blast_radius_with_symbol_confirms_real_callers(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    (repo / "a.py").write_text("from b import helper\nhelper()\n")
    server = build_server(repo)

    result = await server.call_tool(
        "aletheore_get_blast_radius", {"target": "b.py", "symbol": "helper"}
    )

    body = tool_result_body(result)["result"]
    assert body["symbol"] == "helper"
    assert body["confirmed_callers"] == ["a.py"]


@pytest.mark.asyncio
async def test_aletheore_get_blast_radius_raises_for_unknown_module(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    with pytest.raises(ToolError):
        await server.call_tool("aletheore_get_blast_radius", {"target": "does/not/exist.py"})


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
async def test_aletheore_ast_pattern_finds_a_structural_match(tmp_path):
    repo = make_repo_with_files(
        tmp_path,
        {"app.py": "def plain():\n    pass\n\ndef guarded():\n    try:\n        pass\n    except ValueError:\n        pass\n"},
    )
    server = build_server(repo)

    result = await server.call_tool(
        "aletheore_ast_pattern",
        {
            "language": "python",
            "query": "(function_definition name: (identifier) @name body: (block (try_statement))) @whole",
        },
    )

    body = tool_result_body(result)["result"]
    assert body["truncated"] is False
    assert len(body["matches"]) == 1
    assert body["matches"][0]["captures"]["name"][0]["text"] == "guarded"


@pytest.mark.asyncio
async def test_aletheore_ast_pattern_reports_an_unknown_language_as_an_error_not_a_crash(tmp_path):
    repo = make_repo_with_files(tmp_path, {"app.py": "pass\n"})
    server = build_server(repo)

    result = await server.call_tool("aletheore_ast_pattern", {"language": "cobol", "query": "(anything)"})

    assert "unknown language" in tool_result_body(result)["result"]["error"].lower()


@pytest.mark.asyncio
async def test_aletheore_ast_pattern_reports_an_invalid_query_as_an_error_not_a_crash(tmp_path):
    repo = make_repo_with_files(tmp_path, {"app.py": "pass\n"})
    server = build_server(repo)

    result = await server.call_tool(
        "aletheore_ast_pattern", {"language": "python", "query": "(this_node_type_does_not_exist)"}
    )

    assert "invalid tree-sitter query" in tool_result_body(result)["result"]["error"].lower()


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


@pytest.mark.asyncio
async def test_aletheore_search_truncates_a_single_very_long_matched_line(tmp_path):
    # A minified bundle or a huge generated JSON line can match once and
    # still blow well past a reasonable result size on its own - the
    # 200-match count cap does nothing to stop this since it's one match,
    # not many.
    long_line = "MATCH_ME " + ("x" * 3000)
    repo = make_repo_with_files(tmp_path, {"bundle.min.js": long_line})
    server = build_server(repo)

    result = await server.call_tool("aletheore_search", {"pattern": "MATCH_ME"})

    matches = tool_result_body(result)["result"]["matches"]
    assert len(matches) == 1
    assert len(matches[0]["text"]) < len(long_line)
    assert matches[0]["text"].startswith("MATCH_ME")
    assert matches[0]["text"].endswith("(line truncated)")


@pytest.mark.asyncio
async def test_aletheore_search_stops_early_on_total_char_budget_even_under_the_match_cap(tmp_path):
    # Confirmed live: an unscoped search on a common word returned a result
    # an MCP client rejected for exceeding its own size limit, well under
    # the 200-match count cap (200 matches x long lines = ~600,000 chars in
    # this exact repro). The count cap alone can't catch this - only an
    # aggregate character budget does.
    long_line = "MATCH_ME " + ("x" * 3000)
    content = "\n".join(long_line for _ in range(200))
    repo = make_repo_with_files(tmp_path, {"big.js": content})
    server = build_server(repo)

    result = await server.call_tool("aletheore_search", {"pattern": "MATCH_ME"})

    result_body = tool_result_body(result)["result"]
    total_chars = sum(len(m["text"]) for m in result_body["matches"])
    assert len(result_body["matches"]) < 200
    assert total_chars < 110_000
    assert result_body["truncated"] is True


@pytest.mark.asyncio
async def test_aletheore_search_respects_repo_config_ignored_paths(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    (repo / "vendor").mkdir()
    (repo / "vendor" / "bundle.js").write_text("needle in a vendored file")
    (repo / "real.py").write_text("needle in a real file")
    (repo / ".aletheore.json").write_text(json.dumps({"ignored_paths": ["vendor/**"]}))
    server = build_server(repo)

    result = await server.call_tool("aletheore_search", {"pattern": "needle"})

    matches = tool_result_body(result)["result"]["matches"]
    matched_paths = {m["path"] for m in matches}
    assert "real.py" in matched_paths
    assert "vendor/bundle.js" not in matched_paths


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


@pytest.mark.asyncio
async def test_aletheore_list_modules(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("aletheore_list", {"kind": "modules"})

    assert tool_result_body(result) == {"result": ["a.py", "b.py"]}


@pytest.mark.asyncio
async def test_aletheore_list_unknown_kind_returns_an_error(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    result = await server.call_tool("aletheore_list", {"kind": "nonsense"})

    assert "error" in tool_result_body(result)["result"]
