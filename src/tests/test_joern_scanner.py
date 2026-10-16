import json
import os
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


def test_check_joern_resolves_which_to_absolute_paths(tmp_path):
    # Real gap found via audit: this scanner was missed by the sibling fix
    # (bandit/semgrep/gosec/pmd/bearer/sonarqube/trivy) for the same class
    # - shutil.which() can return a path relative to a relative PATH
    # entry, and both subprocess calls below switch cwd to a throwaway
    # tmp_path - a scanned (untrusted) repo could shadow a bare relative
    # name with its own same-named executable (Strix security review,
    # 2026-10-15).
    (tmp_path / "go.mod").write_text("module example\n\ngo 1.21\n")
    (tmp_path / "main.go").write_text("package main\n")

    called_cmds = []

    def fake_run(cmd, **kwargs):
        called_cmds.append(cmd)
        if "gosrc2cpg" in cmd[0]:
            cpg_path = cmd[cmd.index("-o") + 1]
            with open(cpg_path, "w") as f:
                f.write("fake cpg")
            return _mock_run(0)
        output_path = None
        for arg in cmd:
            if arg.startswith("outputPath="):
                output_path = arg.split("=", 1)[1]
        with open(output_path, "w") as f:
            json.dump([], f)
        return _mock_run(0)

    with patch(
        "aletheore.static_analysis.joern_scanner.shutil.which",
        side_effect=lambda name: f"relative/{name}",
    ), patch("aletheore.static_analysis.joern_scanner.subprocess.run", side_effect=fake_run):
        check_joern(tmp_path)

    assert len(called_cmds) == 2
    for cmd in called_cmds:
        assert os.path.isabs(cmd[0])
        assert not cmd[0].startswith("relative/")


def test_check_joern_excludes_findings_under_worktrees_dir(tmp_path):
    # Real bug found via audit (2026-09-21): unlike bandit/bearer/gosec/
    # semgrep/sonarqube, check_joern never called filter_findings, so a
    # finding under .worktrees/ or .repowise/ (or a user's ignored_paths)
    # reached the caller unfiltered - exactly the class of duplicate/noise
    # finding filter_findings exists to strip for every other scanner here.
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
            "message": "real finding, must survive",
        },
        {
            "tool": "joern",
            "rule_id": "asymmetric-cache-trust-go",
            "severity": "critical",
            "type": "vulnerability",
            "path": ".worktrees/agent-1/service.go",
            "line": 117,
            "message": "duplicate under a nested worktree checkout, must be excluded",
        },
    ]

    def fake_run(cmd, **kwargs):
        if "gosrc2cpg" in cmd[0]:
            cpg_path = cmd[cmd.index("-o") + 1]
            with open(cpg_path, "w") as f:
                f.write("fake cpg")
            return _mock_run(0)
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
    assert result["findings"] == [findings_payload[0]]


def test_check_joern_reports_cpg_build_failure(tmp_path):
    (tmp_path / "go.mod").write_text("module example\n\ngo 1.21\n")
    (tmp_path / "main.go").write_text("package main\n")

    with patch("aletheore.static_analysis.joern_scanner.shutil.which", side_effect=lambda name: f"/usr/local/bin/{name}"), \
         patch("aletheore.static_analysis.joern_scanner.subprocess.run", return_value=_mock_run(1, stderr="boom")):
        result = check_joern(tmp_path)

    assert result["checked"] is False
    assert "CPG build failed" in result["reason"]
