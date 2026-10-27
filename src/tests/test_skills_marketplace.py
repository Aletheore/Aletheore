from unittest.mock import MagicMock, patch

import pytest

from aletheore import skill_hub, skills_marketplace
from aletheore.mcp_server import build_server


def _response(payload):
    response = MagicMock()
    response.json.return_value = payload
    return response


def test_search_external_skills_maps_metadata_bounds_limit_and_query():
    payload = {
        "skills": [
            {
                "id": "owner/repo/task-skill",
                "name": "task-skill",
                "source": "owner/repo",
                "installs": 1245,
            }
        ]
    }
    with patch.object(skills_marketplace.httpx, "get", return_value=_response(payload)) as get:
        result = skills_marketplace.search_external_skills("  build a useful skill  ", limit=99)

    assert result["status"] == "ok"
    assert result["results"] == [
        {
            "name": "task-skill",
            "slug": "owner/repo/task-skill",
            "source": "owner/repo",
            "installs": 1245,
            "registry_url": "https://skills.sh/owner/repo/task-skill",
            "trust": "unvetted; review source and security report before adding to Hallbook",
        }
    ]
    assert get.call_args.args == (skills_marketplace.SEARCH_URL,)
    assert get.call_args.kwargs["params"] == {
        "q": "build a useful skill",
        "limit": str(skills_marketplace.MAX_RESULTS),
    }
    assert get.call_args.kwargs["follow_redirects"] is False


def test_search_external_skills_uses_default_limit_for_invalid_limit():
    payload = {"skills": []}
    with patch.object(skills_marketplace.httpx, "get", return_value=_response(payload)) as get:
        skills_marketplace.search_external_skills("find skills", limit="invalid")

    assert get.call_args.kwargs["params"]["limit"] == "5"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"skills": "unexpected"},
        {"skills": [{"id": "../repo/skill", "name": "unsafe", "installs": 1}]},
        {"skills": [{"id": "owner/repo/task-skill", "name": "", "installs": 1}]},
    ],
)
def test_search_external_skills_rejects_malformed_payloads_and_slugs(payload):
    with patch.object(skills_marketplace.httpx, "get", return_value=_response(payload)):
        result = skills_marketplace.search_external_skills("find a skill")

    if "skills" not in payload or not isinstance(payload.get("skills"), list):
        assert result == {"status": "unavailable", "results": []}
    else:
        assert result == {"status": "ok", "results": []}


def test_search_external_skills_fails_soft_on_http_and_json_errors():
    with patch.object(skills_marketplace.httpx, "get", side_effect=TimeoutError):
        assert skills_marketplace.search_external_skills("find a skill") == {
            "status": "unavailable",
            "results": [],
        }

    response = _response({})
    response.raise_for_status.side_effect = RuntimeError("HTTP error")
    with patch.object(skills_marketplace.httpx, "get", return_value=response):
        assert skills_marketplace.search_external_skills("find a skill")["status"] == "unavailable"


@pytest.mark.parametrize("confidence", ["low", "none"])
@pytest.mark.asyncio
async def test_external_mcp_tool_falls_back_after_low_or_no_local_match(tmp_path, monkeypatch, confidence):
    server = build_server(tmp_path)
    monkeypatch.setattr(skill_hub, "find_skill", lambda task: {"confidence": confidence})
    external = {"status": "ok", "results": [{"name": "task-skill"}]}
    search = MagicMock(return_value=external)
    monkeypatch.setattr(skills_marketplace, "search_external_skills", search)

    result = await server.call_tool(
        "aletheore_find_external_skills",
        {"task": "describe only the task, not private project details"},
    )

    body = result.content[0].text
    assert "task-skill" in body
    assert confidence in body
    search.assert_called_once_with(
        "describe only the task, not private project details", limit=5
    )


@pytest.mark.asyncio
async def test_external_mcp_tool_skips_network_for_high_confidence_local_match(tmp_path, monkeypatch):
    server = build_server(tmp_path)
    monkeypatch.setattr(skill_hub, "find_skill", lambda task: {"confidence": "high"})
    search = MagicMock()
    monkeypatch.setattr(skills_marketplace, "search_external_skills", search)

    result = await server.call_tool("aletheore_find_external_skills", {"task": "a task"})

    assert "not_needed" in result.content[0].text
    search.assert_not_called()


@pytest.mark.asyncio
async def test_external_mcp_tool_is_absent_without_network_effect(tmp_path):
    server = build_server(tmp_path, allow=frozenset())
    names = {tool.name for tool in await server.list_tools()}
    assert "aletheore_find_skill" in names
    assert "aletheore_search_skills" in names
    assert "aletheore_find_external_skills" not in names
