import json
from pathlib import Path

import pytest

from aletheore.evidence import EVIDENCE_VERSION
from aletheore.mcp_server import (
    EFFECT_EXTERNAL,
    EFFECT_NETWORK,
    EFFECT_WRITE,
    TOOL_REQUIRED_EFFECTS,
    UnknownEffectError,
    allowed_effects,
    build_server,
)
from tests.air_fixtures import minimal_air_evidence

ALL_EFFECTS = frozenset({EFFECT_WRITE, EFFECT_NETWORK, EFFECT_EXTERNAL})


def make_repo(tmp_path: Path) -> Path:
    (tmp_path / ".aletheore").mkdir()
    evidence = minimal_air_evidence()
    evidence["aletheore_version"] = EVIDENCE_VERSION
    (tmp_path / ".aletheore" / "air.json").write_text(json.dumps(evidence))
    return tmp_path


async def tool_names(repo: Path, **kwargs) -> set[str]:
    server = build_server(repo, **kwargs)
    return {tool.name for tool in await server.list_tools()}


# --- ALETHEORE_MCP_ALLOW parsing -------------------------------------------


def test_unset_allows_local_effects_but_not_evidence_upload():
    effects = allowed_effects(None)

    assert effects == frozenset({EFFECT_WRITE, EFFECT_NETWORK})
    assert EFFECT_EXTERNAL not in effects


def test_read_means_read_only_not_default_plus_read():
    # An explicit value replaces the default rather than adding to it -
    # otherwise "read" would be a no-op that looks like a lockdown.
    assert allowed_effects("read") == frozenset()


def test_values_are_parsed_case_and_whitespace_insensitively():
    assert allowed_effects(" Write , NETWORK ") == frozenset({EFFECT_WRITE, EFFECT_NETWORK})


def test_an_unknown_effect_name_is_rejected_rather_than_ignored():
    # A typo that silently left evidence upload *enabled* would be a security
    # hole with a plausible-looking explanation attached.
    with pytest.raises(UnknownEffectError) as excinfo:
        allowed_effects("write,extenral")

    assert "extenral" in str(excinfo.value)


def test_the_error_names_the_valid_effects():
    with pytest.raises(UnknownEffectError) as excinfo:
        allowed_effects("nonsense")

    message = str(excinfo.value)
    for effect in ("read", EFFECT_WRITE, EFFECT_NETWORK, EFFECT_EXTERNAL):
        assert effect in message


# --- Registration is the boundary -------------------------------------------


@pytest.mark.asyncio
async def test_evidence_upload_is_withheld_by_default(tmp_path):
    names = await tool_names(make_repo(tmp_path))

    assert "aletheore_managed_audit" not in names


@pytest.mark.asyncio
async def test_local_tools_still_work_by_default(tmp_path):
    # The default posture must not break the product's own core loop: a scan
    # writing .aletheore/ is what a tool called "aletheore" is for.
    names = await tool_names(make_repo(tmp_path))

    for expected in ("aletheore_scan", "aletheore_index", "aletheore_healthcheck"):
        assert expected in names


@pytest.mark.asyncio
async def test_opting_in_to_external_registers_the_upload_tool(tmp_path):
    names = await tool_names(make_repo(tmp_path), allow=ALL_EFFECTS)

    assert "aletheore_managed_audit" in names


@pytest.mark.asyncio
async def test_read_only_posture_withholds_every_effectful_tool(tmp_path):
    names = await tool_names(make_repo(tmp_path), allow=frozenset())

    assert names.isdisjoint(TOOL_REQUIRED_EFFECTS)
    # ...and still serves the evidence queries, which is the point of it.
    assert "aletheore_imports" in names


@pytest.mark.asyncio
async def test_withheld_tools_are_absent_not_merely_refusing(tmp_path):
    """The distinction the whole design rests on.

    A registered tool that returns "not permitted" is a convention an agent
    can keep retrying, and it still spends context advertising something it
    may not do. Absent from list_tools means it cannot be invoked at all.
    """
    repo = make_repo(tmp_path)
    server = build_server(repo, allow=frozenset())

    assert "aletheore_scan" not in {tool.name for tool in await server.list_tools()}
    with pytest.raises(Exception) as excinfo:
        await server.call_tool("aletheore_scan", {})
    assert "aletheore_scan" in str(excinfo.value)


@pytest.mark.asyncio
async def test_network_only_posture_withholds_writers_but_keeps_query_embedding(tmp_path):
    names = await tool_names(make_repo(tmp_path), allow=frozenset({EFFECT_NETWORK}))

    assert "aletheore_search_codebase" in names  # needs network only
    assert "aletheore_scan" not in names  # also needs write
    assert "aletheore_index" not in names


@pytest.mark.asyncio
async def test_the_env_var_drives_registration_when_allow_is_not_passed(tmp_path, monkeypatch):
    monkeypatch.setenv("ALETHEORE_MCP_ALLOW", "write,network,external")

    names = await tool_names(make_repo(tmp_path))

    assert "aletheore_managed_audit" in names


@pytest.mark.asyncio
async def test_withholding_prints_an_actionable_notice_to_stderr(tmp_path, capsys):
    # stdout is the MCP transport, so the operator - who is the one granting
    # consent - can only be told on stderr. A missing tool with no
    # explanation reads as a bug.
    build_server(make_repo(tmp_path), allow=frozenset())
    notice = capsys.readouterr().err

    assert "aletheore_scan" in notice
    assert "ALETHEORE_MCP_ALLOW" in notice


@pytest.mark.asyncio
async def test_nothing_is_printed_when_every_tool_is_permitted(tmp_path, capsys):
    build_server(make_repo(tmp_path), allow=ALL_EFFECTS)

    assert capsys.readouterr().err == ""


# --- The embedding fallback is unreachable from MCP -------------------------


def test_embedding_fallback_cannot_send_code_to_openai_without_a_terminal(monkeypatch):
    """Why aletheore_index and aletheore_search_codebase are `network`, not `external`.

    Both embed through Ollama and fall back to OpenAI, which on its face means
    they could ship source off-machine under the default posture. They can't:
    embed_texts refuses the fallback whenever stdin isn't a TTY, and an MCP
    server is always spawned with piped stdio. This test pins that guard,
    which lives in another module and is otherwise only incidentally
    load-bearing for this property.
    """
    from unittest.mock import patch

    from aletheore.search_index import EmbeddingProviderUnavailableError, embed_texts

    with patch("aletheore.search_index.OpenAI") as mock_openai, patch(
        "aletheore.search_index.has_api_key", return_value=True
    ):
        mock_openai.return_value.embeddings.create.side_effect = RuntimeError("ollama down")

        with pytest.raises(EmbeddingProviderUnavailableError):
            embed_texts(["def secret(): ..."])

        # The decisive assertion: no client was ever pointed at OpenAI, so
        # nothing left the machine. Asserting only on the raised error would
        # still pass if the code sent the request and then failed.
        base_urls = [call.kwargs.get("base_url") for call in mock_openai.call_args_list]
        assert base_urls == ["http://localhost:11434/v1"]


# --- Annotations ------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_tool_declares_annotations(tmp_path):
    server = build_server(make_repo(tmp_path), allow=ALL_EFFECTS)

    unannotated = [t.name for t in await server.list_tools() if t.annotations is None]

    assert unannotated == []


@pytest.mark.asyncio
async def test_effectful_tools_are_distinguishable_from_read_only_ones(tmp_path):
    """Guards the reverse of the gate: annotations that labelled everything
    read-only would pass every registration test above while telling a client
    nothing.
    """
    server = build_server(make_repo(tmp_path), allow=ALL_EFFECTS)
    tools = {t.name: t.annotations for t in await server.list_tools()}

    # Writers must not claim to be read-only.
    for name in ("aletheore_scan", "aletheore_index", "aletheore_healthcheck"):
        assert tools[name].read_only_hint is False, f"{name} claims to be read-only"

    # Anything reaching off-process must say so. aletheore_answer is in the
    # effects map but registers only with an adapter, so intersect.
    for name in set(TOOL_REQUIRED_EFFECTS) & set(tools):
        assert tools[name].open_world_hint is True, f"{name} does not declare open-world"

    # Pure evidence queries must claim neither.
    for name in ("aletheore_imports", "aletheore_hotspots", "aletheore_symbol_source"):
        assert tools[name].read_only_hint is True
        assert tools[name].open_world_hint is False


@pytest.mark.asyncio
async def test_every_tool_is_either_gated_or_provably_inert(tmp_path):
    """Catches the next tool added without classifying it.

    A new tool that touches the filesystem or network but is neither listed
    in TOOL_REQUIRED_EFFECTS nor annotated as such would reopen the finding
    silently. Anything ungated must therefore prove it is inert: read-only
    and closed-world.
    """
    server = build_server(make_repo(tmp_path), allow=ALL_EFFECTS)

    for tool in await server.list_tools():
        if tool.name in TOOL_REQUIRED_EFFECTS:
            continue
        assert tool.annotations.read_only_hint is True, (
            f"{tool.name} is not gated but does not declare itself read-only - "
            "add it to TOOL_REQUIRED_EFFECTS or correct its annotations"
        )
        assert tool.annotations.open_world_hint is False, (
            f"{tool.name} is not gated but declares open-world access"
        )
