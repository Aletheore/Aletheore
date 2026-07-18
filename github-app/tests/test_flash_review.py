from unittest.mock import MagicMock, patch

from scan_worker.flash_review import review_diff


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
