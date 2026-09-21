import json
from unittest.mock import MagicMock, patch

from aletheore.static_analysis.gosec_scanner import check_gosec


def _mock_run(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def test_check_gosec_is_checked_true_with_no_findings_when_no_go_source(tmp_path):
    # Matches check_vulnerabilities' own "nothing to check" convention: no
    # Go source at all is a real, valid "checked, nothing found" result,
    # not a skip - and never invokes the binary at all.
    with patch("aletheore.static_analysis.gosec_scanner.shutil.which") as mock_which:
        result = check_gosec(tmp_path)

    mock_which.assert_not_called()
    assert result == {"checked": True, "reason": None, "findings": []}


def test_check_gosec_reports_not_installed(tmp_path):
    (tmp_path / "main.go").write_text("package main\n")

    with patch("aletheore.static_analysis.gosec_scanner.shutil.which", return_value=None):
        result = check_gosec(tmp_path)

    assert result == {"checked": False, "reason": "gosec not installed", "findings": []}


def test_check_gosec_normalizes_a_real_finding_shape(tmp_path):
    (tmp_path / "main.go").write_text("package main\n")
    # Real shape confirmed live tonight - "file" is absolute, "line" is a
    # string.
    payload = {
        "Issues": [
            {
                "severity": "MEDIUM",
                "rule_id": "G204",
                "details": "Subprocess launched with variable",
                "file": str(tmp_path / "main.go"),
                "line": "9",
            }
        ]
    }
    mock_result = _mock_run(1, stdout=json.dumps(payload))

    with patch("aletheore.static_analysis.gosec_scanner.shutil.which", return_value="/usr/bin/gosec"), \
         patch("aletheore.static_analysis.gosec_scanner.subprocess.run", return_value=mock_result):
        result = check_gosec(tmp_path)

    assert result["checked"] is True
    assert result["findings"] == [
        {
            "tool": "gosec",
            "rule_id": "G204",
            "severity": "major",
            "type": "vulnerability",
            "path": "main.go",
            "line": 9,
            "message": "Subprocess launched with variable",
        }
    ]


def test_check_gosec_parses_a_multi_line_range(tmp_path):
    # gosec's own "line" field can be a "start-end" range for a multi-line
    # issue - real documented behavior, not hypothetical. The first number
    # is taken as the finding's line.
    (tmp_path / "main.go").write_text("package main\n")
    payload = {"Issues": [{"severity": "LOW", "rule_id": "G101", "details": "d", "file": str(tmp_path / "main.go"), "line": "12-14"}]}
    mock_result = _mock_run(1, stdout=json.dumps(payload))

    with patch("aletheore.static_analysis.gosec_scanner.shutil.which", return_value="/usr/bin/gosec"), \
         patch("aletheore.static_analysis.gosec_scanner.subprocess.run", return_value=mock_result):
        result = check_gosec(tmp_path)

    assert result["findings"][0]["line"] == 12


def test_check_gosec_excludes_ignored_directories(tmp_path):
    (tmp_path / ".claude" / "worktrees").mkdir(parents=True)
    (tmp_path / ".claude" / "worktrees" / "dup.go").write_text("package main\n")
    (tmp_path / "main.go").write_text("package main\n")

    payload = {
        "Issues": [
            {"severity": "LOW", "rule_id": "G101", "details": "d", "file": str(tmp_path / ".claude" / "worktrees" / "dup.go"), "line": "1"},
            {"severity": "LOW", "rule_id": "G101", "details": "d", "file": str(tmp_path / "main.go"), "line": "1"},
        ]
    }
    mock_result = _mock_run(1, stdout=json.dumps(payload))

    with patch("aletheore.static_analysis.gosec_scanner.shutil.which", return_value="/usr/bin/gosec"), \
         patch("aletheore.static_analysis.gosec_scanner.subprocess.run", return_value=mock_result):
        result = check_gosec(tmp_path)

    assert [f["path"] for f in result["findings"]] == ["main.go"]
