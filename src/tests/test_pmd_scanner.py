import json
from unittest.mock import MagicMock, patch

from aletheore.static_analysis.pmd_scanner import check_pmd


def _mock_run(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def test_check_pmd_is_checked_true_with_no_findings_when_no_java_source(tmp_path):
    (tmp_path / "app.py").write_text("print(1)\n")

    with patch("aletheore.static_analysis.pmd_scanner.shutil.which") as mock_which:
        result = check_pmd(tmp_path)

    mock_which.assert_not_called()
    assert result == {"checked": True, "reason": None, "findings": []}


def test_check_pmd_reports_not_installed(tmp_path):
    (tmp_path / "App.java").write_text("public class App {}\n")

    with patch("aletheore.static_analysis.pmd_scanner.shutil.which", return_value=None):
        result = check_pmd(tmp_path)

    assert result == {"checked": False, "reason": "pmd not installed", "findings": []}


def test_check_pmd_normalizes_a_real_finding_shape(tmp_path):
    (tmp_path / "App.java").write_text("public class App {}\n")
    # Real shape confirmed live against a real PMD 7.27.0 run (2026-09-21) -
    # "filename" is absolute when -d is given an absolute path.
    payload = {
        "files": [
            {
                "filename": str(tmp_path / "App.java"),
                "violations": [
                    {
                        "beginline": 4,
                        "rule": "SystemPrintln",
                        "ruleset": "Best Practices",
                        "priority": 2,
                        "description": "Usage of System.out/err",
                    }
                ],
            }
        ]
    }
    mock_result = _mock_run(4, stdout=json.dumps(payload))

    with patch("aletheore.static_analysis.pmd_scanner.shutil.which", return_value="/usr/local/bin/pmd"), \
         patch("aletheore.static_analysis.pmd_scanner.subprocess.run", return_value=mock_result):
        result = check_pmd(tmp_path)

    assert result["checked"] is True
    assert result["findings"] == [
        {
            "tool": "pmd",
            "rule_id": "SystemPrintln",
            "severity": "major",
            "type": "bug",
            "path": "App.java",
            "line": 4,
            "message": "Usage of System.out/err",
        }
    ]


def test_check_pmd_normalizes_path_to_forward_slashes_even_on_windows(tmp_path, monkeypatch):
    # Real bug found on Windows CI in semgrep_scanner.py's identical
    # pattern, audited into every scanner sharing it: str(Path(...))
    # renders with the OS's native separator - a backslash-joined path on
    # Windows - while every other path in this codebase's evidence uses
    # .as_posix(). Can't be reproduced by just running on this (POSIX)
    # machine - str(PosixPath(...)) already uses forward slashes here - so
    # this simulates Windows' real Path.relative_to() return shape
    # directly (a PureWindowsPath) rather than requiring an actual Windows
    # machine to prove the fix.
    from pathlib import Path, PureWindowsPath

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.java").write_text("public class App {}\n")
    abs_path = tmp_path / "src" / "App.java"
    expected_target = abs_path.resolve()
    windows_relative = PureWindowsPath("src", "App.java")
    original_relative_to = Path.relative_to

    def patched_relative_to(self, other):
        if self == expected_target and other == tmp_path.resolve():
            return windows_relative
        return original_relative_to(self, other)

    monkeypatch.setattr(Path, "relative_to", patched_relative_to)

    payload = {
        "files": [
            {
                "filename": str(abs_path),
                "violations": [
                    {
                        "beginline": 4,
                        "rule": "SystemPrintln",
                        "ruleset": "Best Practices",
                        "priority": 2,
                        "description": "Usage of System.out/err",
                    }
                ],
            }
        ]
    }
    mock_result = _mock_run(4, stdout=json.dumps(payload))

    with patch("aletheore.static_analysis.pmd_scanner.shutil.which", return_value="/usr/local/bin/pmd"), \
         patch("aletheore.static_analysis.pmd_scanner.subprocess.run", return_value=mock_result):
        result = check_pmd(tmp_path)

    assert result["findings"][0]["path"] == "src/App.java"
    assert "\\" not in result["findings"][0]["path"]


def test_check_pmd_maps_security_ruleset_to_vulnerability_type(tmp_path):
    (tmp_path / "App.java").write_text("public class App {}\n")
    payload = {
        "files": [
            {
                "filename": str(tmp_path / "App.java"),
                "violations": [
                    {
                        "beginline": 10,
                        "rule": "HardCodedCryptoKey",
                        "ruleset": "Security",
                        "priority": 1,
                        "description": "Do not use hard coded values for cryptographic key",
                    }
                ],
            }
        ]
    }
    mock_result = _mock_run(4, stdout=json.dumps(payload))

    with patch("aletheore.static_analysis.pmd_scanner.shutil.which", return_value="/usr/local/bin/pmd"), \
         patch("aletheore.static_analysis.pmd_scanner.subprocess.run", return_value=mock_result):
        result = check_pmd(tmp_path)

    assert result["findings"][0]["type"] == "vulnerability"
    assert result["findings"][0]["severity"] == "critical"


def test_check_pmd_filters_known_noisy_rules(tmp_path):
    # Real finding (2026-09-21): unfiltered against gson (264 real Java
    # files), WrongTestAnnotation/UnitTestContainsTooManyAsserts alone
    # accounted for 70% of all violations - test-authoring-convention
    # noise, not bugs. CloseResource sampled as a real false positive
    # (flagged an in-memory JsonTreeWriter whose close() is a no-op).
    (tmp_path / "App.java").write_text("public class App {}\n")
    payload = {
        "files": [
            {
                "filename": str(tmp_path / "App.java"),
                "violations": [
                    {"beginline": 1, "rule": "WrongTestAnnotation", "ruleset": "Error Prone", "priority": 3, "description": "m"},
                    {"beginline": 2, "rule": "CloseResource", "ruleset": "Error Prone", "priority": 3, "description": "m"},
                    {"beginline": 3, "rule": "NullAssignment", "ruleset": "Error Prone", "priority": 3, "description": "real one"},
                ],
            }
        ]
    }
    mock_result = _mock_run(4, stdout=json.dumps(payload))

    with patch("aletheore.static_analysis.pmd_scanner.shutil.which", return_value="/usr/local/bin/pmd"), \
         patch("aletheore.static_analysis.pmd_scanner.subprocess.run", return_value=mock_result):
        result = check_pmd(tmp_path)

    assert len(result["findings"]) == 1
    assert result["findings"][0]["rule_id"] == "NullAssignment"


def test_check_pmd_treats_a_fatal_exit_code_as_a_real_failure(tmp_path):
    (tmp_path / "App.java").write_text("public class App {}\n")
    # Real exit code confirmed live: 1 = a real config/runtime error (e.g.
    # an unresolvable ruleset reference), distinct from 4 (violations
    # found, not a failure) and 0 (clean).
    mock_result = _mock_run(1, stdout="", stderr="[ERROR] Cannot load ruleset")

    with patch("aletheore.static_analysis.pmd_scanner.shutil.which", return_value="/usr/local/bin/pmd"), \
         patch("aletheore.static_analysis.pmd_scanner.subprocess.run", return_value=mock_result):
        result = check_pmd(tmp_path)

    assert result["checked"] is False


def test_check_pmd_treats_exit_code_4_as_violations_found_not_a_failure(tmp_path):
    (tmp_path / "App.java").write_text("public class App {}\n")
    mock_result = _mock_run(4, stdout=json.dumps({"files": []}))

    with patch("aletheore.static_analysis.pmd_scanner.shutil.which", return_value="/usr/local/bin/pmd"), \
         patch("aletheore.static_analysis.pmd_scanner.subprocess.run", return_value=mock_result):
        result = check_pmd(tmp_path)

    assert result["checked"] is True
    assert result["findings"] == []


def test_check_pmd_exit_code_5_still_reports_findings_from_files_that_did_parse(tmp_path):
    # Real, documented PMD exit code (docs.pmd-code.org CLI reference): 5 =
    # "at least one recoverable error has occurred [on some file PMD's
    # parser couldn't handle]. There might be additionally zero or more
    # violations detected [on the files it could]." One unparsable file
    # (a newer Java syntax feature, a non-UTF8 source, a generated file)
    # must not discard every real finding from the rest of the repo.
    (tmp_path / "App.java").write_text("public class App {}\n")
    abs_path = str(tmp_path / "App.java")
    payload = {
        "files": [
            {
                "filename": abs_path,
                "violations": [
                    {
                        "rule": "NullAssignment",
                        "ruleset": "Error Prone",
                        "priority": 3,
                        "beginline": 5,
                        "description": "Assigning an Object to null",
                    }
                ],
            }
        ]
    }
    mock_result = _mock_run(5, stdout=json.dumps(payload), stderr="[ERROR] Cannot parse Other.java")

    with patch("aletheore.static_analysis.pmd_scanner.shutil.which", return_value="/usr/local/bin/pmd"), \
         patch("aletheore.static_analysis.pmd_scanner.subprocess.run", return_value=mock_result):
        result = check_pmd(tmp_path)

    assert result["checked"] is True
    assert len(result["findings"]) == 1
    assert result["findings"][0]["rule_id"] == "NullAssignment"
