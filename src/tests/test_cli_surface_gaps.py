"""Regression tests for the CLI command surface and MCP server gaps found by a QA audit of 0.10.2."""

import http.server
import json
import os
import subprocess
import sys
import threading
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from aletheore import cli
from aletheore.cli import app
from aletheore.citation_verifier import extract_citations, verify_citations
from tests.air_fixtures import minimal_air_evidence

runner = CliRunner()


def _scanned_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "util.py").write_text(
        "def helper():\n    return 1\n\n\ndef users():\n    # calls helper here\n    return helper()\n"
    )
    (repo / "app" / "main.py").write_text(
        "from app.util import helper\n"
        "from flask import Flask\n"
        "app = Flask(__name__)\n\n"
        "@app.get('/users')\n"
        "def list_users():\n    return helper()\n\n"
        "@app.post('/users')\n"
        "def make_user():\n    return helper()\n"
    )
    result = runner.invoke(app, ["scan", str(repo), "--no-check-vulnerabilities", "--no-check-licenses"])
    assert result.exit_code == 0, result.output
    return repo


# --- H2: aletheore mcp never writes to stdout before the protocol -------------------------------------------------


def test_mcp_nav_notice_goes_to_stderr_and_stdout_starts_with_jsonrpc(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    src = str(Path(cli.__file__).resolve().parent.parent)
    env = {**os.environ, "PYTHONPATH": src, "ALETHEORE_CRASH_REPORTING": "0", "HOME": str(tmp_path)}
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "aletheore.cli", "mcp", str(repo), "--profile", "nav", "--yes"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        proc.stdin.write(json.dumps(initialize) + "\n")
        proc.stdin.flush()
        first_line = proc.stdout.readline()
    finally:
        proc.stdin.close()
        proc.terminate()
        stderr = proc.stderr.read()
        proc.wait(timeout=30)
    message = json.loads(first_line)  # raises if a plain-text notice came first
    assert message["jsonrpc"] == "2.0" and message["id"] == 1
    assert message["result"]["serverInfo"]["version"]  # L22: version is set
    assert "--profile nav does not watch" in stderr


# --- M2: preferences ---------------------------------------------------------------------------------------------


def test_config_crash_reporting_fails_loudly_when_it_cannot_save(monkeypatch):
    monkeypatch.delenv("ALETHEORE_CRASH_REPORTING", raising=False)
    with patch("aletheore.cli.set_crash_reporting_enabled", return_value=False):
        result = runner.invoke(app, ["config", "crash-reporting", "off"])
    assert result.exit_code == 1
    assert "error:" in result.output
    assert "turned off" not in result.output


def test_config_crash_reporting_says_when_the_env_var_overrides(monkeypatch):
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "0")
    result = runner.invoke(app, ["config", "crash-reporting", "on"])
    assert result.exit_code == 0
    assert "ALETHEORE_CRASH_REPORTING=0" in result.output
    assert "overrides" in result.output


def test_status_and_show_explain_an_env_var_override(monkeypatch):
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "0")
    shown = runner.invoke(app, ["config", "crash-reporting"])
    assert "Crash reporting: off" in shown.output
    assert "ALETHEORE_CRASH_REPORTING=0" in shown.output
    with patch("aletheore.cli._check_for_update", return_value="up to date"):
        status = runner.invoke(app, ["status"])
    assert "set by ALETHEORE_CRASH_REPORTING=0" in status.output


def test_save_preference_reports_failure_instead_of_swallowing_it(tmp_path):
    from aletheore import preferences

    blocked = tmp_path / "file"
    blocked.write_text("x")
    # the parent "directory" is a file, so mkdir/write raises OSError
    assert preferences.set_crash_reporting_enabled(False, blocked / "sub" / "preferences.json") is False
    assert preferences.set_crash_reporting_enabled(False, tmp_path / "ok" / "preferences.json") is True


def test_main_prints_no_banner_and_writes_no_preferences_when_env_var_disables_reporting(monkeypatch, capsys):
    monkeypatch.setenv("ALETHEORE_CRASH_REPORTING", "0")
    marked = []
    monkeypatch.setattr(cli, "mark_crash_reporting_notice_shown", lambda *a: marked.append(1))
    monkeypatch.setattr(cli, "has_shown_crash_reporting_notice", lambda *a: False)
    monkeypatch.setattr(sys, "argv", ["aletheore", "--version"])
    with pytest.raises(SystemExit):
        cli.main()
    assert marked == []
    assert "monitors for crashes" not in capsys.readouterr().err


# --- M5: healthcheck ---------------------------------------------------------------------------------------------


class _Handler(http.server.BaseHTTPRequestHandler):
    methods_seen: list[str] = []

    def _answer(self):
        _Handler.methods_seen.append(self.command)
        code = {"/ok": 200, "/missing": 404, "/boom": 500}.get(self.path, 200)
        self.send_response(code)
        self.end_headers()

    do_GET = _answer
    do_POST = _answer
    do_PUT = _answer
    do_DELETE = _answer
    do_HEAD = _answer

    def log_message(self, *args):
        pass


@pytest.fixture
def status_server():
    _Handler.methods_seen = []
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _repo_with_endpoints(tmp_path: Path, endpoints: list[tuple[str, str]]) -> Path:
    repo = tmp_path / "hc"
    (repo / ".aletheore").mkdir(parents=True)
    evidence = minimal_air_evidence()
    evidence["repository"]["api_endpoints"] = {
        "checked": True,
        "endpoints": [
            {"method": m, "path": p, "framework": "flask", "file": "app.py", "line": 1, "handler": "h", "unresolved": False}
            for m, p in endpoints
        ],
    }
    (repo / ".aletheore" / "air.json").write_text(json.dumps(evidence))
    return repo


def test_healthcheck_counts_404_and_500_as_unhealthy_and_exits_nonzero(tmp_path, status_server):
    repo = _repo_with_endpoints(tmp_path, [("GET", "/ok"), ("GET", "/missing"), ("GET", "/boom")])
    result = runner.invoke(app, ["healthcheck", str(repo), "--base-url", status_server])
    assert result.exit_code == 1
    assert "1 of 3 endpoint(s) healthy" in result.output
    assert "responded 404" in result.output and "responded 500" in result.output
    assert "UNREACHABLE" not in result.output


def test_healthcheck_all_healthy_exits_zero(tmp_path, status_server):
    repo = _repo_with_endpoints(tmp_path, [("GET", "/ok")])
    result = runner.invoke(app, ["healthcheck", str(repo), "--base-url", status_server])
    assert result.exit_code == 0
    assert "200" in result.output and "1 of 1 endpoint(s) healthy" in result.output


def test_healthcheck_only_ever_sends_get(tmp_path, status_server):
    repo = _repo_with_endpoints(tmp_path, [("POST", "/ok"), ("DELETE", "/ok"), ("PUT", "/ok"), ("GET", "/ok")])
    runner.invoke(app, ["healthcheck", str(repo), "--base-url", status_server])
    assert _Handler.methods_seen and set(_Handler.methods_seen) == {"GET"}


def test_healthcheck_explains_why_an_endpoint_is_unreachable(tmp_path):
    repo = _repo_with_endpoints(tmp_path, [("GET", "/ok")])
    refused = ConnectionRefusedError(111, "refused")
    with patch("aletheore.healthcheck._NO_REDIRECT_OPENER.open", side_effect=urllib.error.URLError(refused)):
        result = runner.invoke(app, ["healthcheck", str(repo), "--base-url", "http://localhost:5000"])
    assert result.exit_code == 1
    assert "UNREACHABLE: connection refused" in result.output
    import socket

    with patch(
        "aletheore.healthcheck._NO_REDIRECT_OPENER.open",
        side_effect=urllib.error.URLError(socket.gaierror(8, "nodename nor servname")),
    ):
        result = runner.invoke(app, ["healthcheck", str(repo), "--base-url", "http://localhost:5000"])
    assert "DNS lookup failed" in result.output
    with patch("aletheore.healthcheck._NO_REDIRECT_OPENER.open", side_effect=TimeoutError()):
        result = runner.invoke(app, ["healthcheck", str(repo), "--base-url", "http://localhost:5000"])
    assert "UNREACHABLE: timeout" in result.output


def test_healthcheck_with_no_mapped_endpoints_says_so_and_exits_zero(tmp_path):
    repo = _repo_with_endpoints(tmp_path, [])
    result = runner.invoke(app, ["healthcheck", str(repo), "--base-url", "http://localhost:5000"])
    assert result.exit_code == 0
    assert "No API endpoints are mapped" in result.output


def test_healthcheck_rejects_credentials_in_the_base_url(tmp_path):
    repo = _repo_with_endpoints(tmp_path, [("GET", "/ok")])
    with patch("aletheore.healthcheck._NO_REDIRECT_OPENER.open") as opened:
        result = runner.invoke(app, ["healthcheck", str(repo), "--base-url", "http://user:pw@localhost:5000"])
    assert result.exit_code == 1
    assert "credentials" in result.output
    assert "pw" not in result.output
    opened.assert_not_called()


# --- M6: diff gate, exit codes, dashboard history -----------------------------------------------------------------


def _evidence_file(path: Path, *, checked: bool, findings: list[dict] | None = None) -> Path:
    evidence = minimal_air_evidence()
    evidence["security"]["dependency_vulnerabilities"]["checked"] = checked
    evidence["security"]["dependency_vulnerabilities"]["findings"] = findings or []
    path.write_text(json.dumps(evidence))
    return path


_VULN = {
    "ecosystem": "PyPI",
    "package": "requests",
    "installed_version": "2.25.0",
    "advisory_id": "GHSA-xxxx",
    "summary": "x",
    "severity": [],
}


def test_diff_vulnerability_gate_is_not_enforced_when_the_check_was_toggled(tmp_path):
    old = _evidence_file(tmp_path / "old.json", checked=False)
    new = _evidence_file(tmp_path / "new.json", checked=True, findings=[_VULN])
    result = runner.invoke(app, ["diff", str(old), str(new), "--fail-on-new-vulnerabilities"])
    assert result.exit_code == 0
    assert "not enforced" in result.output
    json.loads(result.stdout)  # the note stays off stdout


def test_diff_vulnerability_gate_still_fails_when_both_snapshots_checked(tmp_path):
    old = _evidence_file(tmp_path / "old.json", checked=True)
    new = _evidence_file(tmp_path / "new.json", checked=True, findings=[_VULN])
    result = runner.invoke(app, ["diff", str(old), str(new), "--fail-on-new-vulnerabilities"])
    assert result.exit_code == 1


def test_diff_input_errors_exit_two_not_one(tmp_path):
    good = _evidence_file(tmp_path / "good.json", checked=True)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    wrong_schema = tmp_path / "schema.json"
    wrong_schema.write_text("{}")
    assert runner.invoke(app, ["diff", str(tmp_path / "missing.json"), str(good)]).exit_code == 2
    assert runner.invoke(app, ["diff", str(bad), str(good)]).exit_code == 2
    assert runner.invoke(app, ["diff", str(wrong_schema), str(good)]).exit_code == 2
    assert "Exit codes" in runner.invoke(app, ["diff", "--help"]).output


def test_dashboard_history_reports_none_for_scans_that_skipped_the_vulnerability_check(tmp_path):
    from aletheore.dashboard import build_history_summary
    from aletheore.history import save_snapshot

    repo = tmp_path / "d"
    repo.mkdir()
    evidence = minimal_air_evidence()
    evidence["scanned_at"] = "2026-01-01T00:00:00+00:00"
    evidence["security"]["dependency_vulnerabilities"]["checked"] = False
    save_snapshot(evidence, repo)
    summary = build_history_summary(repo)
    assert summary and summary[0]["vulnerability_findings"] is None


# --- M7: verify --------------------------------------------------------------------------------------------------


def _evidence_with(paths: list[str]) -> dict:
    evidence = minimal_air_evidence()
    evidence["repository"]["modules"] = [{"path": p, "imports": [], "imported_by": [], "symbols": {"functions": [], "classes": []}} for p in paths]
    return evidence


def test_citation_range_end_is_bounds_checked_and_order_validated():
    evidence = _evidence_with(["app/util.py"])
    result = verify_citations(
        "see app/util.py:2-4 and app/util.py:5-99 and app/util.py:3-2",
        evidence,
        fetch_line_count=lambda path: 5,
    )
    assert [c["line"] for c in result["verified"]] == [2]
    unverified = {(c["line"], c.get("end_line")): c.get("reason") for c in result["unverified"]}
    assert unverified[(5, 99)] == "file has 5 lines"
    assert unverified[(3, 2)] == "reversed line range"


def test_citations_outside_the_repo_are_reported_and_never_read():
    evidence = _evidence_with(["app/util.py"])
    read = []
    result = verify_citations(
        "bad ../../etc/passwd:1 and /etc/passwd:2 and ../x.py:3",
        evidence,
        fetch_line_count=lambda path: read.append(path) or 5,
    )
    assert result["total_citations"] == 3
    assert len(result["unverified"]) == 3
    assert {c["reason"] for c in result["unverified"]} == {"path outside the repository"}
    assert read == []


def test_urls_are_not_citations():
    assert extract_citations("see http://x.com/a.py:5 and https://host/p/q.py:7", {"a.py"}) == []


def test_citation_paths_are_normalized():
    evidence = _evidence_with(["app/util.py"])
    result = verify_citations("./app/util.py:2 and app//util.py:2", evidence)
    assert result["all_verified"] is True
    assert result["total_citations"] == 1  # both name the same citation


def test_verify_command_lists_outside_citations_instead_of_finding_none(tmp_path):
    repo = _scanned_repo(tmp_path)
    report = tmp_path / "r.md"
    report.write_text("Only `../../etc/passwd:1` and `/etc/passwd:2` here.\n")
    result = runner.invoke(app, ["verify", str(report), "--path", str(repo)])
    assert result.exit_code == 1
    assert "No `file:line` citations found" not in result.output
    assert "path outside the repository" in result.output
    assert "/etc/passwd:2" in result.output


# --- M8: mcp-install ---------------------------------------------------------------------------------------------


def test_mcp_install_exits_nonzero_and_prints_no_restart_line_when_nothing_was_written(tmp_path):
    (tmp_path / ".mcp.json").write_text("{not json")
    result = runner.invoke(app, ["mcp-install", str(tmp_path), "--target", "claude-code"])
    assert result.exit_code == 1
    assert "skipped" in result.output
    assert "Restart or reload" not in result.output
    assert "picked up automatically" not in result.output


def test_mcp_install_partial_failure_is_nonzero_and_only_advertises_written_targets(tmp_path):
    (tmp_path / ".mcp.json").write_text("[]")
    result = runner.invoke(app, ["mcp-install", str(tmp_path), "--target", "claude-code", "--target", "cursor"])
    assert result.exit_code == 1
    assert (tmp_path / ".cursor" / "mcp.json").exists()
    assert "Restart or reload" in result.output
    assert "picked up automatically" not in result.output  # claude-code was not written


def test_mcp_install_will_not_rewrite_jsonc_and_shows_the_snippet(tmp_path):
    config = tmp_path / ".vscode" / "mcp.json"
    config.parent.mkdir()
    original = '{\n  // keep me\n  "servers": {"x": {"command": "y"},},\n}\n'
    config.write_text(original)
    result = runner.invoke(app, ["mcp-install", str(tmp_path), "--target", "vscode"])
    assert result.exit_code == 1
    assert config.read_text() == original
    assert "comments or trailing commas" in result.output
    assert '"aletheore"' in result.output  # the exact entry to paste


def test_mcp_install_toml_warns_before_writing_and_keeps_a_backup(tmp_path):
    config = tmp_path / ".codex" / "config.toml"
    config.parent.mkdir()
    original = "# keep me\n[mcp_servers.other]\ncommand = 'z'\n"
    config.write_text(original)
    result = runner.invoke(app, ["mcp-install", str(tmp_path), "--target", "codex-cli"])
    assert result.exit_code == 0
    assert (tmp_path / ".codex" / "config.toml.bak").read_text() == original
    assert result.output.index("has comments") < result.output.index("codex-cli: wrote")


def test_mcp_install_asks_before_writing_every_file_in_a_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr("aletheore.ui.is_interactive", lambda: True)
    monkeypatch.setattr(cli, "_claude_desktop_config_path", lambda: tmp_path / "global" / "claude.json")
    declined = runner.invoke(app, ["mcp-install", str(tmp_path)], input="n\n")
    assert declined.exit_code == 1
    assert "Write these? [y/N]" in declined.output
    assert "machine-global" in declined.output
    assert not (tmp_path / ".mcp.json").exists()
    accepted = runner.invoke(app, ["mcp-install", str(tmp_path)], input="y\n")
    assert accepted.exit_code == 0
    assert (tmp_path / ".mcp.json").exists()


def test_mcp_install_yes_and_explicit_targets_skip_the_question(tmp_path, monkeypatch):
    monkeypatch.setattr("aletheore.ui.is_interactive", lambda: True)
    monkeypatch.setattr(cli, "_claude_desktop_config_path", lambda: tmp_path / "global" / "claude.json")
    assert runner.invoke(app, ["mcp-install", str(tmp_path), "--yes"]).exit_code == 0
    other = tmp_path / "o"
    other.mkdir()
    result = runner.invoke(app, ["mcp-install", str(other), "--target", "cursor"])
    assert result.exit_code == 0 and "Write these?" not in result.output


# --- M10: query --------------------------------------------------------------------------------------------------


def test_query_evidence_for_unknown_target_errors_with_close_matches(tmp_path):
    repo = _scanned_repo(tmp_path)
    result = runner.invoke(app, ["query", "evidence-for-symbol", "hlper", "--path", str(repo)])
    assert result.exit_code == 1
    assert "not found" in result.output and "helper" in result.output
    result = runner.invoke(app, ["query", "evidence-for-dependency", "nothing_zz", "--path", str(repo)])
    assert result.exit_code == 1 and "not found" in result.output
    result = runner.invoke(app, ["query", "evidence-for-endpoint", "GET /nope", "--path", str(repo)])
    assert result.exit_code == 1 and "Expected 'METHOD /path'" in result.output


def test_query_evidence_for_endpoint_accepts_a_bare_path_and_lists_methods(tmp_path):
    repo = _scanned_repo(tmp_path)
    result = runner.invoke(app, ["query", "evidence-for-endpoint", "/users", "--path", str(repo)])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["methods"] == ["GET", "POST"]
    assert data["file"] == "app/main.py"


def test_query_secrets_empty_result_says_target_is_a_path_filter(tmp_path):
    repo = _scanned_repo(tmp_path)
    result = runner.invoke(app, ["query", "secrets", "all", "--path", str(repo)])
    assert result.exit_code == 0
    assert "file path" in result.stderr and "no 'all'" in " ".join(result.stderr.split())
    help_text = runner.invoke(app, ["query", "--help"], terminal_width=200).output.replace("│", " ")
    assert "not a keyword" in " ".join(help_text.split())


def test_dependency_risk_severity_keeps_its_structure_and_case():
    from aletheore.evidence_resolution import attach_risk_evidence

    evidence = minimal_air_evidence()
    severity = [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N"}]
    evidence["security"]["dependency_vulnerabilities"]["findings"] = [
        {"package": "requests", "advisory_id": "GHSA-1", "severity": severity}
    ]
    resolution = {"kind": "dependency", "dependency": "requests", "file": None}
    risks = attach_risk_evidence(evidence, resolution)["risk"]
    assert risks[0]["severity"] == severity


def test_locate_ranks_definitions_above_bodies_that_mention_the_name(tmp_path):
    repo = _scanned_repo(tmp_path)
    result = runner.invoke(app, ["locate", "helper", str(repo)])
    assert result.exit_code == 0
    first_block = result.stdout.split("\n\n")[0]
    assert first_block.startswith("app/util.py::helper ")


# --- low items ---------------------------------------------------------------------------------------------------


def test_scan_progress_and_promo_go_to_stderr(tmp_path):
    repo = tmp_path / "p"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    result = runner.invoke(app, ["scan", str(repo), "--no-check-vulnerabilities", "--no-check-licenses"])
    assert result.exit_code == 0
    assert "Detecting languages" not in result.stdout
    assert "Scanning" not in result.stdout
    assert "GitHub App" not in result.stdout
    err = " ".join(result.stderr.split())
    assert "Detecting languages" in err and "GitHub App" in err
    assert "Evidence written to" in result.stdout


def test_conflicting_positional_path_and_path_option_is_an_error(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    result = runner.invoke(app, ["scan", str(a), "--path", str(b)])
    assert result.exit_code == 2
    assert "different folders" in result.output
    assert runner.invoke(app, ["scan", str(a), "--path", str(a)]).exit_code != 2


def test_query_module_errors_ask_for_a_repo_relative_path_and_suggest(tmp_path):
    repo = _scanned_repo(tmp_path)
    result = runner.invoke(app, ["query", "imports", "app/mian.py", "--path", str(repo)])
    assert result.exit_code == 1
    assert "repo-relative" in result.output and "app/main.py" in result.output
    result = runner.invoke(app, ["query", "cluster", "0", "--path", str(repo)])
    assert result.exit_code == 1 and "repo-relative" in result.output


def test_query_normalizes_dot_slash_and_absolute_paths(tmp_path):
    repo = _scanned_repo(tmp_path)
    for target in ("./app/main.py", str(repo / "app" / "main.py")):
        result = runner.invoke(app, ["query", "imports", target, "--path", str(repo)])
        assert result.exit_code == 0, result.output
        assert "app/util.py" in result.stdout


def test_query_missing_target_message_says_what_the_target_is(tmp_path):
    repo = _scanned_repo(tmp_path)
    result = runner.invoke(app, ["query", "imports", "--path", str(repo)])
    assert result.exit_code == 1
    assert "file path" in result.output


def test_query_warns_about_flags_that_have_no_effect_and_empty_under(tmp_path):
    repo = _scanned_repo(tmp_path)
    result = runner.invoke(
        app, ["query", "imports", "app/main.py", "--language", "python", "--k", "3", "--path", str(repo)]
    )
    assert result.exit_code == 0
    assert "--language has no effect" in result.stderr and "--k has no effect" in result.stderr
    result = runner.invoke(app, ["query", "error-handling", "--under", "nowhere", "--path", str(repo)])
    assert "no files under nowhere" in result.stderr
    result = runner.invoke(app, ["dependents", "app/util.py", str(repo), "--under", "nowhere"])
    assert "no files under nowhere" in result.stderr


def test_error_handling_aggregate_agrees_with_the_filtered_lists():
    from aletheore.scope_filter import scope_result

    result = {
        "checked": True,
        "error_types": [{"name": "MyError", "file": "src/e.py", "line": 1, "bases": []}],
        "raise_sites": [{"file": "src/a.py", "line": 3, "error_type": "MyError"}],
        "handlers": [{"file": "src/a.py", "line": 9, "catches": ["MyError"]}],
        "by_error_type": [{"name": "MyError", "defined_in": "src/e.py", "raised": 1, "caught": 1}],
    }
    scoped = scope_result("error-handling", result, [], ["tests"], False)
    assert scoped["handlers"] == [] and scoped["by_error_type"] == []


def test_query_path_that_does_not_exist_says_so(tmp_path):
    result = runner.invoke(app, ["query", "imports", "a.py", "--path", str(tmp_path / "nope")])
    assert result.exit_code == 1
    assert "path does not exist" in result.output
    assert "aletheore scan" not in result.output


def test_missing_evidence_hint_appears_once(tmp_path):
    result = runner.invoke(app, ["dependents", "x.py", str(tmp_path)])
    assert result.exit_code == 1
    assert result.output.count("aletheore scan") == 1


def test_grammar_and_capitalisation_in_report_text(tmp_path):
    repo = _scanned_repo(tmp_path)
    one = runner.invoke(app, ["dependents", "app/util.py", str(repo)])
    assert "1 file imports app/util.py" in one.stdout
    assert "1 direct importer " in one.stdout and "1 direct importers" not in one.stdout
    assert "importlib" in one.stdout  # python project keeps the python wording
    assert runner.invoke(app, ["cycles", str(repo)]).stdout.startswith("No circular")
    assert runner.invoke(app, ["tests-for", "app/util.py", str(repo)]).stdout.startswith("No test file")


def test_runtime_import_note_is_language_aware():
    from aletheore.scope_filter import runtime_import_examples

    assert "importlib" in runtime_import_examples({"python"})
    assert "importlib" not in runtime_import_examples({"go"})
    assert "importlib" not in runtime_import_examples({"typescript"})
    assert "importlib" not in runtime_import_examples({"python", "go"})


def test_locate_exits_nonzero_when_nothing_matches(tmp_path):
    repo = _scanned_repo(tmp_path)
    result = runner.invoke(app, ["locate", "zzz_nothing_here", str(repo)])
    assert result.exit_code == 1
    assert "no match" in result.stdout


def test_init_template_names_air_json():
    text = runner.invoke(app, ["init", "--help"]).output
    from pathlib import Path as _P

    source = _P(cli.__file__).read_text(encoding="utf-8")
    assert "evidence.json always keeps everything" not in source
    assert "air.json always keeps everything" in source
    assert text


def test_banner_and_picker_tables_cover_the_new_commands():
    names = [n for n, _ in cli._COMMAND_SUMMARIES]
    for command in ("index", "dependents", "cycles", "tests-for", "usages", "locate", "hook", "tokens"):
        assert command in names
    grouped = [n for _, group in cli._COMMAND_GROUPS for n in group]
    assert sorted(grouped) == sorted(names + [n for n, _ in cli._PICKER_ONLY_SUMMARIES])
    assert len(grouped) == len(set(grouped))
    description = dict(cli._COMMAND_SUMMARIES)
    for client in ("Antigravity", "Claude Desktop"):
        assert client in description["mcp-install"]
    assert "Hallbook" in description["logout"]
    assert "locate" in runner.invoke(app, ["query", "--help"], terminal_width=200).output


def test_tokens_profile_refuses_a_missing_project_directory(tmp_path):
    missing = tmp_path / "nope"
    for flag in ("--project", "--path"):
        result = runner.invoke(app, ["tokens", "profile", "core", flag, str(missing), "--yes"])
        assert result.exit_code == 1
        assert "does not exist" in result.output
    assert not missing.exists()


def test_audit_rejects_an_unknown_agent_before_scanning(tmp_path):
    with patch("aletheore.cli._scan") as scan:
        result = runner.invoke(app, ["audit", str(tmp_path), "--agent", "bogus"])
    assert result.exit_code == 2
    assert "unknown agent" in result.output
    scan.assert_not_called()


def test_watch_runs_an_initial_scan_when_the_repo_was_never_scanned(tmp_path):
    with patch("aletheore.cli._scan", return_value=(0, {}, tmp_path)) as scan, patch(
        "aletheore.watch.watch"
    ) as run_watch:
        assert cli._watch(str(tmp_path), 1.0) == 0
    scan.assert_called_once()
    run_watch.assert_called_once()


def test_watch_skips_the_initial_scan_when_evidence_exists(tmp_path):
    (tmp_path / ".aletheore").mkdir()
    (tmp_path / ".aletheore" / "air.json").write_text("{}")
    with patch("aletheore.cli._scan") as scan, patch("aletheore.watch.watch"):
        cli._watch(str(tmp_path), 1.0)
    scan.assert_not_called()


# --- L22 / L23: MCP server -------------------------------------------------------------------------------------


def _mcp_server_with_evidence(tmp_path: Path):
    from aletheore.mcp_server import build_server

    repo = _scanned_repo(tmp_path)
    return build_server(repo)


def _call(server, name, arguments):
    import anyio

    return anyio.run(lambda: server.call_tool(name, arguments))


def test_mcp_application_errors_are_flagged_is_error_but_empty_results_are_not(tmp_path):
    server = _mcp_server_with_evidence(tmp_path)
    failed = _call(server, "aletheore_imports", {"target": "nope.py"})
    assert failed.is_error is True
    assert "repo-relative" in failed.content[0].text
    found = _call(server, "aletheore_imports", {"target": "app/main.py"})
    assert not found.is_error


def test_mcp_range_errors_and_empty_search_pattern_are_clear(tmp_path):
    server = _mcp_server_with_evidence(tmp_path)
    ranged = _call(server, "aletheore_dead_code", {"limit": -5})
    assert "limit must be at least 1" in ranged.content[0].text and "section" not in ranged.content[0].text
    for pattern in ("", "   "):
        searched = _call(server, "aletheore_search", {"pattern": pattern})
        assert searched.is_error is True
        assert "must not be empty" in searched.content[0].text


def test_mcp_instructions_say_answer_needs_agent():
    from aletheore.mcp_server import SERVER_INSTRUCTIONS

    assert "only when the server was started with --agent" in SERVER_INSTRUCTIONS
