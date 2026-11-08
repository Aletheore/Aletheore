import json
from unittest.mock import MagicMock, patch

import pytest

from aletheore.static_analysis._cwe import add_cwe, normalize_cwes
from aletheore.static_analysis.bandit_scanner import check_bandit
from aletheore.static_analysis.bearer_scanner import check_bearer
from aletheore.static_analysis.semgrep_scanner import check_semgrep


@pytest.mark.parametrize(
    "value, expected",
    [
        # Shapes copied from real tool output (OWASP Benchmark Java scan, 2026-11-08).
        ("CWE-89: Improper Neutralization of Special Elements used in an SQL Command", ["CWE-89"]),
        (["CWE-829: Inclusion of Functionality from Untrusted Control Sphere"], ["CWE-829"]),
        ({"id": 78, "link": "https://cwe.mitre.org/data/definitions/78.html"}, ["CWE-78"]),  # bandit
        (["73"], ["CWE-73"]),  # bearer
        ({"id": "327", "url": "x"}, ["CWE-327"]),  # gosec
        (22, ["CWE-22"]),
        (["CWE-79", "cwe-79", "CWE-89"], ["CWE-79", "CWE-89"]),  # de-duplicated, sorted
        ("cwe_501", ["CWE-501"]),
    ],
)
def test_normalize_cwes_accepts_every_scanner_shape(value, expected):
    assert normalize_cwes(value) == expected


@pytest.mark.parametrize("value", [None, "", "no weakness here", "see page 12", 0, -3, True, [], {}, {"id": None}])
def test_normalize_cwes_ignores_missing_or_non_cwe_values(value):
    assert normalize_cwes(value) == []


def test_add_cwe_leaves_findings_without_cwe_information_untouched():
    finding = {"tool": "semgrep", "rule_id": "r"}
    assert add_cwe(finding, None, "") == {"tool": "semgrep", "rule_id": "r"}
    assert "cwe" not in finding


def _run(stdout, returncode=1):
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = ""
    return result


def test_semgrep_findings_carry_cwe_from_rule_metadata(tmp_path):
    payload = {"results": [
        {"check_id": "a.str", "path": str(tmp_path / "A.java"), "start": {"line": 1},
         "extra": {"severity": "ERROR", "message": "m", "metadata": {"cwe": "CWE-78: OS Command Injection"}}},
        {"check_id": "a.list", "path": str(tmp_path / "B.java"), "start": {"line": 2},
         "extra": {"severity": "ERROR", "message": "m", "metadata": {"cwe": ["CWE-89: SQLi", "CWE-564: Hibernate"]}}},
        {"check_id": "a.none", "path": str(tmp_path / "C.java"), "start": {"line": 3},
         "extra": {"severity": "ERROR", "message": "m", "metadata": {}}},
    ]}
    with patch("aletheore.static_analysis.semgrep_scanner.shutil.which", return_value="/usr/bin/semgrep"), \
         patch("aletheore.static_analysis.semgrep_scanner.subprocess.run", return_value=_run(json.dumps(payload))):
        findings = check_semgrep(tmp_path)["findings"]

    assert findings[0]["cwe"] == ["CWE-78"]
    assert findings[1]["cwe"] == ["CWE-89", "CWE-564"], "order should follow ascending CWE number"
    assert "cwe" not in findings[2]


def test_bandit_findings_carry_cwe(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    payload = {"results": [{
        "test_id": "B602", "filename": str(tmp_path / "a.py"), "line_number": 3,
        "issue_severity": "HIGH", "issue_text": "subprocess call with shell=True",
        "issue_cwe": {"id": 78, "link": "https://cwe.mitre.org/data/definitions/78.html"},
    }]}
    with patch("aletheore.static_analysis.bandit_scanner.shutil.which", return_value="/usr/bin/bandit"), \
         patch("aletheore.static_analysis.bandit_scanner.subprocess.run", return_value=_run(json.dumps(payload))):
        findings = check_bandit(tmp_path)["findings"]

    assert findings and findings[0]["cwe"] == ["CWE-78"]


def test_bearer_findings_carry_cwe(tmp_path):
    (tmp_path / "A.java").write_text("class A {}\n")
    payload = {"high": [{
        "id": "java_lang_path_using_user_input", "filename": "A.java", "line_number": 4,
        "title": "Unsanitized user input in file path", "cwe_ids": ["73"],
    }]}
    with patch("aletheore.static_analysis.bearer_scanner.shutil.which", return_value="/usr/bin/bearer"), \
         patch("aletheore.static_analysis.bearer_scanner._has_commits", return_value=True), \
         patch("aletheore.static_analysis.bearer_scanner.subprocess.run", return_value=_run(json.dumps(payload))):
        findings = check_bearer(tmp_path)["findings"]

    assert findings and findings[0]["cwe"] == ["CWE-73"]
