import json
from unittest.mock import MagicMock, patch

from aletheore.static_analysis.trivy_scanner import check_trivy


def _mock_run(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def test_check_trivy_reports_not_installed(tmp_path):
    with patch("aletheore.static_analysis.trivy_scanner.shutil.which", return_value=None):
        result = check_trivy(tmp_path)

    assert result == {"checked": False, "reason": "trivy not installed", "findings": []}


def test_check_trivy_always_passes_offline_scan(tmp_path):
    # Real bug found live (2026-09-21): even scoped to secret,misconfig
    # only, Trivy still made a real network call resolving Maven POM
    # metadata for a Java project's pom.xml, and failed the whole scan
    # when Maven Central rate-limited it (429, confirmed live against
    # google/gson). --offline-scan must always be present to prevent this
    # regressing - a hosted scan-worker sharing one IP across every
    # customer's scans makes this a real, recurring failure mode.
    (tmp_path / "app.py").write_text("x = 1\n")
    mock_result = _mock_run(0, stdout=json.dumps({"Results": []}))

    with patch("aletheore.static_analysis.trivy_scanner.shutil.which", return_value="/usr/local/bin/trivy"), \
         patch("aletheore.static_analysis.trivy_scanner.subprocess.run", return_value=mock_result) as mock_run:
        check_trivy(tmp_path)

    called_cmd = mock_run.call_args[0][0]
    assert "--offline-scan" in called_cmd


def test_check_trivy_normalizes_a_real_secret_finding_shape(tmp_path):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-proj-abcdef1234567890\n")
    # Real shape confirmed live against this repo's own .env (2026-09-21).
    # Match/raw secret text is real trivy output too, included here to prove
    # it never reaches a returned finding even though it's present upstream.
    payload = {
        "Results": [
            {
                "Target": ".env",
                "Secrets": [
                    {
                        "RuleID": "openai-api-key",
                        "Title": "OpenAI API Key",
                        "Severity": "CRITICAL",
                        "StartLine": 1,
                        "Match": "OPENAI_API_KEY=sk-proj-abcdef1234567890",
                    }
                ],
            }
        ]
    }
    mock_result = _mock_run(0, stdout=json.dumps(payload))

    with patch("aletheore.static_analysis.trivy_scanner.shutil.which", return_value="/usr/local/bin/trivy"), \
         patch("aletheore.static_analysis.trivy_scanner.subprocess.run", return_value=mock_result):
        result = check_trivy(tmp_path)

    assert result["checked"] is True
    assert result["findings"] == [
        {
            "tool": "trivy",
            "rule_id": "openai-api-key",
            "severity": "critical",
            "type": "privacy",
            "path": ".env",
            "line": 1,
            "message": "OpenAI API Key (sha256:5f59305859ed)",
        }
    ]


def test_check_trivy_never_leaks_the_raw_secret_value(tmp_path):
    raw_secret = "sk-proj-abcdef1234567890"
    payload = {
        "Results": [
            {
                "Target": ".env",
                "Secrets": [
                    {
                        "RuleID": "openai-api-key",
                        "Title": "OpenAI API Key",
                        "Severity": "CRITICAL",
                        "StartLine": 1,
                        "Match": f"OPENAI_API_KEY={raw_secret}",
                    }
                ],
            }
        ]
    }
    mock_result = _mock_run(0, stdout=json.dumps(payload))

    with patch("aletheore.static_analysis.trivy_scanner.shutil.which", return_value="/usr/local/bin/trivy"), \
         patch("aletheore.static_analysis.trivy_scanner.subprocess.run", return_value=mock_result):
        result = check_trivy(tmp_path)

    assert raw_secret not in json.dumps(result)


def _trivy_secret_payload(match: str) -> dict:
    return {
        "Results": [
            {
                "Target": ".env",
                "Secrets": [
                    {
                        "RuleID": "openai-api-key",
                        "Title": "OpenAI API Key",
                        "Severity": "CRITICAL",
                        "StartLine": 1,
                        "Match": match,
                    }
                ],
            }
        ]
    }


def test_check_trivy_preview_hash_actually_depends_on_the_secret_value(tmp_path):
    # Real bug found auditing this PR: the preview hash used to be computed
    # from f"{path}:{line}:{rule_id}" - metadata already plaintext elsewhere
    # in the same finding - instead of Trivy's own "Match" field (the real
    # matched secret text). Two different secrets at the identical
    # path/line/rule (e.g. the same key rotated to a new value) produced the
    # exact same "preview", silently defeating the one thing a salted hash
    # of the real value is for. This asserts the hash actually changes when
    # the underlying secret does, at the identical path/line/rule.
    with patch("aletheore.static_analysis.trivy_scanner.shutil.which", return_value="/usr/local/bin/trivy"):
        with patch(
            "aletheore.static_analysis.trivy_scanner.subprocess.run",
            return_value=_mock_run(0, stdout=json.dumps(_trivy_secret_payload("OPENAI_API_KEY=sk-proj-aaaa"))),
        ):
            first = check_trivy(tmp_path)["findings"][0]["message"]
        with patch(
            "aletheore.static_analysis.trivy_scanner.subprocess.run",
            return_value=_mock_run(0, stdout=json.dumps(_trivy_secret_payload("OPENAI_API_KEY=sk-proj-bbbb"))),
        ):
            second = check_trivy(tmp_path)["findings"][0]["message"]

    assert first != second


def test_check_trivy_normalizes_a_real_misconfig_finding_shape(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\n")
    # Real shape confirmed live against this repo's own Dockerfiles
    # (2026-09-21) - missing HEALTHCHECK has no single offending line, so
    # CauseMetadata carries no StartLine for this check.
    payload = {
        "Results": [
            {
                "Target": "Dockerfile",
                "Misconfigurations": [
                    {
                        "ID": "DS026",
                        "Title": "No HEALTHCHECK defined",
                        "Message": "Dockerfile does not specify a healthcheck",
                        "Severity": "LOW",
                        "CauseMetadata": {},
                    }
                ],
            }
        ]
    }
    mock_result = _mock_run(0, stdout=json.dumps(payload))

    with patch("aletheore.static_analysis.trivy_scanner.shutil.which", return_value="/usr/local/bin/trivy"), \
         patch("aletheore.static_analysis.trivy_scanner.subprocess.run", return_value=mock_result):
        result = check_trivy(tmp_path)

    assert result["checked"] is True
    assert result["findings"] == [
        {
            "tool": "trivy",
            "rule_id": "DS026",
            "severity": "minor",
            "type": "bug",
            "path": "Dockerfile",
            "line": 0,
            "message": "Dockerfile does not specify a healthcheck",
        }
    ]


def test_check_trivy_treats_a_fatal_exit_code_as_a_real_failure(tmp_path):
    mock_result = _mock_run(1, stdout="", stderr="fatal: could not open policy cache")

    with patch("aletheore.static_analysis.trivy_scanner.shutil.which", return_value="/usr/local/bin/trivy"), \
         patch("aletheore.static_analysis.trivy_scanner.subprocess.run", return_value=mock_result):
        result = check_trivy(tmp_path)

    assert result["checked"] is False


def test_check_trivy_treats_a_timeout_as_a_real_skip_not_a_crash(tmp_path):
    import subprocess

    with patch("aletheore.static_analysis.trivy_scanner.shutil.which", return_value="/usr/local/bin/trivy"), \
         patch(
             "aletheore.static_analysis.trivy_scanner.subprocess.run",
             side_effect=subprocess.TimeoutExpired(cmd="trivy", timeout=60),
         ):
        result = check_trivy(tmp_path, timeout=60)

    assert result["checked"] is False
    assert "timed out" in result["reason"]
    assert result["findings"] == []
