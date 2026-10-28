import json

import pytest

from aletheore.mcp_server import _toon_result, build_server, read_evidence
from aletheore.query import find_endpoints, find_vulnerabilities
from aletheore.token_meter import load_events
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


def bloat_symbols(repo, n=300):
    path = repo / ".aletheore" / "air.json"
    evidence = json.loads(path.read_text())
    evidence["repository"]["modules"][0]["symbols"]["functions"] = [
        {"name": f"fn_{i}", "start_line": i + 1, "end_line": i + 2, "params": "()"} for i in range(n)
    ]
    path.write_text(json.dumps(evidence))


async def tool_named(server, name):
    return {t.name: t for t in await server.list_tools()}[name]


@pytest.mark.asyncio
async def test_every_generic_query_tool_has_paging_parameters_and_documents_them(tmp_path):
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
    # Real audit finding, found later: aletheore_symbols (requires_target)
    # had no cap either - on this project's own hottest file it rendered to
    # 82,908 characters. A tool that takes a target still takes the same
    # budget parameters alongside it, not instead of it.
    imports = await tool_named(server, "aletheore_imports")
    assert set(imports.input_schema["properties"]) == {"target"} | BUDGET_PARAMS


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
async def test_a_requires_target_tool_is_bounded_and_pageable_too(tmp_path, monkeypatch):
    # Real audit finding: aletheore_symbols (requires_target=True) had no
    # cap at all - on this project's own hottest file (234 functions/
    # classes/constants) it rendered to 82,908 characters, bigger than
    # almost every no-target case the earlier audit found. apply_budget
    # now applies to the requires_target branch too, not just no-target.
    monkeypatch.setenv("ALETHEORE_MCP_BUDGET_CHARS", "3000")
    repo = make_repo_with_evidence(tmp_path)
    bloat_symbols(repo, 300)
    server = build_server(repo)

    first = await server.call_tool("aletheore_symbols", {"target": "a.py"})
    assert len(first.content[0].text) <= 3000
    info = tool_result_body(first)["result"]["_budget"]["truncated"]["functions"]
    assert info["total"] == 300 and 3 <= info["shown"] < 300

    names, offset = [], 0
    while offset is not None:
        page = await server.call_tool(
            "aletheore_symbols", {"target": "a.py", "section": "functions", "offset": offset}
        )
        assert len(page.content[0].text) <= 3000
        body = tool_result_body(page)["result"]
        names.extend(item["name"] for item in body["items"])
        offset = body["next_offset"]
    assert names == [f"fn_{i}" for i in range(300)]


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


@pytest.mark.asyncio
async def test_meter_records_exact_chars_only_for_real_trim_not_pages_or_small_results(tmp_path, monkeypatch):
    monkeypatch.setenv("ALETHEORE_MCP_BUDGET_CHARS", "3000")
    repo = make_repo_with_evidence(tmp_path)
    bloat(repo, 300)
    server = build_server(repo)

    original = _toon_result(find_endpoints(read_evidence(repo), None))
    first = await server.call_tool("aletheore_endpoints", {})
    events, corrupt = load_events(repo)
    assert corrupt == 0 and len(events) == 1
    event = events[0]
    assert event.source == "mcp_budget"
    assert event.original_chars == len(original)
    assert event.delivered_chars == len(first.content[0].text)
    assert event.delivered_chars < event.original_chars

    await server.call_tool("aletheore_endpoints", {"section": "endpoints", "offset": 0, "limit": 5})
    events_after_page, _ = load_events(repo)
    assert len(events_after_page) == 1


@pytest.mark.asyncio
async def test_meter_does_not_create_events_for_results_that_fit(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)
    await server.call_tool("aletheore_vulnerabilities", {})
    assert load_events(repo) == ([], 0)
    assert not (repo / ".aletheore" / "token_meter").exists()


@pytest.mark.asyncio
async def test_meter_recording_failure_never_breaks_mcp_response(tmp_path, monkeypatch):
    from aletheore import token_meter

    monkeypatch.setenv("ALETHEORE_MCP_BUDGET_CHARS", "3000")
    repo = make_repo_with_evidence(tmp_path)
    bloat(repo, 300)
    monkeypatch.setattr(token_meter, "record_mcp_trim", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    server = build_server(repo)
    response = await server.call_tool("aletheore_endpoints", {})
    assert response.content and "_budget" in response.content[0].text


@pytest.mark.asyncio
async def test_meter_records_semantic_code_search_trimming(tmp_path, monkeypatch):
    from aletheore import mcp_server

    monkeypatch.setenv("ALETHEORE_MCP_BUDGET_CHARS", "3000")
    repo = make_repo_with_evidence(tmp_path)
    results = [
        {"module_path": f"module_{index}.py", "symbol_name": f"symbol_{index}", "text": "x" * 500}
        for index in range(200)
    ]
    monkeypatch.setattr(mcp_server, "search_index", lambda *args, **kwargs: results)
    server = build_server(repo)

    original_chars = len(_toon_result(results))
    response = await server.call_tool("aletheore_search_codebase", {"query": "example", "k": 200})
    body = tool_result_body(response)["result"]
    assert "_budget" in body
    events, corrupt = load_events(repo)
    assert corrupt == 0 and len(events) == 1
    assert events[0].source == "mcp_budget"
    assert events[0].original_chars == original_chars
    assert events[0].delivered_chars == len(response.content[0].text)
    assert events[0].delivered_chars < events[0].original_chars


def test_meter_reuses_original_and_final_budget_serializations(tmp_path, monkeypatch):
    from aletheore import mcp_server
    original = {'items': ['x' * 200] * 100}
    bounded = {'items': ['x'], '_budget': {'trimmed': True}}
    seen = []
    encode = mcp_server._toon_result

    def serializer(value):
        seen.append(value)
        return encode(value)

    def budget(value, *, size_of, **options):
        size_of(value)
        size_of(bounded)
        return bounded

    monkeypatch.setattr(mcp_server, '_toon_result', serializer)
    monkeypatch.setattr(mcp_server, 'apply_budget', budget)
    assert mcp_server._metered_budget(tmp_path, original) == encode(bounded)
    assert len(seen) == 2
    assert seen[0] is original and seen[1] is bounded


@pytest.mark.parametrize('measure_original', [False, True])
def test_meter_serializes_returned_result_even_after_intermediate_measurements(tmp_path, monkeypatch, measure_original):
    from aletheore import mcp_server
    original = {'items': ['x' * 200] * 100}
    bounded = {'items': ['x'], '_budget': {'trimmed': True}}
    def budget(value, *, size_of, **options):
        if measure_original:
            size_of(value)
        else:
            size_of(dict(value))
        size_of(bounded)
        size_of({'intermediate': 'must never be returned'})
        return bounded
    monkeypatch.setattr(mcp_server, 'apply_budget', budget)
    recorded = []
    from aletheore import token_meter
    monkeypatch.setattr(token_meter, 'record_mcp_trim', lambda *args: recorded.append(args))
    assert mcp_server._metered_budget(tmp_path, original) == mcp_server._toon_result(bounded)
    assert recorded[0][1:] == (len(mcp_server._toon_result(original)), len(mcp_server._toon_result(bounded)))
