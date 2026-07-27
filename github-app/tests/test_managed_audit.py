import json
from pathlib import Path

from scan_worker.managed_audit import _llm_based_suggestion_section, run_managed_audit


def test_run_managed_audit_returns_report_text(tmp_path, monkeypatch):
    repo_path = tmp_path / "repo"
    (repo_path / ".aletheore").mkdir(parents=True)
    (repo_path / ".aletheore" / "air.toon").write_text("fake toon evidence")

    captured_adapters = []

    def fake_run_reasoning_phase(adapter, repo_path_arg, manual_dir):
        captured_adapters.append(adapter)
        report_path = Path(repo_path_arg) / ".aletheore" / "audit-report.md"
        report_path.write_text("# Real Report\n\nfindings here")
        return str(report_path)

    monkeypatch.setattr("scan_worker.managed_audit.run_reasoning_phase", fake_run_reasoning_phase)

    assert "Real Report" in run_managed_audit(repo_path, plan="air")

    adapter = captured_adapters[0]
    assert adapter.name == "DeepSeek"
    assert adapter._base_url == "https://api.deepseek.com"
    assert adapter._api_key_env_var == "DEEPSEEK_API_KEY"
    assert adapter._model == "deepseek-v4-pro"
    assert adapter._supports_tool_choice is False


def test_run_managed_audit_threads_on_usage_to_the_adapter(tmp_path, monkeypatch):
    repo_path = tmp_path / "repo"
    (repo_path / ".aletheore").mkdir(parents=True)
    (repo_path / ".aletheore" / "air.toon").write_text("fake toon evidence")

    captured_adapters = []

    def fake_run_reasoning_phase(adapter, repo_path_arg, manual_dir):
        captured_adapters.append(adapter)
        report_path = Path(repo_path_arg) / ".aletheore" / "audit-report.md"
        report_path.write_text("# Real Report\n\nfindings here")
        return str(report_path)

    monkeypatch.setattr("scan_worker.managed_audit.run_reasoning_phase", fake_run_reasoning_phase)

    received = []
    run_managed_audit(repo_path, plan="air", on_usage=lambda p, c: received.append((p, c)))

    adapter = captured_adapters[0]
    assert adapter._on_usage is not None
    adapter._on_usage(50, 10)
    assert received == [(50, 10)]


class _FakeSuggestionAdapter:
    def __init__(self, response: str):
        self._response = response

    def simple_completion(self, system_prompt, user_prompt, cwd):
        return self._response


def test_llm_based_suggestion_section_formats_rating_and_suggestions(monkeypatch):
    response = json.dumps(
        {
            "rating": 7,
            "rating_justification": "solid tests, thin error handling",
            "suggestions": ["Add retries around the payment webhook call", "Extract the 300-line handler"],
        }
    )
    monkeypatch.setattr(
        "scan_worker.managed_audit.writing_adapter_for_plan",
        lambda plan, on_usage=None: _FakeSuggestionAdapter(response),
    )

    section = _llm_based_suggestion_section("# Real Report\n\nfindings", "pro")

    assert section is not None
    assert "LLM Based Suggestion (Not Evidence Backed)" in section
    assert "7/10" in section
    assert "solid tests, thin error handling" in section
    assert "Add retries around the payment webhook call" in section
    assert "Extract the 300-line handler" in section


def test_llm_based_suggestion_section_returns_none_for_malformed_json(monkeypatch):
    monkeypatch.setattr(
        "scan_worker.managed_audit.writing_adapter_for_plan",
        lambda plan, on_usage=None: _FakeSuggestionAdapter("not json at all"),
    )

    assert _llm_based_suggestion_section("report", "pro") is None


def test_llm_based_suggestion_section_returns_none_for_out_of_range_rating(monkeypatch):
    response = json.dumps({"rating": 15, "rating_justification": "x", "suggestions": ["do a thing"]})
    monkeypatch.setattr(
        "scan_worker.managed_audit.writing_adapter_for_plan",
        lambda plan, on_usage=None: _FakeSuggestionAdapter(response),
    )

    assert _llm_based_suggestion_section("report", "pro") is None


def test_llm_based_suggestion_section_returns_none_when_suggestions_empty(monkeypatch):
    response = json.dumps({"rating": 8, "rating_justification": "x", "suggestions": []})
    monkeypatch.setattr(
        "scan_worker.managed_audit.writing_adapter_for_plan",
        lambda plan, on_usage=None: _FakeSuggestionAdapter(response),
    )

    assert _llm_based_suggestion_section("report", "pro") is None


def test_llm_based_suggestion_section_degrades_gracefully_on_adapter_failure(monkeypatch):
    def _raise(plan, on_usage=None):
        raise RuntimeError("no credentials")

    monkeypatch.setattr("scan_worker.managed_audit.writing_adapter_for_plan", _raise)

    assert _llm_based_suggestion_section("report", "pro") is None


def test_run_managed_audit_appends_llm_suggestion_section_when_available(tmp_path, monkeypatch):
    repo_path = tmp_path / "repo"
    (repo_path / ".aletheore").mkdir(parents=True)

    def fake_run_reasoning_phase(adapter, repo_path_arg, manual_dir):
        report_path = Path(repo_path_arg) / ".aletheore" / "audit-report.md"
        report_path.write_text("# Real Report\n\nfindings here")
        return str(report_path)

    monkeypatch.setattr("scan_worker.managed_audit.run_reasoning_phase", fake_run_reasoning_phase)
    response = json.dumps({"rating": 6, "rating_justification": "ok", "suggestions": ["tighten input validation"]})
    monkeypatch.setattr(
        "scan_worker.managed_audit.writing_adapter_for_plan",
        lambda plan, on_usage=None: _FakeSuggestionAdapter(response),
    )

    result = run_managed_audit(repo_path, plan="air")

    assert "# Real Report" in result
    assert "LLM Based Suggestion (Not Evidence Backed)" in result
    assert "tighten input validation" in result
