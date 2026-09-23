import json
from unittest.mock import MagicMock, patch

from aletheore.static_analysis.bandit_scanner import check_bandit


def _mock_run(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def test_check_bandit_is_checked_true_with_no_findings_when_no_python_source(tmp_path):
    with patch("aletheore.static_analysis.bandit_scanner.shutil.which") as mock_which:
        result = check_bandit(tmp_path)

    mock_which.assert_not_called()
    assert result == {"checked": True, "reason": None, "findings": []}


def test_check_bandit_reports_not_installed(tmp_path):
    (tmp_path / "app.py").write_text("import os\n")

    with patch("aletheore.static_analysis.bandit_scanner.shutil.which", return_value=None):
        result = check_bandit(tmp_path)

    assert result == {"checked": False, "reason": "bandit not installed", "findings": []}


def test_check_bandit_normalizes_a_real_finding_shape(tmp_path):
    (tmp_path / "app.py").write_text("import subprocess\n")
    # Real shape confirmed live tonight.
    payload = {
        "results": [
            {
                "filename": "./app.py",
                "issue_severity": "HIGH",
                "issue_text": "subprocess call with shell=True identified, security issue.",
                "line_number": 4,
                "test_id": "B602",
            }
        ]
    }
    mock_result = _mock_run(1, stdout=json.dumps(payload))

    with patch("aletheore.static_analysis.bandit_scanner.shutil.which", return_value="/usr/bin/bandit"), \
         patch("aletheore.static_analysis.bandit_scanner.subprocess.run", return_value=mock_result):
        result = check_bandit(tmp_path)

    assert result["checked"] is True
    assert result["findings"] == [
        {
            "tool": "bandit",
            "rule_id": "B602",
            "severity": "critical",
            "type": "vulnerability",
            "path": "app.py",
            "line": 4,
            "message": "subprocess call with shell=True identified, security issue.",
        }
    ]


def test_check_bandit_treats_a_fatal_exit_code_as_a_real_failure(tmp_path):
    (tmp_path / "app.py").write_text("import os\n")
    mock_result = _mock_run(3, stdout="", stderr="fatal")

    with patch("aletheore.static_analysis.bandit_scanner.shutil.which", return_value="/usr/bin/bandit"), \
         patch("aletheore.static_analysis.bandit_scanner.subprocess.run", return_value=mock_result):
        result = check_bandit(tmp_path)

    assert result["checked"] is False


def test_check_bandit_filters_known_noisy_rules(tmp_path):
    # Real finding (2026-09-23): unfiltered against pallets/flask, B101
    # ("assert used") alone was 1,054 of 1,083 total findings (97%) -
    # fires on every bare `assert`, idiomatic in both pytest-style tests
    # and ordinary programmer sanity checks, not a real security signal.
    (tmp_path / "app.py").write_text("import subprocess\n")
    payload = {
        "results": [
            {
                "filename": "./app.py",
                "issue_severity": "LOW",
                "issue_text": "Use of assert detected.",
                "line_number": 1,
                "test_id": "B101",
            },
            {
                "filename": "./app.py",
                "issue_severity": "HIGH",
                "issue_text": "subprocess call with shell=True identified, security issue.",
                "line_number": 4,
                "test_id": "B602",
            },
        ]
    }
    mock_result = _mock_run(1, stdout=json.dumps(payload))

    with patch("aletheore.static_analysis.bandit_scanner.shutil.which", return_value="/usr/bin/bandit"), \
         patch("aletheore.static_analysis.bandit_scanner.subprocess.run", return_value=mock_result):
        result = check_bandit(tmp_path)

    assert len(result["findings"]) == 1
    assert result["findings"][0]["rule_id"] == "B602"
