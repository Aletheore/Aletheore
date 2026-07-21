from unittest.mock import MagicMock, patch

from scan_worker.flash_review import build_code_evidence_context, review_diff


def test_review_diff_returns_empty_list_for_empty_diff():
    assert review_diff("") == []
    assert review_diff("   \n  ") == []


@patch("scan_worker.flash_review.OpenAICompatibleAdapter")
def test_review_diff_parses_valid_findings(mock_adapter_class):
    mock_adapter = MagicMock()
    mock_adapter.simple_completion.return_value = (
        '[{"file": "app.py", "line": 42, "issue": "unclosed file handle, never calls .close()"}]'
    )
    mock_adapter_class.return_value = mock_adapter

    findings = review_diff("--- app.py ---\n@@ ... @@\n+f = open('x')")

    assert findings == [
        {"file": "app.py", "line": 42, "issue": "unclosed file handle, never calls .close()"}
    ]


@patch("scan_worker.flash_review.OpenAICompatibleAdapter")
def test_review_diff_treats_malformed_json_as_no_findings(mock_adapter_class):
    mock_adapter = MagicMock()
    mock_adapter.simple_completion.return_value = "not valid json at all"
    mock_adapter_class.return_value = mock_adapter

    assert review_diff("some diff text") == []


@patch("scan_worker.flash_review.OpenAICompatibleAdapter")
def test_review_diff_drops_findings_missing_required_fields(mock_adapter_class):
    mock_adapter = MagicMock()
    mock_adapter.simple_completion.return_value = (
        '[{"file": "app.py", "issue": "missing a line number"}, '
        '{"file": "b.py", "line": 3, "issue": "this one is valid"}]'
    )
    mock_adapter_class.return_value = mock_adapter

    findings = review_diff("some diff text")

    assert findings == [{"file": "b.py", "line": 3, "issue": "this one is valid"}]


@patch("scan_worker.flash_review.OpenAICompatibleAdapter")
def test_review_diff_threads_on_usage_to_the_adapter(mock_adapter_class):
    mock_adapter = MagicMock()
    mock_adapter.simple_completion.return_value = "[]"
    mock_adapter_class.return_value = mock_adapter

    on_usage = lambda p, c: None
    review_diff("some diff text", on_usage=on_usage)

    _, kwargs = mock_adapter_class.call_args
    assert kwargs["on_usage"] is on_usage
    assert kwargs["model"] == "deepseek-v4-flash"


@patch("scan_worker.flash_review.OpenAICompatibleAdapter")
def test_review_diff_includes_file_context_in_prompt(mock_adapter_class):
    mock_adapter = MagicMock()
    mock_adapter.simple_completion.return_value = "[]"
    mock_adapter_class.return_value = mock_adapter

    review_diff("some diff", file_context="--- full content: a.py ---\nprint(1)")

    call_args = mock_adapter.simple_completion.call_args
    assert "print(1)" in call_args.args[1] or "print(1)" in call_args.kwargs.get("user_prompt", "")


@patch("scan_worker.flash_review.OpenAICompatibleAdapter")
def test_review_diff_includes_code_evidence_context_in_prompt(mock_adapter_class):
    mock_adapter = MagicMock()
    mock_adapter.simple_completion.return_value = "[]"
    mock_adapter_class.return_value = mock_adapter

    review_diff(
        "some diff",
        code_evidence_context="--- code evidence ---\na.py:1 symbol=foo owner=@api",
    )

    call_args = mock_adapter.simple_completion.call_args
    assert "a.py:1 symbol=foo owner=@api" in call_args.args[1]


def test_build_code_evidence_context_includes_file_symbol_dependency_and_risk():
    evidence = {
        "repository": {
            "modules": [
                {
                    "path": "a.py",
                    "imports": ["b.py"],
                    "symbols": {"functions": [{"name": "foo", "start_line": 1, "end_line": 2}], "classes": []},
                }
            ],
            "api_endpoints": {"endpoints": []},
        },
        "security": {
            "secrets": {"findings": [{"path": "a.py", "line": 2, "pattern": "generic_secret"}]},
            "dependency_vulnerabilities": {"findings": []},
            "dependency_licenses": {"findings": []},
        },
        "architecture": {"layer_violations": {"violations": []}},
    }

    context = build_code_evidence_context(evidence, ["a.py"])

    assert "a.py:1" in context
    assert "symbol=foo" in context
    assert "dependency=b.py" in context
    assert "risk=generic_secret at a.py:2" in context


@patch("scan_worker.flash_review.OpenAICompatibleAdapter")
def test_review_diff_parses_optional_suggestion_field(mock_adapter_class):
    mock_adapter = MagicMock()
    mock_adapter.simple_completion.return_value = (
        '[{"file": "a.py", "line": 3, "issue": "off-by-one", '
        '"suggestion": "for i in range(n):"}]'
    )
    mock_adapter_class.return_value = mock_adapter

    findings = review_diff("some diff")

    assert findings == [
        {"file": "a.py", "line": 3, "issue": "off-by-one", "suggestion": "for i in range(n):"}
    ]


@patch("scan_worker.flash_review.OpenAICompatibleAdapter")
def test_review_diff_suggestion_field_is_optional(mock_adapter_class):
    mock_adapter = MagicMock()
    mock_adapter.simple_completion.return_value = (
        '[{"file": "a.py", "line": 3, "issue": "off-by-one"}]'
    )
    mock_adapter_class.return_value = mock_adapter

    findings = review_diff("some diff")

    assert findings == [{"file": "a.py", "line": 3, "issue": "off-by-one"}]


def test_gather_file_context_stops_at_max_files(monkeypatch):
    from scan_worker import flash_review

    monkeypatch.setattr(flash_review, "MAX_CONTEXT_FILES", 2)
    fetched = []

    def fake_fetch(client, token, repo, path, ref):
        fetched.append(path)
        return "x" * 10

    monkeypatch.setattr(flash_review, "fetch_file_content", fake_fetch)

    flash_review.gather_file_context(None, "tok", "o/r", ["a.py", "b.py", "c.py", "d.py"], "sha")

    assert fetched == ["a.py", "b.py"]


def test_gather_file_context_skips_oversized_files(monkeypatch):
    from scan_worker import flash_review

    monkeypatch.setattr(flash_review, "MAX_CONTEXT_FILE_BYTES", 5)

    def fake_fetch(client, token, repo, path, ref):
        return "way too long for the cap"

    monkeypatch.setattr(flash_review, "fetch_file_content", fake_fetch)

    result = flash_review.gather_file_context(None, "tok", "o/r", ["a.py"], "sha")

    assert "a.py" not in result


def test_gather_file_context_stops_at_total_byte_budget(monkeypatch):
    from scan_worker import flash_review

    monkeypatch.setattr(flash_review, "MAX_CONTEXT_FILES", 10)
    monkeypatch.setattr(flash_review, "MAX_CONTEXT_FILE_BYTES", 1000)
    monkeypatch.setattr(flash_review, "MAX_CONTEXT_TOTAL_BYTES", 15)

    def fake_fetch(client, token, repo, path, ref):
        return "0123456789"

    monkeypatch.setattr(flash_review, "fetch_file_content", fake_fetch)

    result = flash_review.gather_file_context(None, "tok", "o/r", ["a.py", "b.py", "c.py"], "sha")

    assert result.count("0123456789") == 1
