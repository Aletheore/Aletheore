from unittest.mock import MagicMock, patch

from aletheore.static_analysis.sonarqube_scanner import check_sonarqube


def _mock_run(returncode: int = 0) -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = ""
    result.stderr = ""
    return result


def test_check_sonarqube_is_opt_in_and_silent_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("SONARQUBE_HOST_URL", raising=False)

    result = check_sonarqube(tmp_path)

    assert result["checked"] is False
    assert "SONARQUBE_HOST_URL" in result["reason"]


def test_check_sonarqube_reports_scanner_not_installed(tmp_path):
    with patch("aletheore.static_analysis.sonarqube_scanner.shutil.which", return_value=None):
        result = check_sonarqube(tmp_path, host_url="http://localhost:9000")

    assert result["checked"] is False
    assert "sonar-scanner CLI not installed" in result["reason"]


def test_check_sonarqube_full_happy_path(tmp_path, monkeypatch):
    monkeypatch.setenv("SONARQUBE_PROJECT_KEY", "myproj")
    # Real shape confirmed live tonight against an actual local SonarQube
    # Community Edition server (Docker), a real project scan, and a real
    # generated user token - not assumed from API docs.
    scannerwork = tmp_path / ".scannerwork"
    scannerwork.mkdir()
    (scannerwork / "report-task.txt").write_text("ceTaskId=AB123\nceTaskUrl=http://x\n")

    with patch("aletheore.static_analysis.sonarqube_scanner.shutil.which", return_value="/usr/bin/sonar-scanner"), \
         patch("aletheore.static_analysis.sonarqube_scanner.subprocess.run", return_value=_mock_run(0)), \
         patch(
             "aletheore.static_analysis.sonarqube_scanner._api_get",
             side_effect=[
                 {"task": {"status": "SUCCESS"}},
                 {
                     "issues": [
                         {
                             "rule": "python:S3776",
                             "severity": "CRITICAL",
                             "type": "CODE_SMELL",
                             "component": "myproj:src/app.py",
                             "line": 41,
                             "message": "Refactor this function.",
                         }
                     ],
                     "paging": {"total": 1},
                 },
             ],
         ):
        result = check_sonarqube(tmp_path, host_url="http://localhost:9000")

    assert result["checked"] is True
    assert result["findings"] == [
        {
            "tool": "sonarqube",
            "rule_id": "python:S3776",
            "severity": "critical",
            "type": "code_smell",
            "path": "src/app.py",
            "line": 41,
            "message": "Refactor this function.",
        }
    ]


def test_check_sonarqube_reports_scanner_failure(tmp_path):
    with patch("aletheore.static_analysis.sonarqube_scanner.shutil.which", return_value="/usr/bin/sonar-scanner"), \
         patch("aletheore.static_analysis.sonarqube_scanner.subprocess.run", return_value=_mock_run(1)):
        result = check_sonarqube(tmp_path, host_url="http://localhost:9000")

    assert result["checked"] is False
