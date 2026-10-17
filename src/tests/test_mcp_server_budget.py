import json

import pytest

from aletheore.mcp_server import _toon_result, build_server, read_evidence
from aletheore.query import find_vulnerabilities
from tests.test_mcp_server import make_repo_with_evidence, tool_result_body

BUDGET_PARAMS = {"section", "offset", "limit", "filter"}


def bloat(repo, n=300):
    path = repo / ".aletheore" / "air.json"
    evidence = json.loads(path.read_text())
    evidence["repository"]["api_endpoints"]["endpoints"] = [
        {"method": "GET", "path": f"/r/{i}", "framework": "fastapi", "file": "a.py", "line": i + 1,
         "handler": f"h{i}", "unresolved": False}
        for i in range(n)
    ]
    path.write_text(json.dumps(evidence))


async def tool_named(server, name):
    return {t.name: t for t in await server.list_tools()}[name]


@pytest.mark.asyncio
async def test_a_no_target_tool_always_has_paging_parameters_and_documents_them(tmp_path):
    # Real audit finding: a whole family of no-target evidence tools
    # (aletheore_database, aletheore_error_handling, aletheore_environment_variables,
    # aletheore_static_analysis, aletheore_endpoints, ...) returned their
    # section raw with no cap at all - on this project's own evidence,
    # aletheore_database alone rendered to 972,404 characters. This is on
    # by default, not opt-in behind an env var: an agent has no way to
    # discover or set one, and the capability this fixes is correctness,
    # not a token-savings lever that needs benchmarking before shipping.
    server = build_server(make_repo_with_evidence(tmp_path))
    tool = await tool_named(server, "aletheore_endpoints")
    assert set(tool.input_schema["properties"]) == BUDGET_PARAMS
    assert "section" in tool.description and "All API endpoints" in tool.description
    # tools that take a target keep their exact signature - the budget only
    # applies to repo-wide, no-target results.
    imports = await tool_named(server, "aletheore_imports")
    assert set(imports.input_schema["properties"]) == {"target"}


@pytest.mark.asyncio
async def test_over_budget_result_is_bounded_and_pageable_to_the_exact_original(tmp_path, monkeypatch):
    # query_budget_chars is read once via budget_chars() when the server's
    # tools are registered, so the env var has to be set before build_server
    # runs, not patched onto a module attribute afterward.
    monkeypatch.setenv("ALETHEORE_MCP_BUDGET_CHARS", "3000")
    repo = make_repo_with_evidence(tmp_path)
    bloat(repo, 300)
    server = build_server(repo)

    first = await server.call_tool("aletheore_endpoints", {})
    assert len(first.content[0].text) <= 3000
    info = tool_result_body(first)["result"]["_budget"]["truncated"]["endpoints"]
    assert info["total"] == 300 and 3 <= info["shown"] < 300

    paths, offset = [], 0
    while offset is not None:
        page = await server.call_tool("aletheore_endpoints", {"section": "endpoints", "offset": offset})
        assert len(page.content[0].text) <= 3000
        body = tool_result_body(page)["result"]
        paths.extend(item["path"] for item in body["items"])
        offset = body["next_offset"]
    assert paths == [f"/r/{i}" for i in range(300)]

    filtered = tool_result_body(
        await server.call_tool("aletheore_endpoints", {"section": "endpoints", "filter": "/r/17"})
    )["result"]
    assert filtered["total"] == 11 and filtered["unfiltered_total"] == 300  # /r/17, /r/170 .. /r/179


@pytest.mark.asyncio
async def test_a_small_result_is_returned_exactly_as_the_underlying_query_gives_it(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)
    got = (await server.call_tool("aletheore_vulnerabilities", {})).content[0].text
    assert got == _toon_result(find_vulnerabilities(read_evidence(repo), None))


@pytest.mark.asyncio
async def test_unknown_section_is_a_clear_error_not_a_crash(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    bloat(repo, 5)
    server = build_server(repo)
    body = tool_result_body(await server.call_tool("aletheore_endpoints", {"section": "nope"}))["result"]
    assert "unknown section" in body["error"] and body["sections"]["endpoints"] == 5
