import json
import os
import socket
import subprocess

import pytest
from typer.testing import CliRunner

from aletheore.cli import app
from aletheore.evidence import load_evidence
from aletheore.network_plan import scan_network_plan
from aletheore.query import find_hotspots


runner = CliRunner()


def test_explain_network_resolves_config_and_cli_overrides_without_scanning(tmp_path, monkeypatch):
    (tmp_path / ".aletheore.json").write_text(json.dumps({
        "disabled_checks": ["vulnerabilities", "licenses", "static_analysis"],
    }))

    def unexpected(*args, **kwargs):
        raise AssertionError("Explanation must not scan, prompt, or open a connection")

    monkeypatch.setattr("aletheore.cli._scan", unexpected)
    monkeypatch.setattr("aletheore.cli._workspace_guard", unexpected)
    monkeypatch.setattr("aletheore.cli._resolve_bearer_toggle", unexpected)
    monkeypatch.setattr(socket, "create_connection", unexpected)
    calls = []
    original = scan_network_plan

    def capture(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr("aletheore.network_plan.scan_network_plan", capture)
    result = runner.invoke(app, ["scan", str(tmp_path), "--explain-network", "--check-licenses"])
    assert result.exit_code == 0, result.output
    assert calls == [{"vulnerabilities": False, "licenses": True, "static_analysis": False}]
    assert "no scan runs" in result.output
    assert not (tmp_path / ".aletheore").exists()


def test_network_plan_accounts_for_installed_scanners_and_environment(monkeypatch):
    monkeypatch.setattr("aletheore.network_plan.shutil.which", lambda binary: f"/bin/{binary}")
    monkeypatch.setenv("ALETHEORE_DISABLE_SEMGREP", "false")
    monkeypatch.setenv("SONARQUBE_HOST_URL", "https://secret-user:secret-pass@sonar.invalid")
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "1")
    plan = scan_network_plan(vulnerabilities=True, licenses=True, static_analysis=True)
    assert all(entry["enabled"] for entry in plan)
    assert "secret-pass" not in json.dumps(plan)
    assert "metrics" in next(entry["sends"] for entry in plan if entry["service"] == "Semgrep")
    assert "source" in next(entry["sends"] for entry in plan if entry["service"] == "SonarQube")
    monkeypatch.setenv("ALETHEORE_DISABLE_SEMGREP", "1")
    assert not scan_network_plan(vulnerabilities=False, licenses=False, static_analysis=True)[2]["enabled"]
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "off")
    plan = scan_network_plan(vulnerabilities=False, licenses=False, static_analysis=False)
    assert not any(entry["enabled"] for entry in plan)
    monkeypatch.setattr("aletheore.network_plan.shutil.which", lambda binary: None)
    assert not any(entry["enabled"] for entry in scan_network_plan(
        vulnerabilities=False, licenses=False, static_analysis=True,
    ))


def test_demo_scans_real_sample_and_supports_follow_up_queries(tmp_path, monkeypatch):
    import aletheore.demo as demo

    repo = tmp_path / "sample with spaces"
    repo.mkdir()
    monkeypatch.setattr(demo.tempfile, "mkdtemp", lambda **kwargs: str(repo))

    def unexpected(*args, **kwargs):
        raise AssertionError("Demo must not use registry checks or external scanners")

    monkeypatch.setattr("aletheore.evidence.check_dependency_vulnerabilities", unexpected)
    monkeypatch.setattr("aletheore.evidence.check_dependency_licenses", unexpected)
    monkeypatch.setattr("aletheore.evidence.run_static_analysis", unexpected)
    result = runner.invoke(app, ["demo"])
    assert result.exit_code == 0, (result.output, result.exception)
    assert "synthetic" in result.output
    assert "pricing.py" in result.output
    assert "Your task: change pricing.py::total" in result.output
    assert "3 files depend on pricing.py: 1 directly, 2 transitively." in result.output
    assert "Confirmed caller of total: service.py" in result.output
    assert "The demo did not run these tests" in result.output
    assert "Try your own repository next" in result.output
    assert (repo / ".aletheore" / "demo").read_text() == demo.DEMO_MARKER
    evidence = load_evidence(repo)
    assert ["service.py", "pricing.py"] in evidence["repository"]["dependency_graph"]["edges"]
    hotspots = find_hotspots(evidence, None)
    assert hotspots[0]["path"] == "pricing.py"
    assert hotspots[0]["churn_count"] == 3
    query = runner.invoke(app, ["query", "hotspots", "--path", str(repo)])
    assert query.exit_code == 0, query.output
    assert "pricing.py" in query.output


@pytest.mark.parametrize("failure", [
    FileNotFoundError("git missing"),
    subprocess.TimeoutExpired("git", 30),
    subprocess.CalledProcessError(128, "git", stderr="fatal: demo commit failed"),
])
def test_demo_cleans_up_failed_setup_and_reports_git_error(tmp_path, monkeypatch, failure):
    import aletheore.demo as demo

    repo = tmp_path / "failed-demo"
    repo.mkdir()
    monkeypatch.setattr(demo.tempfile, "mkdtemp", lambda **kwargs: str(repo))

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(demo.subprocess, "run", fail)
    result = runner.invoke(app, ["demo"])
    assert result.exit_code == 1, result.output
    assert "Demo failed:" in result.output
    assert not repo.exists()
    if isinstance(failure, subprocess.CalledProcessError):
        assert "fatal: demo commit failed" in result.output


def test_demo_cleans_up_if_scan_fails(tmp_path, monkeypatch):
    import aletheore.demo as demo

    repo = tmp_path / "failed-scan"
    repo.mkdir()
    monkeypatch.setattr(demo.tempfile, "mkdtemp", lambda **kwargs: str(repo))
    monkeypatch.setattr(demo.subprocess, "run", lambda *args, **kwargs: None)

    def fail(*args, **kwargs):
        raise OSError("cannot scan demo")

    monkeypatch.setattr(demo, "scan_repository", fail)
    result = runner.invoke(app, ["demo"])
    assert result.exit_code == 1, result.output
    assert "cannot scan demo" in result.output
    assert not repo.exists()


def test_demo_git_uses_resolved_executable_and_shell_free_arguments(tmp_path, monkeypatch):
    import aletheore.demo as demo

    repo = tmp_path / "sample with spaces"
    repo.mkdir()
    executable = str(tmp_path / "trusted tools" / "git")
    monkeypatch.setattr(demo.tempfile, "mkdtemp", lambda **kwargs: str(repo))
    monkeypatch.setattr(demo, "git_executable", lambda: executable)
    calls = []
    monkeypatch.setattr(demo.subprocess, "run", lambda argv, **kwargs: calls.append((argv, kwargs)))
    monkeypatch.setattr(demo, "scan_repository", lambda *args, **kwargs: {})
    monkeypatch.setattr(demo, "write_evidence", lambda *args: (repo / ".aletheore").mkdir())

    assert demo.create_demo() == (repo, {})
    assert len(calls) == 7
    for argv, kwargs in calls:
        assert argv[0] == executable
        assert kwargs["shell"] is False
        assert kwargs["cwd"] == repo
        assert kwargs["env"]["GIT_CONFIG_NOSYSTEM"] == "1"
        assert "core.hooksPath=" + str(repo / ".demo-hooks") in argv
        assert "commit.gpgsign=false" in argv
        assert "maintenance.auto=false" in argv
        assert "gc.auto=0" in argv
    assert calls[0][0][-3:] == ["init", "--quiet", "--template="]
    assert calls[1][0][-6:] == ["add", "--", *demo._FILES]


def test_demo_cleans_up_when_git_cannot_be_resolved(tmp_path, monkeypatch):
    import aletheore.demo as demo

    repo = tmp_path / "missing-git-demo"
    repo.mkdir()
    monkeypatch.setattr(demo.tempfile, "mkdtemp", lambda **kwargs: str(repo))

    def missing_git():
        raise FileNotFoundError("git was not found on PATH")

    monkeypatch.setattr(demo, "git_executable", missing_git)
    result = runner.invoke(app, ["demo"])
    assert result.exit_code == 1
    assert "git was not found on PATH" in result.output
    assert not repo.exists()


def test_demo_commands_remain_copyable_in_a_narrow_terminal(tmp_path, monkeypatch):
    import shlex

    import aletheore.demo as demo
    from rich.console import Console

    repo = tmp_path / "a long sample path with spaces and [brackets]"
    repo.mkdir()
    monkeypatch.setattr(demo.tempfile, "mkdtemp", lambda **kwargs: str(repo))
    monkeypatch.setattr("aletheore.cli.console", Console(width=40))
    result = runner.invoke(app, ["demo"])
    assert result.exit_code == 0, result.output
    quote = subprocess.list2cmdline if os.name == "nt" else shlex.join
    assert quote(["cd", str(repo)]) in result.output.splitlines()
    for command in ("aletheore dependents pricing.py", "aletheore tests-for pricing.py", "aletheore scan --explain-network"):
        assert command in result.output.splitlines()


@pytest.mark.parametrize("lookup", ["source", "impact", "tests", "source-read"])
def test_demo_reports_sample_lookup_errors_without_a_traceback(tmp_path, monkeypatch, lookup):
    import aletheore.demo as demo
    from aletheore.query import ModuleNotFoundInEvidenceError, SymbolNotFoundInEvidenceError
    from aletheore.tests_for import TestsForError

    repo = tmp_path / "retained sample"
    repo.mkdir()
    monkeypatch.setattr(demo.tempfile, "mkdtemp", lambda **kwargs: str(repo))
    name, failure = {
        "source": ("aletheore.query.find_symbol_source", SymbolNotFoundInEvidenceError("pricing.py", "total")),
        "impact": ("aletheore.query.find_blast_radius", ModuleNotFoundInEvidenceError("pricing.py")),
        "tests": ("aletheore.tests_for.tests_for_report", TestsForError("sample missing")),
        "source-read": ("aletheore.query.find_symbol_source", OSError("sample unreadable")),
    }[lookup]

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(name, fail)
    result = runner.invoke(app, ["demo"])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Demo could not read its sample evidence:" in result.output
    assert "Run aletheore demo again" in result.output
    assert str(repo) in result.output
    assert (repo / ".aletheore" / "air.json").exists()


def test_failed_scan_removes_real_git_readonly_objects(tmp_path, monkeypatch):
    import aletheore.demo as demo
    import stat

    repo = tmp_path / "failed real git demo"
    repo.mkdir()
    monkeypatch.setattr(demo.tempfile, "mkdtemp", lambda **kwargs: str(repo))

    def fail(*args, **kwargs):
        object_dir = repo / ".git" / "objects"
        # Only loose objects: maintenance.lock is transient and can disappear
        # between discovery and chmod on macOS's Git runners.
        lock = object_dir / "maintenance.lock"
        lock.write_text("test maintenance lock", encoding="utf-8")
        lock_mode = lock.stat().st_mode
        objects = [path for path in object_dir.glob("[0-9a-f][0-9a-f]/*") if path.is_file()]
        assert objects
        for path in objects:
            path.chmod(stat.S_IREAD)
        assert lock.stat().st_mode == lock_mode
        lock.unlink()
        raise OSError("scan failed after git setup")

    monkeypatch.setattr(demo, "scan_repository", fail)
    with pytest.raises(OSError, match="scan failed after git setup"):
        demo.create_demo()
    assert not repo.exists()
