import json
from unittest.mock import MagicMock, patch

from aletheore.static_analysis.bearer_scanner import check_bearer


def _mock_run(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def test_check_bearer_reports_not_installed(tmp_path):
    with patch("aletheore.static_analysis.bearer_scanner.shutil.which", return_value=None):
        result = check_bearer(tmp_path)

    assert result == {"checked": False, "reason": "bearer not installed", "findings": []}


def test_check_bearer_requires_a_git_tracked_working_tree(tmp_path):
    # Real requirement confirmed live: an untracked working tree makes
    # Bearer silently report zero findings ("couldn't find any files to
    # scan"), which reads as a false "clean" rather than "didn't scan
    # anything" - caught before ever calling the subprocess.
    with patch("aletheore.static_analysis.bearer_scanner.shutil.which", return_value="/usr/bin/bearer"), \
         patch("aletheore.static_analysis.bearer_scanner._has_commits", return_value=False):
        result = check_bearer(tmp_path)

    assert result["checked"] is False
    assert "git-tracked" in result["reason"]


def test_check_bearer_normalizes_a_real_finding_shape(tmp_path):
    # Real shape confirmed live tonight against a real throwaway git repo -
    # top-level keys are severities, not "results".
    payload = {
        "medium": [
            {
                "id": "python_lang_logger",
                "title": "Leakage of sensitive information in logger message",
                "filename": "app.py",
                "line_number": 9,
                "category_groups": ["PII", "Personal Data"],
            }
        ]
    }
    mock_result = _mock_run(1, stdout=json.dumps(payload))

    with patch("aletheore.static_analysis.bearer_scanner.shutil.which", return_value="/usr/bin/bearer"), \
         patch("aletheore.static_analysis.bearer_scanner._has_commits", return_value=True), \
         patch("aletheore.static_analysis.bearer_scanner.subprocess.run", return_value=mock_result):
        result = check_bearer(tmp_path)

    assert result["checked"] is True
    assert result["findings"] == [
        {
            "tool": "bearer",
            "rule_id": "python_lang_logger",
            "severity": "minor",
            "type": "privacy",
            "path": "app.py",
            "line": 9,
            "message": "Leakage of sensitive information in logger message",
        }
    ]


def test_check_bearer_treats_a_fatal_exit_code_as_a_real_failure(tmp_path):
    mock_result = _mock_run(2, stdout="", stderr="fatal")

    with patch("aletheore.static_analysis.bearer_scanner.shutil.which", return_value="/usr/bin/bearer"), \
         patch("aletheore.static_analysis.bearer_scanner._has_commits", return_value=True), \
         patch("aletheore.static_analysis.bearer_scanner.subprocess.run", return_value=mock_result):
        result = check_bearer(tmp_path)

    assert result["checked"] is False
