import json
import subprocess
from unittest.mock import MagicMock, patch

from aletheore.static_analysis.semgrep_scanner import check_semgrep


def _mock_run(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def test_check_semgrep_reports_not_installed(tmp_path):
    with patch("aletheore.static_analysis.semgrep_scanner.shutil.which", return_value=None):
        result = check_semgrep(tmp_path)

    assert result == {"checked": False, "reason": "semgrep not installed", "findings": []}


def test_check_semgrep_normalizes_a_real_finding_shape(tmp_path):
    # Real shape confirmed live tonight against a real registry finding -
    # not a guessed schema.
    payload = {
        "results": [
            {
                "check_id": "go.lang.security.audit.xss.import-text-template.import-text-template",
                "path": str(tmp_path / "pkg" / "queries.go"),
                "start": {"line": 6},
                "extra": {
                    "severity": "WARNING",
                    "message": "Importing text/template risks XSS.",
                    "metadata": {"category": "security"},
                },
            }
        ]
    }
    mock_result = _mock_run(1, stdout=json.dumps(payload))

    with patch("aletheore.static_analysis.semgrep_scanner.shutil.which", return_value="/usr/bin/semgrep"), \
         patch("aletheore.static_analysis.semgrep_scanner.subprocess.run", return_value=mock_result):
        result = check_semgrep(tmp_path)

    assert result["checked"] is True
    assert result["findings"] == [
        {
            "tool": "semgrep",
            "rule_id": "go.lang.security.audit.xss.import-text-template.import-text-template",
            "severity": "major",
            "type": "vulnerability",
            "path": "pkg/queries.go",
            "line": 6,
            "message": "Importing text/template risks XSS.",
        }
    ]


def test_check_semgrep_rewrites_a_local_custom_rule_id_to_its_short_form(tmp_path):
    # Real bug found live: Semgrep dot-joins a locally-loaded rule's full
    # load path into check_id (e.g.
    # ".../static_analysis/semgrep_rules/oauth-state-not-random" becomes
    # "static.analysis.semgrep_rules.oauth-state-not-random") - unusable in
    # a PR comment. Only rewritten for rules this package actually ships,
    # never for registry rule ids.
    payload = {
        "results": [
            {
                "check_id": "src.aletheore.static_analysis.semgrep_rules.oauth-state-not-random",
                "path": str(tmp_path / "app.py"),
                "start": {"line": 4},
                "extra": {"severity": "WARNING", "message": "msg", "metadata": {}},
            }
        ]
    }
    mock_result = _mock_run(1, stdout=json.dumps(payload))

    with patch("aletheore.static_analysis.semgrep_scanner.shutil.which", return_value="/usr/bin/semgrep"), \
         patch("aletheore.static_analysis.semgrep_scanner.subprocess.run", return_value=mock_result):
        result = check_semgrep(tmp_path)

    assert result["findings"][0]["rule_id"] == "oauth-state-not-random"


def test_check_semgrep_excludes_ignored_directories(tmp_path):
    (tmp_path / "node_modules").mkdir()
    payload = {
        "results": [
            {
                "check_id": "some.rule",
                "path": str(tmp_path / "node_modules" / "pkg" / "app.py"),
                "start": {"line": 1},
                "extra": {"severity": "INFO", "message": "msg", "metadata": {}},
            },
            {
                "check_id": "some.rule",
                "path": str(tmp_path / "app.py"),
                "start": {"line": 1},
                "extra": {"severity": "INFO", "message": "msg", "metadata": {}},
            },
        ]
    }
    mock_result = _mock_run(0, stdout=json.dumps(payload))

    with patch("aletheore.static_analysis.semgrep_scanner.shutil.which", return_value="/usr/bin/semgrep"), \
         patch("aletheore.static_analysis.semgrep_scanner.subprocess.run", return_value=mock_result):
        result = check_semgrep(tmp_path)

    assert [f["path"] for f in result["findings"]] == ["app.py"]


def test_check_semgrep_treats_a_fatal_exit_code_as_a_real_failure(tmp_path):
    # Real bug found live: --config=auto combined with --metrics=off is a
    # hard Semgrep error (exit 2, empty stdout) - json.loads("{}") on the
    # empty fallback would silently report "checked: True, zero findings"
    # for a scan that never actually ran. Any returncode outside {0, 1}
    # must surface as a real failure instead.
    mock_result = _mock_run(2, stdout="", stderr="fatal config error")

    with patch("aletheore.static_analysis.semgrep_scanner.shutil.which", return_value="/usr/bin/semgrep"), \
         patch("aletheore.static_analysis.semgrep_scanner.subprocess.run", return_value=mock_result):
        result = check_semgrep(tmp_path)

    assert result["checked"] is False
    assert "fatal config error" in result["reason"]


def test_check_semgrep_reports_timeout(tmp_path):
    with patch("aletheore.static_analysis.semgrep_scanner.shutil.which", return_value="/usr/bin/semgrep"), \
         patch(
             "aletheore.static_analysis.semgrep_scanner.subprocess.run",
             side_effect=subprocess.TimeoutExpired(cmd="semgrep", timeout=180),
         ):
        result = check_semgrep(tmp_path, timeout=180)

    assert result["checked"] is False
    assert "timed out" in result["reason"]
