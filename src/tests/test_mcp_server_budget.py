import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from aletheore.mcp_server import _TOOL_NAME_TO_QUERY_KIND, _toon_result, build_server, read_evidence
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
async def test_query_tools_share_one_paging_tool_instead_of_four_parameters_each(tmp_path):
    # Real audit finding: a family of no-target evidence tools (aletheore_database, aletheore_error_handling, ...)
    # once returned their section raw with no cap - aletheore_database alone rendered to 972,404 characters - so every
    # query tool is still bounded by default. The four paging arguments used to be repeated on all 18 of them, about
    # 1,900 tokens a turn; they now live on one tool, aletheore_page, that re-runs the named query and returns a page.
    server = build_server(make_repo_with_evidence(tmp_path))
    tools = {t.name: t for t in await server.list_tools()}

    endpoints = tools["aletheore_endpoints"]
    assert "All API endpoints" in endpoints.description
    assert not (set(endpoints.input_schema.get("properties") or {}) & BUDGET_PARAMS)
    # A tool that takes a target still takes only that, and is still bounded.
    assert set(tools["aletheore_imports"].input_schema["properties"]) == {"target"}
    # None of the 18 query tools carries the paging arguments any more (other tools, such as aletheore_list, have their
    # own unrelated `filter`).
    assert not any(
        set((tools[name].input_schema.get("properties") or {})) & BUDGET_PARAMS for name in _TOOL_NAME_TO_QUERY_KIND
    )

    page = tools["aletheore_page"]
    assert set(page.input_schema["properties"]) == {"tool", "target"} | BUDGET_PARAMS
    assert all(prop.get("description") for prop in page.input_schema["properties"].values())
    assert page.input_schema["required"] == ["tool", "section"]
    assert "aletheore_page" in server.instructions


@pytest.mark.asyncio
async def test_the_paging_tool_is_offered_only_where_a_pageable_tool_is(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    for profile, expected in (("full", True), ("lean", True), ("nav", False), ("router", False)):
        names = {t.name for t in await build_server(repo, profile=profile).list_tools()}
        assert ("aletheore_page" in names) is expected, profile


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
    marker = tool_result_body(first)["result"]["_budget"]
    info = marker["truncated"]["endpoints"]
    assert info["total"] == 300 and 3 <= info["shown"] < 300
    # The cut result names the tool that pages it and the tool to pass.
    assert "aletheore_page" in marker["how_to_get_more"] and "aletheore_endpoints" in marker["how_to_get_more"]

    paths, offset = [], 0
    while offset is not None:
        page = await server.call_tool(
            "aletheore_page", {"tool": "aletheore_endpoints", "section": "endpoints", "offset": offset}
        )
        assert len(page.content[0].text) <= 3000
        body = tool_result_body(page)["result"]
        paths.extend(item["path"] for item in body["items"])
        offset = body["next_offset"]
    assert paths == [f"/r/{i}" for i in range(300)]

    filtered = tool_result_body(
        await server.call_tool(
            "aletheore_page", {"tool": "aletheore_endpoints", "section": "endpoints", "filter": "/r/17"}
        )
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
    marker = tool_result_body(first)["result"]["_budget"]
    info = marker["truncated"]["functions"]
    assert info["total"] == 300 and 3 <= info["shown"] < 300
    assert "target='a.py'" in marker["how_to_get_more"]  # a targeted tool's continue hint carries its target

    names, offset = [], 0
    while offset is not None:
        page = await server.call_tool(
            "aletheore_page",
            {"tool": "aletheore_symbols", "target": "a.py", "section": "functions", "offset": offset},
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
    body = tool_result_body(
        await server.call_tool("aletheore_page", {"tool": "aletheore_endpoints", "section": "nope"})
    )["result"]
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

    await server.call_tool("aletheore_page", {"tool": "aletheore_endpoints", "section": "endpoints", "offset": 0, "limit": 5})
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


def test_untrimmed_result_serializes_only_once(tmp_path, monkeypatch):
    from aletheore import mcp_server
    calls = []
    encode = mcp_server._toon_result
    original = {'items': ['small']}
    def serializer(value):
        calls.append(value)
        return encode(value)
    monkeypatch.setattr(mcp_server, '_toon_result', serializer)
    assert mcp_server._metered_budget(tmp_path, original, budget_chars=10000) == encode(original)
    assert calls == [original]


def test_fallback_measurement_failure_cannot_break_response(tmp_path, monkeypatch, caplog):
    from aletheore import mcp_server
    original = {'items': ['x' * 200] * 100}
    bounded = {'items': ['x'], '_budget': {'trimmed': True}}
    encode = mcp_server._toon_result
    def serializer(value):
        if value is original:
            raise ValueError('controlled fallback failure')
        return encode(value)
    def budget(value, *, size_of, **kwargs):
        size_of(bounded)
        return bounded
    monkeypatch.setattr(mcp_server, '_toon_result', serializer)
    monkeypatch.setattr(mcp_server, 'apply_budget', budget)
    with caplog.at_level('DEBUG', logger='aletheore.mcp_server'):
        assert mcp_server._metered_budget(tmp_path, original) == encode(bounded)
    assert 'token meter event recording failed' in caplog.text


@pytest.mark.asyncio
async def test_the_paging_tool_rejects_an_unknown_tool_and_a_missing_target(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    unknown = tool_result_body(await server.call_tool("aletheore_page", {"tool": "aletheore_nope", "section": "$"}))["result"]
    assert "aletheore_endpoints" in unknown["tools"] and "unknown" in unknown["error"]

    missing = tool_result_body(await server.call_tool("aletheore_page", {"tool": "aletheore_symbols", "section": "functions"}))["result"]
    assert "target" in missing["error"]


@pytest.mark.asyncio
async def test_a_client_still_passing_the_old_paging_arguments_gets_a_loud_error_not_a_silent_first_page(tmp_path):
    # Paging moved to aletheore_page. A pydantic model ignores unknown arguments by default, so without this a client
    # that still sends section/offset to aletheore_endpoints would quietly get the first page and think it paged.
    repo = make_repo_with_evidence(tmp_path)
    bloat(repo, 5)
    server = build_server(repo)
    for arguments in ({"section": "endpoints"}, {"offset": 3}):
        # Over the wire this is an is_error result; the in-process server raises the same ToolError.
        with pytest.raises(ToolError) as error:
            await server.call_tool("aletheore_endpoints", arguments)
        assert next(iter(arguments)) in str(error.value) and "Extra inputs are not permitted" in str(error.value)
    ok = await server.call_tool("aletheore_endpoints", {})
    assert ok.is_error is not True


@pytest.mark.asyncio
async def test_a_page_of_an_optional_target_tool_says_which_scope_it_was_read_from(tmp_path):
    # aletheore_ownership is the one evidence tool whose target is optional: with none it is repo-wide. A page read
    # without the target the cut result was made with would otherwise look like the same list and not be.
    repo = make_repo_with_evidence(tmp_path)
    server = build_server(repo)

    repo_wide = tool_result_body(await server.call_tool("aletheore_page", {"tool": "aletheore_ownership", "section": "$"}))["result"]
    assert repo_wide["scope"] == "repo-wide"

    scoped = tool_result_body(
        await server.call_tool("aletheore_page", {"tool": "aletheore_ownership", "section": "$", "target": "a.py"})
    )["result"]
    assert scoped["scope"] == "a.py"

    # Tools with a required target or none need no echo: the call already names it, or there is none to name.
    bloat(repo, 5)
    plain = tool_result_body(await build_server(repo).call_tool(
        "aletheore_page", {"tool": "aletheore_endpoints", "section": "endpoints"}
    ))["result"]
    assert "scope" not in plain


def test_rejecting_unknown_arguments_never_breaks_server_startup_when_fastmcp_internals_differ():
    from unittest.mock import MagicMock

    from aletheore.mcp_server import _reject_unknown_arguments

    no_metadata = MagicMock(); no_metadata.name = "aletheore_imports"; no_metadata.fn_metadata = None
    no_model = MagicMock(); no_model.name = "aletheore_symbols"; no_model.fn_metadata.arg_model = None
    unrelated = MagicMock(); unrelated.name = "aletheore_find_skill"
    broken_model = MagicMock(); broken_model.name = "aletheore_endpoints"
    broken_model.fn_metadata.arg_model.model_rebuild.side_effect = RuntimeError("internals moved")
    server = MagicMock()
    server._tool_manager.list_tools.return_value = [no_metadata, no_model, unrelated, broken_model]
    _reject_unknown_arguments(server, {"aletheore_imports", "aletheore_symbols", "aletheore_endpoints"})  # must not raise

    class NoManager:
        pass

    _reject_unknown_arguments(NoManager(), {"aletheore_imports"})  # a server without the private manager: skip, do not raise
