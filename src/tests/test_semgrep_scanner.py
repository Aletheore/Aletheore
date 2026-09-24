import json
import subprocess
from unittest.mock import MagicMock, patch

from aletheore.static_analysis.semgrep_scanner import _relative_path, check_semgrep


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


def test_relative_path_normalizes_to_forward_slashes_even_on_windows(tmp_path, monkeypatch):
    # Real bug found on Windows CI: _relative_path used str(Path(...)),
    # which renders with the OS's native separator - a backslash-joined
    # path on Windows (confirmed live: a real semgrep finding came back as
    # 'pkg\\queries.go') - while every other path in this codebase's
    # evidence (graph.py's _rel(), secrets.py's iter_all_files,
    # mcp_server.py's _search_files) uses .as_posix() specifically so
    # paths are comparable and joinable regardless of the scanning host's
    # OS. This can't be reproduced by just running on this (POSIX) machine
    # - str(PosixPath(...)) already uses forward slashes here, so the bug
    # is invisible on the exact platform this suite normally runs on.
    # Simulates Windows' real Path.relative_to() return shape directly (a
    # PureWindowsPath, exactly what a real Windows host's pathlib returns)
    # rather than requiring an actual Windows machine to prove the fix.
    from pathlib import Path, PureWindowsPath

    repo = tmp_path / "repo"
    repo.mkdir()
    raw_path = str(repo / "pkg" / "queries.go")
    expected_target = repo.resolve() / "pkg" / "queries.go"
    windows_relative = PureWindowsPath("pkg", "queries.go")
    original_relative_to = Path.relative_to

    def patched_relative_to(self, other):
        # Scoped to this exact call (matching this test's own resolved
        # paths) rather than every Path.relative_to() call process-wide -
        # a global patch also intercepts pytest's own internal path
        # handling, corrupting unrelated test IDs/output.
        if self == expected_target and other == repo.resolve():
            return windows_relative
        return original_relative_to(self, other)

    monkeypatch.setattr(Path, "relative_to", patched_relative_to)

    result = _relative_path(raw_path, repo)

    assert result == "pkg/queries.go"
    assert "\\" not in result


def test_check_semgrep_survives_explicit_null_in_nested_fields(tmp_path):
    # Real bug found via audit (2026-09-21): dict.get(key, default) only
    # ever applies its default when the key is ABSENT, not when it's
    # present with an explicit `null` value - semgrep can and does emit
    # "extra": null or "metadata": null on some result shapes. Before this
    # fix, extra.get("metadata", {}).get("category", "") would raise
    # AttributeError the moment "metadata" was present-but-null (the outer
    # .get returns None, not {}), uncaught anywhere between here and
    # check_static_analysis's per-scanner loop - one malformed finding
    # aborted the whole static-analysis pass instead of just itself.
    payload = {
        "results": [
            {
                "check_id": "go.lang.some-rule",
                "path": str(tmp_path / "a.go"),
                "start": None,
                "extra": {"severity": "WARNING", "message": "m", "metadata": None},
            },
            {
                "check_id": "go.lang.other-rule",
                "path": str(tmp_path / "b.go"),
                "start": {"line": 9},
                "extra": None,
            },
        ]
    }
    mock_result = _mock_run(1, stdout=json.dumps(payload))

    with patch("aletheore.static_analysis.semgrep_scanner.shutil.which", return_value="/usr/bin/semgrep"), \
         patch("aletheore.static_analysis.semgrep_scanner.subprocess.run", return_value=mock_result):
        result = check_semgrep(tmp_path)

    assert result["checked"] is True
    assert len(result["findings"]) == 2
    assert result["findings"][0]["line"] == 0
    assert result["findings"][1]["message"] == ""


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
