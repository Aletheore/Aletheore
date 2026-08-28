import json
from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest
import toon

from aletheore.adapters.openai_compatible import (
    AdapterInvocationError,
    EVIDENCE_SCHEMA_MAP,
    OpenAICompatibleAdapter,
    REQUIRED_SECTIONS,
    _get_by_dot_path,
)


def _openai_error(cls, status_code=None):
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    if cls is openai.APIConnectionError:
        return cls(request=request)
    response = httpx.Response(status_code, request=request)
    return cls("boom", response=response, body=None)


def test_get_by_dot_path_simple_key():
    assert _get_by_dot_path({"a": {"b": 1}}, "a.b") == 1


def test_get_by_dot_path_array_index():
    data = {"modules": [{"path": "a.py"}, {"path": "b.py"}]}
    assert _get_by_dot_path(data, "modules[1].path") == "b.py"


def test_get_by_dot_path_missing_returns_none():
    assert _get_by_dot_path({"a": 1}, "b.c") is None


def test_evidence_schema_map_documents_database_block():
    assert "repository.database" in EVIDENCE_SCHEMA_MAP


def test_evidence_schema_map_documents_infrastructure_and_environment_variables():
    assert "repository.infrastructure" in EVIDENCE_SCHEMA_MAP
    assert "repository.environment_variables" in EVIDENCE_SCHEMA_MAP


def test_evidence_schema_map_documents_code_evidence_resolution():
    assert "code_evidence_resolutions" in EVIDENCE_SCHEMA_MAP
    assert "file, line, symbol, owner, commit, dependency, risk" in EVIDENCE_SCHEMA_MAP


def _mock_tool_call(name, arguments, call_id="call_1"):
    tool_call = MagicMock()
    tool_call.id = call_id
    tool_call.function.name = name
    tool_call.function.arguments = json.dumps(arguments)
    return tool_call


def _mock_response(tool_calls=None, usage=(100, 20)):
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
    response.usage = (
        MagicMock(prompt_tokens=usage[0], completion_tokens=usage[1])
        if usage is not None
        else None
    )
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
def test_invoke_calls_on_usage_once_per_round_with_real_totals(mock_openai_class, tmp_path):
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    responses = _write_all_sections_then_finish_responses()
    mock_client.chat.completions.create.side_effect = responses

    usage_calls = []
    adapter = _adapter(tmp_path, on_usage=lambda p, c: usage_calls.append((p, c)))
    with patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"):
        adapter.invoke("audit this repo", cwd=str(repo))

    assert len(usage_calls) == len(responses)
    assert all(call == (100, 20) for call in usage_calls)


@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_invoke_returns_partial_report_when_budget_stops_between_rounds(
    mock_openai_class, tmp_path
):
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_client.chat.completions.create.return_value = _mock_response(
        tool_calls=[
            _mock_tool_call(
                "write_report_section",
                {"name": "Summary", "content": "partial summary"},
            )
        ]
    )
    budget_checks = iter([True, False])

    adapter = _adapter(
        tmp_path,
        before_llm_call=lambda: next(budget_checks),
        allow_partial_report=True,
    )
    with patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"):
        result = adapter.invoke("audit this repo", cwd=str(repo))

    assert "Partial report" in result
    assert "partial summary" in result
    assert "Repository Intelligence" not in result
    assert mock_client.chat.completions.create.call_count == 1


def test_simple_completion_raises_the_default_budget_message_when_before_llm_call_declines():
    adapter = OpenAICompatibleAdapter(
        name="testprovider",
        base_url="https://example.test/v1",
        api_key_env_var="TESTPROVIDER_API_KEY",
        model="test-model",
        before_llm_call=lambda: False,
    )

    with pytest.raises(AdapterInvocationError, match="the monthly LLM spend cap would be exceeded"):
        adapter.simple_completion("system", "user", cwd="/repo")


def test_simple_completion_raises_a_custom_budget_message_when_before_llm_call_declines():
    # Real regression this guards: the default message names the monthly
    # LLM spend cap, which is wrong for a caller whose before_llm_call
    # actually enforces a different budget entirely (e.g. the OpenAI
    # free-tier daily token allowance in model_tiers.py) - an ops alert for
    # a healthy daily rollover would misreport as a billing problem.
    adapter = OpenAICompatibleAdapter(
        name="testprovider",
        base_url="https://example.test/v1",
        api_key_env_var="TESTPROVIDER_API_KEY",
        model="test-model",
        before_llm_call=lambda: False,
        budget_exceeded_message="the daily free-tier token allowance would be exceeded",
    )

    with pytest.raises(AdapterInvocationError, match="the daily free-tier token allowance would be exceeded"):
        adapter.simple_completion("system", "user", cwd="/repo")

    with pytest.raises(AdapterInvocationError) as exc_info:
        adapter.simple_completion("system", "user", cwd="/repo")
    assert "monthly LLM spend cap" not in str(exc_info.value)


@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_invoke_raises_the_custom_budget_message_when_before_llm_call_declines(mock_openai_class, tmp_path):
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    adapter = _adapter(
        tmp_path,
        before_llm_call=lambda: False,
        budget_exceeded_message="the daily free-tier token allowance would be exceeded",
    )

    with patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"):
        with pytest.raises(AdapterInvocationError, match="the daily free-tier token allowance would be exceeded"):
            adapter.invoke("audit this repo", cwd=str(repo))


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


@patch("aletheore.adapters.openai_compatible.time.sleep")
@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_invoke_retries_a_transient_authentication_error_and_succeeds(
    mock_openai_class, mock_sleep, tmp_path
):
    # A real production run hit AuthenticationError twice in a row from a
    # long-lived process using a key that had already succeeded earlier
    # that morning and succeeded again minutes later with no config change
    # in between (see _RETRYABLE_EXCEPTIONS' comment) - confirming this was
    # transient, not a genuinely bad key, which would fail identically on
    # retry too.
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_client.chat.completions.create.side_effect = [
        _openai_error(openai.AuthenticationError, status_code=401),
        _openai_error(openai.AuthenticationError, status_code=401),
        *_write_all_sections_then_finish_responses(),
    ]

    adapter = _adapter(tmp_path)
    with patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"):
        result = adapter.invoke("audit this repo", cwd=str(repo))

    assert "Summary" in result
    assert mock_sleep.call_count == 2


@patch("aletheore.adapters.openai_compatible.time.sleep")
@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_invoke_gives_up_after_exhausting_retries_on_persistent_auth_error(
    mock_openai_class, mock_sleep, tmp_path
):
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_client.chat.completions.create.side_effect = _openai_error(
        openai.AuthenticationError, status_code=401
    )

    adapter = _adapter(tmp_path)
    with patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"):
        with pytest.raises(AdapterInvocationError) as exc_info:
            adapter.invoke("audit this repo", cwd=str(repo))

    assert "testprovider invocation failed: AuthenticationError" in str(exc_info.value)
    assert mock_client.chat.completions.create.call_count == 3


@patch("aletheore.adapters.openai_compatible.time.sleep")
@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_invoke_does_not_retry_a_non_transient_bad_request_error(
    mock_openai_class, mock_sleep, tmp_path
):
    # Retrying a client error that will fail identically every time (bad
    # params, content policy, etc.) just delays surfacing it - only the
    # conventional transient set (auth/rate-limit/connection/timeout/5xx)
    # is worth retrying.
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_client.chat.completions.create.side_effect = _openai_error(
        openai.BadRequestError, status_code=400
    )

    adapter = _adapter(tmp_path)
    with patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"):
        with pytest.raises(AdapterInvocationError):
            adapter.invoke("audit this repo", cwd=str(repo))

    assert mock_client.chat.completions.create.call_count == 1
    mock_sleep.assert_not_called()


@patch("aletheore.adapters.openai_compatible.time.sleep")
@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_simple_completion_retries_a_transient_rate_limit_error(mock_openai_class, mock_sleep, tmp_path):
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    success = MagicMock()
    success.choices = [MagicMock(message=MagicMock(content="ok"))]
    success.usage = None
    mock_client.chat.completions.create.side_effect = [
        _openai_error(openai.RateLimitError, status_code=429),
        success,
    ]

    adapter = _adapter(tmp_path)
    with patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"):
        result = adapter.simple_completion("system", "user", cwd=str(tmp_path))

    assert result == "ok"
    assert mock_sleep.call_count == 1


@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_simple_completion_calls_on_call_failed_when_the_call_fails(mock_openai_class, tmp_path):
    # Real regression: a caller that reserves real budget before a call
    # (before_llm_call) needs a way to release it when the call itself then
    # fails - without this, on_usage never fires (the call never completed)
    # and the reservation is stuck forever against a call that used zero
    # real tokens.
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_client.chat.completions.create.side_effect = _openai_error(
        openai.BadRequestError, status_code=400
    )

    failed_calls = []
    adapter = _adapter(tmp_path, on_call_failed=lambda: failed_calls.append(1))
    with patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"):
        with pytest.raises(AdapterInvocationError):
            adapter.simple_completion("system", "user", cwd=str(tmp_path))

    assert failed_calls == [1]


@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_simple_completion_does_not_call_on_call_failed_on_success(mock_openai_class, tmp_path):
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_message = MagicMock()
    mock_message.content = "ok"
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=mock_message)]
    mock_response.usage = None
    mock_client.chat.completions.create.return_value = mock_response

    failed_calls = []
    adapter = _adapter(tmp_path, on_call_failed=lambda: failed_calls.append(1))
    with patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"):
        adapter.simple_completion("system", "user", cwd=str(tmp_path))

    assert failed_calls == []


def test_simple_completion_does_not_call_on_call_failed_when_budget_is_declined_up_front():
    # _ensure_budget_for_next_call raises before the try block that
    # on_call_failed lives in is ever entered - before_llm_call declining is
    # a distinct, already-self-releasing path (see
    # model_tiers._reserve_openai_free_tier_budget), not a call failure.
    failed_calls = []
    adapter = OpenAICompatibleAdapter(
        name="testprovider",
        base_url="https://example.test/v1",
        api_key_env_var="TESTPROVIDER_API_KEY",
        model="test-model",
        before_llm_call=lambda: False,
        on_call_failed=lambda: failed_calls.append(1),
    )

    with pytest.raises(AdapterInvocationError):
        adapter.simple_completion("system", "user", cwd="/repo")

    assert failed_calls == []


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


def test_invoke_raises_clean_error_on_undecodable_evidence(tmp_path):
    # Confirmed bug: ToonDecodeError does not inherit from OSError, so the
    # old `except OSError` around toon.decode() let a malformed/empty
    # air.toon crash with a raw traceback instead of the intended clean
    # AdapterInvocationError.
    repo = tmp_path / "repo"
    (repo / ".aletheore").mkdir(parents=True)
    (repo / ".aletheore" / "air.toon").write_text("x[3]: not enough items")

    adapter = _adapter(tmp_path)
    with patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"):
        with pytest.raises(AdapterInvocationError, match="could not decode evidence"):
            adapter.invoke("audit this repo", cwd=str(repo))


@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_read_evidence_section_reports_encoding_failure_instead_of_crashing(
    mock_openai_class, tmp_path
):
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

    from aletheore.toon_encoding import ToonEncodingError

    def _boom(_data):
        raise ToonEncodingError("simulated failure")

    adapter = _adapter(tmp_path)
    with patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"):
        with patch("aletheore.adapters.openai_compatible.to_toon", _boom):
            adapter.invoke("audit this repo", cwd=str(repo))

    second_call = mock_client.chat.completions.create.call_args_list[1]
    tool_message = next(m for m in second_call.kwargs["messages"] if m.get("role") == "tool")
    assert "could not encode section repository.modules" in tool_message["content"]


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
    mock_response.usage = None
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
def test_simple_completion_calls_on_usage(mock_openai_class, tmp_path):
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_message = MagicMock()
    mock_message.content = "ok"
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=mock_message)]
    mock_response.usage = MagicMock(prompt_tokens=40, completion_tokens=8)
    mock_client.chat.completions.create.return_value = mock_response

    usage_calls = []
    adapter = _adapter(tmp_path, on_usage=lambda p, c: usage_calls.append((p, c)))
    with patch("aletheore.adapters.openai_compatible.get_api_key", return_value="sk-test"):
        adapter.simple_completion("system prompt", "user prompt", cwd=str(tmp_path))

    assert usage_calls == [(40, 8)]


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


@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_invoke_forces_reasoning_effort_none_for_openai_provider(mock_openai_class, tmp_path):
    # Regression: gpt-5.6-luna rejects function tools on /v1/chat/completions
    # unless reasoning_effort is explicitly "none" - confirmed directly
    # against the real API (tools+tool_choice="required" only succeeds with
    # this present). extra_body defaults to {} (model_tiers._reasoning_body
    # only returns a real value behind the opt-in AIRVIEW_REASONING=off
    # toggle), so this can't rely on the caller having configured extra_body -
    # invoke() must force it unconditionally for the OpenAI-named provider,
    # since it's the only place that ever sends tools.
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_client.chat.completions.create.side_effect = _write_all_sections_then_finish_responses()

    adapter = _adapter(tmp_path, name="OpenAI", needs_key=False)
    adapter.invoke("audit this repo", cwd=str(repo))

    first_call = mock_client.chat.completions.create.call_args_list[0]
    assert first_call.kwargs["extra_body"] == {"reasoning_effort": "none"}


@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_invoke_merges_reasoning_effort_with_existing_extra_body_for_openai(mock_openai_class, tmp_path):
    # If a caller already configured extra_body (e.g. AIRVIEW_REASONING=off
    # threading the same value through), the forced override must not
    # clobber other keys that might be present alongside it.
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_client.chat.completions.create.side_effect = _write_all_sections_then_finish_responses()

    adapter = _adapter(
        tmp_path, name="OpenAI", needs_key=False, extra_body={"reasoning_effort": "none", "other_flag": True}
    )
    adapter.invoke("audit this repo", cwd=str(repo))

    first_call = mock_client.chat.completions.create.call_args_list[0]
    assert first_call.kwargs["extra_body"] == {"reasoning_effort": "none", "other_flag": True}


@patch("aletheore.adapters.openai_compatible.OpenAI")
def test_invoke_does_not_force_reasoning_effort_for_non_openai_provider(mock_openai_class, tmp_path):
    # DeepSeek uses a different disabled-reasoning shape entirely
    # ({"thinking": {"type": "disabled"}}, not reasoning_effort) and has no
    # documented requirement that tools always need it disabled - forcing
    # an OpenAI-specific field onto every provider would be wrong, not just
    # unnecessary.
    repo = _make_repo_with_evidence(tmp_path, {"repository": {"modules": []}})
    mock_client = MagicMock()
    mock_openai_class.return_value = mock_client
    mock_client.chat.completions.create.side_effect = _write_all_sections_then_finish_responses()

    adapter = _adapter(tmp_path, name="DeepSeek", needs_key=False)
    adapter.invoke("audit this repo", cwd=str(repo))

    first_call = mock_client.chat.completions.create.call_args_list[0]
    assert "extra_body" not in first_call.kwargs
