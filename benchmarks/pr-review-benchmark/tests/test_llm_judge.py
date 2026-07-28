import json
import pytest
from scripts.llm_judge import build_judge_prompt, parse_judge_response, call_judge_model


def test_build_judge_prompt_includes_ground_truth_and_findings():
    ground_truth = {"category": "injected_bug", "expected_file": "x.py", "expected_line": 5}
    findings_by_tool = {"aletheore": [{"file": "x.py", "line": 5, "message": "bug here"}]}
    prompt = build_judge_prompt(ground_truth, findings_by_tool)
    assert "injected_bug" in prompt
    assert "aletheore" in prompt
    assert "bug here" in prompt
    assert "recall" in prompt


def test_parse_judge_response_extracts_json_object():
    response_text = (
        "Here is my scoring:\n"
        '{"Tool A": {"recall": "hit", "false_positives": [], "actionability": 4}}'
    )
    result = parse_judge_response(response_text)
    assert result == {"Tool A": {"recall": "hit", "false_positives": [], "actionability": 4}}


def test_parse_judge_response_rejects_invalid_recall_value():
    response_text = '{"Tool A": {"recall": "maybe", "false_positives": [], "actionability": 3}}'
    with pytest.raises(ValueError, match="recall must be hit/partial/miss"):
        parse_judge_response(response_text)


def test_parse_judge_response_raises_when_no_json_present():
    with pytest.raises(ValueError, match="did not contain a JSON object"):
        parse_judge_response("I refuse to answer in JSON.")


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, text):
        self._text = text
        self.last_call = None

    def create(self, **kwargs):
        self.last_call = kwargs
        return _FakeResponse(self._text)


class _FakeChat:
    def __init__(self, text):
        self.completions = _FakeCompletions(text)


class _FakeClient:
    def __init__(self, text):
        self.chat = _FakeChat(text)


def test_call_judge_model_passes_prompt_and_model_and_returns_text():
    client = _FakeClient("mocked response text")
    result = call_judge_model(client, "the prompt", model="gpt-judge-1")
    assert result == "mocked response text"
    assert client.chat.completions.last_call["model"] == "gpt-judge-1"
    assert client.chat.completions.last_call["messages"] == [
        {"role": "user", "content": "the prompt"}
    ]
