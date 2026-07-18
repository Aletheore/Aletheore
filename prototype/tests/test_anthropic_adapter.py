from unittest.mock import MagicMock, patch

import pytest
import toon

from aletheore.adapters.anthropic_native import AnthropicAdapter
from aletheore.adapters.base import AdapterInvocationError
from aletheore.adapters.openai_compatible import REQUIRED_SECTIONS


def _tool_use_block(name, input_dict, block_id="toolu_1"):
    block = MagicMock()
    block.type = "tool_use"
    block.id = block_id
    block.name = name
    block.input = input_dict
    return block


def _make_repo_with_evidence(tmp_path, evidence: dict):
    repo = tmp_path / "repo"
    (repo / ".aletheore").mkdir(parents=True)
    (repo / ".aletheore" / "air.toon").write_text(toon.encode(evidence))
    return repo


def _write_all_sections_then_finish_responses():
    responses = []
    for i, section in enumerate(REQUIRED_SECTIONS):
        response = MagicMock()
        response.content = [
            _tool_use_block(
                "write_report_section",
                {"name": section, "content": f"content for {section}"},
                block_id=f"toolu_{i}",
            )
        ]
        responses.append(response)
    finish_response = MagicMock()
    finish_response.content = [_tool_use_block("finish_report", {}, block_id="toolu_finish")]
    responses.append(finish_response)
    return responses


def _adapter(tmp_path, **overrides):
    kwargs = dict(model="test-model", credentials_path=tmp_path / "creds.json")
    kwargs.update(overrides)
    return AnthropicAdapter(**kwargs)


@patch("aletheore.adapters.anthropic_native.Anthropic")
def test_invoke_assembles_all_required_sections_in_order(mock_anthropic_class, tmp_path):
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    mock_client = MagicMock()
    mock_anthropic_class.return_value = mock_client
    mock_client.messages.create.side_effect = _write_all_sections_then_finish_responses()

    adapter = _adapter(tmp_path)
    with patch("aletheore.adapters.anthropic_native.get_api_key", return_value="sk-ant-test"):
        result = adapter.invoke("audit this repo", cwd=str(repo))

    for section in REQUIRED_SECTIONS:
        assert f"## {section}" in result
        assert f"content for {section}" in result
    assert result.index("## Summary") < result.index("## Roadmap")

    first_call = mock_client.messages.create.call_args_list[0]
    assert first_call.kwargs["tool_choice"] == {"type": "any"}


@patch("aletheore.adapters.anthropic_native.Anthropic")
def test_invoke_raises_if_finish_called_before_all_sections_written(
    mock_anthropic_class, tmp_path
):
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    mock_client = MagicMock()
    mock_anthropic_class.return_value = mock_client
    first = MagicMock()
    first.content = [_tool_use_block("write_report_section", {"name": "Summary", "content": "x"})]
    second = MagicMock()
    second.content = [_tool_use_block("finish_report", {})]
    mock_client.messages.create.side_effect = [first, second]

    adapter = _adapter(tmp_path)
    with patch("aletheore.adapters.anthropic_native.get_api_key", return_value="sk-ant-test"):
        with pytest.raises(AdapterInvocationError, match="without writing required section"):
            adapter.invoke("audit this repo", cwd=str(repo))


@patch("aletheore.adapters.anthropic_native.Anthropic")
def test_read_evidence_section_tool_returns_wrapped_data(mock_anthropic_class, tmp_path):
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": [{"path": "app.py"}]}})
    mock_client = MagicMock()
    mock_anthropic_class.return_value = mock_client
    read_response = MagicMock()
    read_response.content = [
        _tool_use_block("read_evidence_section", {"path": "repository.modules"})
    ]
    responses = [read_response] + _write_all_sections_then_finish_responses()
    mock_client.messages.create.side_effect = responses

    adapter = _adapter(tmp_path)
    with patch("aletheore.adapters.anthropic_native.get_api_key", return_value="sk-ant-test"):
        adapter.invoke("audit this repo", cwd=str(repo))

    second_call = mock_client.messages.create.call_args_list[1]
    messages = second_call.kwargs["messages"]
    tool_results = [
        content
        for message in messages
        if message["role"] == "user" and isinstance(message["content"], list)
        for content in message["content"]
        if content["type"] == "tool_result"
    ]
    tool_result_content = next(
        result for result in tool_results if "repository.modules" in result["content"]
    )
    assert tool_result_content["type"] == "tool_result"
    assert '<evidence path="repository.modules">' in tool_result_content["content"]
    assert "app.py" in tool_result_content["content"]


@patch("aletheore.adapters.anthropic_native.Anthropic")
def test_invoke_raises_if_never_finishes_within_max_rounds(mock_anthropic_class, tmp_path):
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    mock_client = MagicMock()
    mock_anthropic_class.return_value = mock_client
    looping_response = MagicMock()
    looping_response.content = [
        _tool_use_block("read_evidence_section", {"path": "repository.modules"})
    ]
    mock_client.messages.create.return_value = looping_response

    adapter = _adapter(tmp_path)
    with patch("aletheore.adapters.anthropic_native.get_api_key", return_value="sk-ant-test"):
        with pytest.raises(AdapterInvocationError, match="did not finish"):
            adapter.invoke("audit this repo", cwd=str(repo))


@patch("aletheore.adapters.anthropic_native.Anthropic")
def test_invoke_normalizes_provider_errors_without_leaking_details(
    mock_anthropic_class, tmp_path
):
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    mock_client = MagicMock()
    mock_anthropic_class.return_value = mock_client
    mock_client.messages.create.side_effect = RuntimeError("secret detail")

    adapter = _adapter(tmp_path)
    with patch("aletheore.adapters.anthropic_native.get_api_key", return_value="sk-ant-test"):
        with pytest.raises(AdapterInvocationError) as exc_info:
            adapter.invoke("audit this repo", cwd=str(repo))

    message = str(exc_info.value)
    assert "anthropic invocation failed: RuntimeError" in message
    assert "secret detail" not in message


@patch("aletheore.adapters.anthropic_native.Anthropic")
def test_invoke_fails_fast_after_consecutive_no_tool_call_rounds(mock_anthropic_class, tmp_path):
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    mock_client = MagicMock()
    mock_anthropic_class.return_value = mock_client
    no_tool_response = MagicMock()
    no_tool_response.content = []
    mock_client.messages.create.return_value = no_tool_response

    adapter = _adapter(tmp_path)
    with patch("aletheore.adapters.anthropic_native.get_api_key", return_value="sk-ant-test"):
        with pytest.raises(AdapterInvocationError, match="stopped calling tools"):
            adapter.invoke("audit this repo", cwd=str(repo))

    assert mock_client.messages.create.call_count == 2


@patch("aletheore.adapters.anthropic_native.Anthropic")
def test_invoke_recovers_after_single_no_tool_call_round(mock_anthropic_class, tmp_path):
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    mock_client = MagicMock()
    mock_anthropic_class.return_value = mock_client
    no_tool_response = MagicMock()
    no_tool_response.content = []
    responses = [no_tool_response] + _write_all_sections_then_finish_responses()
    mock_client.messages.create.side_effect = responses

    adapter = _adapter(tmp_path)
    with patch("aletheore.adapters.anthropic_native.get_api_key", return_value="sk-ant-test"):
        result = adapter.invoke("audit this repo", cwd=str(repo))

    for section in REQUIRED_SECTIONS:
        assert f"## {section}" in result

    second_call = mock_client.messages.create.call_args_list[1]
    nudge_messages = [
        m for m in second_call.kwargs["messages"]
        if m.get("role") == "user" and isinstance(m.get("content"), str)
        and "must call exactly one of the provided tools" in m["content"]
    ]
    assert len(nudge_messages) == 1


def test_is_available_true_with_api_key(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-abc")
    assert _adapter(tmp_path).is_available() is True


def test_is_available_false_without_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert _adapter(tmp_path).is_available() is False


def test_name_and_requires_consent():
    adapter = AnthropicAdapter()
    assert adapter.name == "anthropic"
    assert adapter.requires_consent is True


@patch("aletheore.adapters.anthropic_native.Anthropic")
def test_simple_completion_makes_one_plain_completion_call(mock_anthropic_class, tmp_path):
    mock_client = MagicMock()
    mock_anthropic_class.return_value = mock_client
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "a short cited answer"
    mock_response = MagicMock()
    mock_response.content = [text_block]
    mock_client.messages.create.return_value = mock_response

    adapter = _adapter(tmp_path)
    with patch("aletheore.adapters.anthropic_native.get_api_key", return_value="sk-ant-test"):
        result = adapter.simple_completion("system text", "user text", cwd="/repo")

    assert result == "a short cited answer"
    call = mock_client.messages.create.call_args
    assert call.kwargs["system"] == "system text"
    assert call.kwargs["messages"] == [{"role": "user", "content": "user text"}]
    assert "tools" not in call.kwargs
