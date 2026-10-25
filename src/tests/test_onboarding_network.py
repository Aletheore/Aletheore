import json
import socket
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
    evidence = load_evidence(repo)
    assert ["service.py", "pricing.py"] in evidence["repository"]["dependency_graph"]["edges"]
    hotspots = find_hotspots(evidence, None)
    assert hotspots[0]["path"] == "pricing.py"
    assert hotspots[0]["churn_count"] == 3
    query = runner.invoke(app, ["query", "hotspots", "--path", str(repo)])
    assert query.exit_code == 0, query.output
    assert "pricing.py" in query.output
