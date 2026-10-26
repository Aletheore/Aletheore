"""Agent-facing initialization guidance must match the tools actually exposed."""

import re
from unittest.mock import MagicMock

import pytest

from aletheore.mcp_server import build_server


PROFILES = ("full", "lean", "router", "nav")
EFFECTS = (
    frozenset({"read"}),
    frozenset({"read", "write"}),
    frozenset({"read", "network"}),
    frozenset({"read", "write", "network", "external"}),
)


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("effects", EFFECTS)
async def test_guidance_names_only_registered_tools_and_keeps_safety_contract(tmp_path, profile, effects):
    server = build_server(tmp_path, profile=profile, allow=effects)
    names = {tool.name for tool in await server.list_tools()}
    instructions = server.instructions
    mentioned = set(re.findall(r"\baletheore_[a-z_]+\b", instructions))
    assert mentioned <= names, mentioned - names
    text = instructions.lower()
    assert "snapshot" in text
    assert "truncation" in text
    assert "untrusted data" in text
    assert "user authorization" in text
    assert "redact" in text
    assert "counts and lists are exact" not in text
    assert "every result cites a real file:line" not in text
    if profile != "full":
        assert "no auto-watch" in text
        assert "missing/stale" in text
    if profile in ("full", "lean", "router"):
        assert "candidates" in text and "coverage" in text
    if profile == "nav":
        assert len(instructions) < 600
    if profile == "router":
        assert len(instructions) < 700


@pytest.mark.asyncio
@pytest.mark.parametrize("effects", EFFECTS)
@pytest.mark.parametrize("adapter_consent", (None, False, True))
@pytest.mark.parametrize("sonarqube", (False, True))
async def test_full_guidance_tracks_adapter_and_source_upload_gates(
    tmp_path, monkeypatch, effects, adapter_consent, sonarqube,
):
    monkeypatch.setattr("aletheore.mcp_server._sonarqube_upload_configured", lambda: sonarqube)
    adapter = None if adapter_consent is None else MagicMock(requires_consent=adapter_consent)
    server = build_server(tmp_path, allow=effects, answer_adapter=adapter)
    names = {tool.name for tool in await server.list_tools()}
    mentioned = set(re.findall(r"\baletheore_[a-z_]+\b", server.instructions))
    assert mentioned <= names, mentioned - names
    for capability in ("aletheore_scan", "aletheore_index", "aletheore_search_codebase", "aletheore_answer"):
        assert (capability in mentioned) == (capability in names)
    if "aletheore_scan" not in names:
        assert "this session cannot run a scan" in server.instructions
        assert "do not bypass" in server.instructions
    if "aletheore_search_codebase" in names and "aletheore_index" not in names:
        assert "Index creation is unavailable" in server.instructions
        assert "operator refresh" in server.instructions


@pytest.mark.parametrize("watch", (False, True))
@pytest.mark.parametrize("write", (False, True))
@pytest.mark.parametrize("disabled_by_env", (False, True))
def test_full_guidance_reports_watch_request_without_guaranteeing_freshness(
    tmp_path, monkeypatch, watch, write, disabled_by_env,
):
    monkeypatch.setattr("aletheore.mcp_server.ensure_watcher", lambda *args, **kwargs: None)
    monkeypatch.setenv("ALETHEORE_MCP_WATCH", "0" if disabled_by_env else "1")
    effects = frozenset({"read", "write"} if write else {"read"})
    text = build_server(tmp_path, watch=watch, allow=effects).instructions
    if watch and write and not disabled_by_env:
        assert "watching is requested" in text
        assert "may decline to start" in text
    else:
        assert "watching is off in this session" in text
    assert "--no-watch" in text and "ALETHEORE_MCP_WATCH=0" in text
    assert "reuses the last full scan" in text


@pytest.mark.asyncio
async def test_tool_descriptions_do_not_present_test_candidates_as_coverage(tmp_path):
    tools = {tool.name: tool for tool in await build_server(tmp_path).list_tools()}
    assert "candidate tests" in tools["aletheore_ask"].description
    assert "which tests cover" not in tools["aletheore_ask"].description
    assert "Candidates" in tools["aletheore_dead_code"].description


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", PROFILES)
async def test_missing_evidence_recovery_does_not_direct_agents_to_an_absent_scan_tool(tmp_path, profile):
    server = build_server(tmp_path, profile=profile, allow=frozenset({"read"}))
    tool = {"nav": "aletheore_locate", "lean": "aletheore_overview", "router": "aletheore_ask",
            "full": "aletheore_overview"}[profile]
    arguments = {"nav": {"query": "missing"}, "router": {"question": "repo overview"}}.get(profile, {})
    result = await server.call_tool(tool, arguments)
    assert result.is_error
    assert "ask the user/operator" in result.content[0].text
    assert "listed and authorized" in result.content[0].text
    assert "aletheore_scan" not in result.content[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ("aletheore_search_codebase", "aletheore_answer"))
async def test_missing_index_recovery_respects_read_network_only_permissions(tmp_path, monkeypatch, tool):
    from aletheore.search_index import IndexNotFoundError

    def missing(*args, **kwargs):
        raise IndexNotFoundError("missing")

    monkeypatch.setattr("aletheore.mcp_server.search_index", missing)
    monkeypatch.setattr("aletheore.mcp_server.answer_question", missing)
    server = build_server(tmp_path, allow=frozenset({"read", "network"}),
                          answer_adapter=MagicMock(requires_consent=False))
    arguments = {"query": "where is the parser"} if tool.endswith("codebase") else {"question": "where is the parser"}
    result = await server.call_tool(tool, arguments)
    assert result.is_error
    assert "index creation is unavailable" in result.content[0].text
    assert "aletheore_index" not in result.content[0].text
    assert "do not bypass permissions" in result.content[0].text
