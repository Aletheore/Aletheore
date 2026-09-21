import json
from unittest.mock import MagicMock, patch

from aletheore.static_analysis.joern_scanner import check_joern


def _mock_run(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def test_check_joern_is_checked_true_with_no_findings_when_no_go_source(tmp_path):
    with patch("aletheore.static_analysis.joern_scanner.shutil.which") as mock_which:
        result = check_joern(tmp_path)

    mock_which.assert_not_called()
    assert result == {"checked": True, "reason": None, "findings": []}


def test_check_joern_reports_not_installed(tmp_path):
    (tmp_path / "main.go").write_text("package main\n")

    with patch("aletheore.static_analysis.joern_scanner.shutil.which", return_value=None):
        result = check_joern(tmp_path)

    assert result == {"checked": False, "reason": "joern not installed", "findings": []}


def test_check_joern_requires_a_go_mod(tmp_path):
    # Real requirement confirmed live: gosrc2cpg needs a go.mod at (or
    # above) the parse target to resolve module context - a bare
    # subdirectory of a larger module fails immediately without one.
    (tmp_path / "main.go").write_text("package main\n")

    with patch("aletheore.static_analysis.joern_scanner.shutil.which", return_value="/usr/local/bin/joern"):
        result = check_joern(tmp_path)

    assert result["checked"] is False
    assert "go.mod" in result["reason"]


def test_check_joern_parses_real_finding_shape(tmp_path):
    (tmp_path / "go.mod").write_text("module example\n\ngo 1.21\n")
    (tmp_path / "main.go").write_text("package main\n")

    findings_payload = [
        {
            "tool": "joern",
            "rule_id": "asymmetric-cache-trust-go",
            "severity": "critical",
            "type": "vulnerability",
            "path": "service.go",
            "line": 117,
            "message": "asymmetric cache trust",
        }
    ]

    def fake_run(cmd, **kwargs):
        if "gosrc2cpg" in cmd[0]:
            cpg_path = cmd[cmd.index("-o") + 1]
            with open(cpg_path, "w") as f:
                f.write("fake cpg")
            return _mock_run(0)
        # joern --script ... --param outputPath=...
        output_path = None
        for arg in cmd:
            if arg.startswith("outputPath="):
                output_path = arg.split("=", 1)[1]
        with open(output_path, "w") as f:
            json.dump(findings_payload, f)
        return _mock_run(0)

    with patch("aletheore.static_analysis.joern_scanner.shutil.which", side_effect=lambda name: f"/usr/local/bin/{name}"), \
         patch("aletheore.static_analysis.joern_scanner.subprocess.run", side_effect=fake_run):
        result = check_joern(tmp_path)

    assert result["checked"] is True
    assert result["findings"] == findings_payload


def test_check_joern_reports_cpg_build_failure(tmp_path):
    (tmp_path / "go.mod").write_text("module example\n\ngo 1.21\n")
    (tmp_path / "main.go").write_text("package main\n")

    with patch("aletheore.static_analysis.joern_scanner.shutil.which", side_effect=lambda name: f"/usr/local/bin/{name}"), \
         patch("aletheore.static_analysis.joern_scanner.subprocess.run", return_value=_mock_run(1, stderr="boom")):
        result = check_joern(tmp_path)

    assert result["checked"] is False
    assert "CPG build failed" in result["reason"]
