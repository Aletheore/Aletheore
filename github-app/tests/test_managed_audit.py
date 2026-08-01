import json
from pathlib import Path

from aletheore.citation_verifier import load_verifiable_evidence as _load_verifiable_evidence
from aletheore.citation_verifier import local_line_count_fetcher as _local_line_count_fetcher
from scan_worker.managed_audit import (
    _citation_verification_section,
    _llm_based_suggestion_section,
    run_managed_audit,
)


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


def _repo_with_evidence(tmp_path, files: dict[str, str]) -> Path:
    """A checkout that has both real source files and a real air.json
    naming them - i.e. what run_managed_audit_pr_job actually hands to
    run_managed_audit after _run_scan."""
    repo_path = tmp_path / "repo"
    (repo_path / ".aletheore").mkdir(parents=True)
    for path, content in files.items():
        full = repo_path / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    evidence = {"repository": {"modules": [{"path": p} for p in files]}}
    (repo_path / ".aletheore" / "air.json").write_text(json.dumps(evidence))
    return repo_path


def test_local_line_count_fetcher_counts_real_lines(tmp_path):
    repo_path = _repo_with_evidence(tmp_path, {"app.py": "one\ntwo\nthree\n"})

    assert _local_line_count_fetcher(repo_path)("app.py") == 3


def test_local_line_count_fetcher_returns_none_for_missing_and_escaping_paths(tmp_path):
    repo_path = _repo_with_evidence(tmp_path, {"app.py": "one\n"})
    fetch = _local_line_count_fetcher(repo_path)

    assert fetch("nope.py") is None
    # A path escape must never be read, and must degrade to "skip the bounds
    # check" rather than to a false "unverified".
    assert fetch("../../../../etc/passwd") is None


def test_load_verifiable_evidence_rejects_the_managed_evidence_placeholder(tmp_path):
    # run_managed_audit_api_job writes this stub when the caller supplies a
    # TOON blob; verifying against it would mark every citation unverified.
    repo_path = tmp_path / "repo"
    (repo_path / ".aletheore").mkdir(parents=True)
    (repo_path / ".aletheore" / "air.json").write_text(json.dumps({"managed_evidence": True}))

    assert _load_verifiable_evidence(repo_path) is None


def test_citation_verification_section_reports_all_verified(tmp_path):
    repo_path = _repo_with_evidence(tmp_path, {"app.py": "one\ntwo\nthree\n"})

    section = _citation_verification_section("The bug is at `app.py:2`.", repo_path)

    assert "Citation Verification" in section
    assert "1 of 1" in section
    assert "could not be verified" not in section


def test_citation_verification_section_flags_a_file_not_in_the_evidence(tmp_path):
    repo_path = _repo_with_evidence(tmp_path, {"app.py": "one\ntwo\n"})

    section = _citation_verification_section("See `ghost.py:1` for details.", repo_path)

    assert "0 of 1" in section
    assert "1 citation(s) could not be verified" in section
    assert "`ghost.py:1`" in section


def test_citation_verification_section_flags_a_line_past_the_end_of_the_file(tmp_path):
    # The exact failure class this whole check exists for: a real file, a
    # fabricated line number.
    repo_path = _repo_with_evidence(tmp_path, {"app.py": "one\ntwo\nthree\n"})

    section = _citation_verification_section("The bug is at `app.py:900`.", repo_path)

    assert "0 of 1" in section
    assert "`app.py:900`" in section


def test_citation_verification_section_is_unavailable_without_a_file_inventory(tmp_path):
    repo_path = tmp_path / "repo"
    (repo_path / ".aletheore").mkdir(parents=True)
    (repo_path / ".aletheore" / "air.json").write_text(json.dumps({"managed_evidence": True}))

    section = _citation_verification_section("See `app.py:2`.", repo_path)

    assert "Not available for this run" in section
    # Must NOT claim the citation failed - we simply could not check it.
    assert "could not be verified" not in section


def test_citation_verification_section_does_not_claim_bounds_checking_it_could_not_do(tmp_path):
    # The API job writes evidence but no source files, so no line count is
    # ever readable. Claiming lines were bounds-checked there would be the
    # exact overclaim this section exists to prevent.
    repo_path = tmp_path / "repo"
    (repo_path / ".aletheore").mkdir(parents=True)
    evidence = {"repository": {"modules": [{"path": "app.py"}]}}
    (repo_path / ".aletheore" / "air.json").write_text(json.dumps(evidence))

    section = _citation_verification_section("The bug is at `app.py:900`.", repo_path)

    assert "1 of 1" in section
    assert "could not be bounds-checked" in section
    assert "within that file's real length" not in section


def test_citation_verification_section_handles_a_report_with_no_citations(tmp_path):
    repo_path = _repo_with_evidence(tmp_path, {"app.py": "one\n"})

    section = _citation_verification_section("No specific locations named.", repo_path)

    assert "no `file:line` citations to verify" in section


def test_run_managed_audit_embeds_citation_verification_in_the_signed_report(tmp_path, monkeypatch):
    # The verification result must be part of report_text itself: jobs.py
    # signs exactly this string, so anything not in here isn't covered by
    # the Audit Certificate's signature.
    repo_path = _repo_with_evidence(tmp_path, {"app.py": "one\ntwo\nthree\n"})

    def fake_run_reasoning_phase(adapter, repo_path_arg, manual_dir):
        report_path = Path(repo_path_arg) / ".aletheore" / "audit-report.md"
        report_path.write_text("# Real Report\n\nThe bug is at `app.py:2`.")
        return str(report_path)

    monkeypatch.setattr("scan_worker.managed_audit.run_reasoning_phase", fake_run_reasoning_phase)
    monkeypatch.setattr(
        "scan_worker.managed_audit.writing_adapter_for_plan",
        lambda plan, on_usage=None: _FakeSuggestionAdapter("not json"),
    )

    result = run_managed_audit(repo_path, plan="air")

    assert "# Real Report" in result
    assert "## Citation Verification" in result
    assert "1 of 1" in result


def test_run_managed_audit_does_not_count_citations_in_the_non_evidence_backed_section(
    tmp_path, monkeypatch
):
    # The LLM suggestion section is explicitly allowed to speak without
    # citations, so counting its text would measure the wrong thing - and a
    # bogus citation invented there must not drag down the audit's own
    # verification result.
    repo_path = _repo_with_evidence(tmp_path, {"app.py": "one\ntwo\nthree\n"})

    def fake_run_reasoning_phase(adapter, repo_path_arg, manual_dir):
        report_path = Path(repo_path_arg) / ".aletheore" / "audit-report.md"
        report_path.write_text("# Real Report\n\nThe bug is at `app.py:2`.")
        return str(report_path)

    monkeypatch.setattr("scan_worker.managed_audit.run_reasoning_phase", fake_run_reasoning_phase)
    response = json.dumps(
        {
            "rating": 6,
            "rating_justification": "ok",
            "suggestions": ["Also look at `imaginary.py:4242`"],
        }
    )
    monkeypatch.setattr(
        "scan_worker.managed_audit.writing_adapter_for_plan",
        lambda plan, on_usage=None: _FakeSuggestionAdapter(response),
    )

    result = run_managed_audit(repo_path, plan="air")

    assert "1 of 1" in result
    assert "could not be verified" not in result
    assert "imaginary.py:4242" in result


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
