import json
from unittest.mock import MagicMock, patch

import pytest
import toon

from aletheore.adapters.openai_compatible import (
    AdapterInvocationError,
    OpenAICompatibleAdapter,
    REQUIRED_SECTIONS,
    _get_by_dot_path,
)


def test_get_by_dot_path_simple_key():
    assert _get_by_dot_path({"a": {"b": 1}}, "a.b") == 1


def test_get_by_dot_path_array_index():
    data = {"modules": [{"path": "a.py"}, {"path": "b.py"}]}
    assert _get_by_dot_path(data, "modules[1].path") == "b.py"


def test_get_by_dot_path_missing_returns_none():
    assert _get_by_dot_path({"a": 1}, "b.c") is None


def _mock_tool_call(name, arguments, call_id="call_1"):
    tool_call = MagicMock()
    tool_call.id = call_id
    tool_call.function.name = name
    tool_call.function.arguments = json.dumps(arguments)
    return tool_call


def _mock_response(tool_calls=None):
    message = MagicMock()
    message.tool_calls = tool_calls
    message.model_dump.return_value = {
        "role": "assistant",
        "content": None,
        "tool_calls": (
            [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in tool_calls
            ]
            if tool_calls
            else None
        ),
    }
    response = MagicMock()
    response.choices = [MagicMock(message=message)]
    return response


def _make_repo_with_evidence(tmp_path, evidence: dict):
    repo = tmp_path / "repo"
    (repo / ".aletheore").mkdir(parents=True)
    (repo / ".aletheore" / "air.toon").write_text(toon.encode(evidence))
    return repo


def _write_all_sections_then_finish_responses():
    responses = [
        _mock_response(
            tool_calls=[
                _mock_tool_call(
                    "write_report_section",
                    {"name": section, "content": f"content for {section}"},
                    call_id=f"call_{i}",
                )
            ]
        )
        for i, section in enumerate(REQUIRED_SECTIONS)
    ]
    responses.append(
        _mock_response(tool_calls=[_mock_tool_call("finish_report", {}, call_id="call_finish")])
    )
    return responses


def _adapter(tmp_path, **overrides):
    kwargs = dict(
        name="testprovider",
        base_url="https://example.test/v1",
        api_key_env_var="TESTPROVIDER_API_KEY",
        model="test-model",
        credentials_path=tmp_path / "creds.json",
    )
    kwargs.update(overrides)
    return OpenAICompatibleAdapter(**kwargs)


@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_invoke_assembles_all_required_sections_in_order(mock_openai_class, tmp_path):
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_client.chat.completions.create.side_effect = _write_all_sections_then_finish_responses()

    adapter = _adapter(tmp_path)
    with patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"):
        result = adapter.invoke("audit this repo", cwd=str(repo))

    for section in REQUIRED_SECTIONS:
        assert f"## {section}" in result
        assert f"content for {section}" in result
    assert result.index("## Summary") < result.index("## Repository Intelligence")
    assert result.index("## Evidence Gaps") < result.index("## Roadmap")

    first_call = mock_client.chat.completions.create.call_args_list[0]
    assert first_call.kwargs["tool_choice"] == "required"


@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_invoke_raises_if_finish_called_before_all_sections_written(mock_openai_class, tmp_path):
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_client.chat.completions.create.side_effect = [
        _mock_response(
            tool_calls=[_mock_tool_call("write_report_section", {"name": "Summary", "content": "x"})]
        ),
        _mock_response(tool_calls=[_mock_tool_call("finish_report", {})]),
    ]

    adapter = _adapter(tmp_path)
    with patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"):
        with pytest.raises(AdapterInvocationError, match="without writing required section"):
            adapter.invoke("audit this repo", cwd=str(repo))


@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_invoke_raises_if_never_finishes_within_max_rounds(mock_openai_class, tmp_path):
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_client.chat.completions.create.return_value = _mock_response(
        tool_calls=[_mock_tool_call("read_evidence_section", {"path": "repository.modules"})]
    )

    adapter = _adapter(tmp_path)
    with patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"):
        with pytest.raises(AdapterInvocationError, match="did not finish"):
            adapter.invoke("audit this repo", cwd=str(repo))


@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_invoke_normalizes_provider_errors_without_leaking_details(mock_openai_class, tmp_path):
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_client.chat.completions.create.side_effect = RuntimeError("secret detail")

    adapter = _adapter(tmp_path)
    with patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"):
        with pytest.raises(AdapterInvocationError) as exc_info:
            adapter.invoke("audit this repo", cwd=str(repo))

    message = str(exc_info.value)
    assert "testprovider invocation failed: RuntimeError" in message
    assert "secret detail" not in message


@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_read_evidence_section_tool_returns_wrapped_data(mock_openai_class, tmp_path):
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": [{"path": "app.py"}]}})
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    responses = [
        _mock_response(
            tool_calls=[_mock_tool_call("read_evidence_section", {"path": "repository.modules"})]
        )
    ]
    responses += _write_all_sections_then_finish_responses()
    mock_client.chat.completions.create.side_effect = responses

    adapter = _adapter(tmp_path)
    with patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"):
        adapter.invoke("audit this repo", cwd=str(repo))

    second_call = mock_client.chat.completions.create.call_args_list[1]
    messages = second_call.kwargs["messages"]
    tool_message = next(m for m in messages if m.get("role") == "tool")
    assert '<evidence path="repository.modules">' in tool_message["content"]
    assert "app.py" in tool_message["content"]
    assert "</evidence>" in tool_message["content"]


@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_read_evidence_section_missing_path_reports_clearly(mock_openai_class, tmp_path):
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    responses = [
        _mock_response(
            tool_calls=[_mock_tool_call("read_evidence_section", {"path": "does.not.exist"})]
        )
    ]
    responses += _write_all_sections_then_finish_responses()
    mock_client.chat.completions.create.side_effect = responses

    adapter = _adapter(tmp_path)
    with patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"):
        adapter.invoke("audit this repo", cwd=str(repo))

    second_call = mock_client.chat.completions.create.call_args_list[1]
    tool_message = next(m for m in second_call.kwargs["messages"] if m.get("role") == "tool")
    assert "no such path: does.not.exist" in tool_message["content"]


def test_is_available_checks_api_key_for_key_based_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("TESTPROVIDER_API_KEY", "sk-abc")
    adapter = _adapter(tmp_path)
    assert adapter.is_available() is True


def test_is_available_false_when_key_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("TESTPROVIDER_API_KEY", raising=False)
    adapter = _adapter(tmp_path)
    assert adapter.is_available() is False


@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_invoke_fails_fast_after_consecutive_no_tool_call_rounds(mock_openai_class, tmp_path):
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_client.chat.completions.create.return_value = _mock_response(tool_calls=None)

    adapter = _adapter(tmp_path)
    with patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"):
        with pytest.raises(AdapterInvocationError, match="stopped calling tools"):
            adapter.invoke("audit this repo", cwd=str(repo))

    # must fail fast (after 2 rounds), not burn through all 20 MAX_TOOL_ROUNDS
    assert mock_client.chat.completions.create.call_count == 2


@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_invoke_recovers_after_single_no_tool_call_round(mock_openai_class, tmp_path):
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    responses = [_mock_response(tool_calls=None)] + _write_all_sections_then_finish_responses()
    mock_client.chat.completions.create.side_effect = responses

    adapter = _adapter(tmp_path)
    with patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"):
        result = adapter.invoke("audit this repo", cwd=str(repo))

    for section in REQUIRED_SECTIONS:
        assert f"## {section}" in result

    # the round after the no-tool-call response must include a corrective nudge
    second_call = mock_client.chat.completions.create.call_args_list[1]
    nudge_messages = [
        m for m in second_call.kwargs["messages"]
        if m.get("role") == "user" and "must call exactly one of the provided tools" in m.get("content", "")
    ]
    assert len(nudge_messages) == 1


def test_ollama_style_adapter_does_not_need_key(tmp_path):
    adapter = _adapter(
        tmp_path,
        name="ollama",
        base_url="http://localhost:11434/v1",
        api_key_env_var="",
        needs_key=False,
        requires_consent=False,
    )
    assert adapter.requires_consent is False
    with patch.object(adapter, "_local_server_reachable", return_value=True):
        assert adapter.is_available() is True
    with patch.object(adapter, "_local_server_reachable", return_value=False):
        assert adapter.is_available() is False


@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_simple_completion_makes_one_plain_completion_call(mock_openai_class, tmp_path):
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_message = MagicMock()
    mock_message.content = "a short cited answer"
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=mock_message)]
    mock_client.chat.completions.create.return_value = mock_response

    adapter = _adapter(tmp_path)
    with patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"):
        result = adapter.simple_completion("system text", "user text", cwd="/repo")

    assert result == "a short cited answer"
    call = mock_client.chat.completions.create.call_args
    assert call.kwargs["messages"] == [
        {"role": "system", "content": "system text"},
        {"role": "user", "content": "user text"},
    ]
    assert "tools" not in call.kwargs


@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_default_request_timeout_matches_module_constant(mock_openai_class, tmp_path):
    from aletheore.adapters.openai_compatible import REQUEST_TIMEOUT_SECONDS

    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_client.chat.completions.create.side_effect = _write_all_sections_then_finish_responses()

    adapter = _adapter(tmp_path)
    with patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"):
        adapter.invoke("audit this repo", cwd=str(repo))

    first_call = mock_client.chat.completions.create.call_args_list[0]
    assert first_call.kwargs["timeout"] == REQUEST_TIMEOUT_SECONDS


@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_custom_request_timeout_is_threaded_through(mock_openai_class, tmp_path):
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_client.chat.completions.create.side_effect = _write_all_sections_then_finish_responses()

    adapter = _adapter(tmp_path, request_timeout_seconds=400)
    with patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"):
        adapter.invoke("audit this repo", cwd=str(repo))

    first_call = mock_client.chat.completions.create.call_args_list[0]
    assert first_call.kwargs["timeout"] == 400


@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_supports_tool_choice_false_omits_tool_choice_from_request(mock_openai_class, tmp_path):
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_client.chat.completions.create.side_effect = _write_all_sections_then_finish_responses()

    adapter = _adapter(tmp_path, needs_key=False, supports_tool_choice=False)
    adapter.invoke("audit this repo", cwd=str(repo))

    first_call = mock_client.chat.completions.create.call_args_list[0]
    assert "tool_choice" not in first_call.kwargs


@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_supports_tool_choice_true_by_default(mock_openai_class, tmp_path):
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_client.chat.completions.create.side_effect = _write_all_sections_then_finish_responses()

    adapter = _adapter(tmp_path)
    with patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"):
        adapter.invoke("audit this repo", cwd=str(repo))

    first_call = mock_client.chat.completions.create.call_args_list[0]
    assert first_call.kwargs["tool_choice"] == "required"
