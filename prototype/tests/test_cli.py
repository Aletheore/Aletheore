import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aletheore.cli import _ElapsedTicker, _make_progress_printer, main
from aletheore.report import (
    AmbiguousAdapterError,
    NoAdapterAvailableError,
    build_instruction,
    run_reasoning_phase,
    select_adapter,
)


def make_adapter(name: str, available: bool):
    adapter = MagicMock()
    adapter.name = name
    adapter.is_available.return_value = available
    return adapter


def test_select_adapter_returns_only_available_one():
    a = make_adapter("claude", True)
    b = make_adapter("cursor", False)
    result = select_adapter([a, b], forced_name=None, interactive=False)
    assert result is a


def test_select_adapter_raises_when_none_available():
    a = make_adapter("claude", False)
    with pytest.raises(NoAdapterAvailableError):
        select_adapter([a], forced_name=None, interactive=False)


def test_select_adapter_raises_when_multiple_and_not_interactive_and_no_flag():
    a = make_adapter("claude", True)
    b = make_adapter("cursor", True)
    with pytest.raises(AmbiguousAdapterError):
        select_adapter([a, b], forced_name=None, interactive=False)


def test_select_adapter_honors_forced_name():
    a = make_adapter("claude", True)
    b = make_adapter("cursor", True)
    result = select_adapter([a, b], forced_name="cursor", interactive=False)
    assert result is b


def test_build_instruction_references_manual_and_evidence():
    instruction = build_instruction(manual_dir="manual")
    assert "manual" in instruction
    assert ".aletheore/evidence.toon" in instruction


def test_run_reasoning_phase_writes_report(tmp_path):
    repo = tmp_path
    (repo / ".aletheore").mkdir()
    (repo / ".aletheore" / "evidence.json").write_text("{}")

    adapter = MagicMock()
    adapter.invoke.return_value = "# Audit Report\n\nfindings here\n"

    report_path = run_reasoning_phase(adapter, repo_path=str(repo), manual_dir="manual")

    written = Path(report_path)
    assert written == repo / ".aletheore" / "audit-report.md"
    assert written.read_text() == "# Audit Report\n\nfindings here\n"
    adapter.invoke.assert_called_once()


def test_run_reasoning_phase_does_not_clobber_report_the_agent_wrote_itself(tmp_path):
    repo = tmp_path
    (repo / ".aletheore").mkdir()
    (repo / ".aletheore" / "evidence.json").write_text("{}")
    report_file = repo / ".aletheore" / "audit-report.md"

    def fake_invoke(instruction, cwd):
        # Simulate an agent (e.g. Claude Code with tool access) that writes
        # the report itself via its own file tools, per the instruction, and
        # only returns a short wrap-up message as its actual return value -
        # not the report content.
        report_file.write_text("# Real Audit Report\n\nreal findings here\n")
        return "I read the manual and evidence, then wrote the audit report."

    adapter = MagicMock()
    adapter.invoke.side_effect = fake_invoke

    report_path = run_reasoning_phase(adapter, repo_path=str(repo), manual_dir="manual")

    written = Path(report_path)
    assert written.read_text() == "# Real Audit Report\n\nreal findings here\n"


def test_main_with_no_command_shows_banner_and_exits_cleanly(capsys):
    with patch("sys.argv", ["aletheore"]):
        exit_code = main()

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "ALETHEORE" in captured.out
    assert "scan" in captured.out and "audit" in captured.out


def test_main_unknown_command_still_errors(capsys):
    with patch("sys.argv", ["aletheore", "bogus-command"]):
        with pytest.raises(SystemExit):
            main()


def test_progress_printer_prints_each_distinct_phase_on_its_own_line(capsys):
    report = _make_progress_printer(is_tty=False)
    report("Detecting languages, frameworks, and build tools")
    report("Building module dependency graph (parsing source with tree-sitter)")

    captured = capsys.readouterr()
    lines = [line for line in captured.out.split("\n") if line]
    assert len(lines) == 2
    assert "Detecting languages" in lines[0]
    assert "Building module dependency graph" in lines[1]


def test_progress_printer_overwrites_repeated_license_progress_on_a_tty(capsys):
    report = _make_progress_printer(is_tty=True)
    report("Checking dependency licenses: 1/3 (flask)")
    report("Checking dependency licenses: 2/3 (requests)")
    report("Done")

    captured = capsys.readouterr()
    # both license lines share one terminal line via \r, so only two real
    # newlines appear: one closing out the in-place license line, one from "Done"
    assert captured.out.count("\n") == 2
    assert "requests" in captured.out
    assert "Done" in captured.out


def test_progress_printer_prints_every_license_line_when_not_a_tty(capsys):
    report = _make_progress_printer(is_tty=False)
    report("Checking dependency licenses: 1/3 (flask)")
    report("Checking dependency licenses: 2/3 (requests)")
    report("Done")

    captured = capsys.readouterr()
    lines = [line for line in captured.out.split("\n") if line]
    assert len(lines) == 3
    assert "flask" in lines[0]
    assert "requests" in lines[1]
    assert "Done" in lines[2]


def test_elapsed_ticker_updates_in_place_on_a_tty(capsys):
    with _ElapsedTicker("Waiting", interval=0.05, is_tty=True):
        time.sleep(0.12)

    captured = capsys.readouterr()
    assert "Waiting" in captured.out
    assert "elapsed" in captured.out


def test_elapsed_ticker_prints_start_and_done_once_when_not_a_tty(capsys):
    with _ElapsedTicker("Waiting", is_tty=False):
        pass

    captured = capsys.readouterr()
    lines = [line for line in captured.out.split("\n") if line]
    assert len(lines) == 2
    assert "Waiting..." in lines[0]
    assert "done" in lines[1]


def test_main_audit_invokes_audit_flow(tmp_path):
    with patch("sys.argv", ["aletheore", "audit", str(tmp_path), "--agent", "claude"]):
        with patch("aletheore.cli._audit", return_value=0) as mock_audit:
            exit_code = main()
    assert exit_code == 0
    mock_audit.assert_called_once_with(str(tmp_path), "claude", True, True, True, True)


def test_main_audit_threads_no_check_vulnerabilities_flag(tmp_path, monkeypatch):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(
        sys,
        "argv",
        ["aletheore", "audit", str(repo), "--no-check-vulnerabilities", "--agent", "nonexistent"],
    )

    main()

    evidence = json.loads((repo / ".aletheore" / "evidence.json").read_text())
    assert evidence["security"]["dependency_vulnerabilities"]["checked"] is False
    assert (
        evidence["security"]["dependency_vulnerabilities"]["reason"]
        == "skipped (--no-check-vulnerabilities)"
    )


def test_main_audit_threads_no_check_licenses_flag(tmp_path, monkeypatch):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(
        sys,
        "argv",
        ["aletheore", "audit", str(repo), "--no-check-licenses", "--agent", "nonexistent"],
    )

    main()

    evidence = json.loads((repo / ".aletheore" / "evidence.json").read_text())
    assert evidence["security"]["dependency_licenses"]["checked"] is False
    assert (
        evidence["security"]["dependency_licenses"]["reason"] == "skipped (--no-check-licenses)"
    )


def test_main_scan_threads_no_check_licenses_flag(tmp_path, monkeypatch):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(sys, "argv", ["aletheore", "scan", str(repo), "--no-check-licenses"])

    main()

    evidence = json.loads((repo / ".aletheore" / "evidence.json").read_text())
    assert evidence["security"]["dependency_licenses"]["checked"] is False


def test_main_scan_threads_no_map_endpoints_flag(tmp_path, monkeypatch):
    repo = tmp_path
    (repo / "app.py").write_text('@app.route("/users")\ndef list_users():\n    pass\n')
    monkeypatch.setattr(sys, "argv", ["aletheore", "scan", str(repo), "--no-map-endpoints"])

    main()

    evidence = json.loads((repo / ".aletheore" / "evidence.json").read_text())
    assert evidence["repository"]["api_endpoints"]["checked"] is False


def test_main_audit_threads_no_scan_git_history_flag(tmp_path, monkeypatch):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "aletheore",
            "audit",
            str(repo),
            "--no-check-vulnerabilities",
            "--no-scan-git-history",
            "--agent",
            "nonexistent",
        ],
    )

    main()

    evidence = json.loads((repo / ".aletheore" / "evidence.json").read_text())
    assert evidence["security"]["secrets"]["history_scanned_commits"] == 0
    assert evidence["security"]["secrets"]["history_findings"] == []


def test_main_scan_writes_evidence_without_invoking_an_agent(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(sys, "argv", ["aletheore", "scan", str(repo)])

    exit_code = main()

    assert exit_code == 0
    assert (repo / ".aletheore" / "evidence.json").exists()
    captured = capsys.readouterr()
    assert "audit-report.md" not in captured.out
    assert "Running audit with" not in captured.out


def test_main_mcp_invokes_mcp_flow(tmp_path):
    with patch("sys.argv", ["aletheore", "mcp", str(tmp_path)]):
        with patch("aletheore.cli._mcp", return_value=0) as mock_mcp:
            exit_code = main()
    assert exit_code == 0
    mock_mcp.assert_called_once_with(str(tmp_path))


def test_main_dashboard_invokes_dashboard_flow(tmp_path):
    with patch("sys.argv", ["aletheore", "dashboard", str(tmp_path)]):
        with patch("aletheore.cli._dashboard", return_value=0) as mock_dashboard:
            exit_code = main()
    assert exit_code == 0
    mock_dashboard.assert_called_once_with(str(tmp_path), 8420)


def test_main_dashboard_threads_custom_port(tmp_path):
    with patch("sys.argv", ["aletheore", "dashboard", str(tmp_path), "--port", "9000"]):
        with patch("aletheore.cli._dashboard", return_value=0) as mock_dashboard:
            exit_code = main()
    assert exit_code == 0
    mock_dashboard.assert_called_once_with(str(tmp_path), 9000)


def test_dashboard_refuses_to_start_when_port_already_bound(tmp_path, capsys):
    import socket

    from aletheore.cli import _dashboard

    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    taken_port = blocker.getsockname()[1]

    try:
        with patch("aletheore.cli.webbrowser.open") as mock_open:
            exit_code = _dashboard(str(tmp_path), taken_port)
    finally:
        blocker.close()

    assert exit_code == 1
    mock_open.assert_not_called()
    captured = capsys.readouterr()
    assert "already in use" in captured.out
    assert "Dashboard running at" not in captured.out


def test_main_healthcheck_reports_results(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    (repo / "app.py").write_text('@app.route("/health")\ndef health():\n    pass\n')
    monkeypatch.setattr(sys, "argv", ["aletheore", "scan", str(repo)])
    main()
    capsys.readouterr()

    response = MagicMock()
    response.status = 200
    response.__enter__.return_value = response
    response.__exit__.return_value = False

    monkeypatch.setattr(
        sys,
        "argv",
        ["aletheore", "healthcheck", str(repo), "--base-url", "http://localhost:5000"],
    )
    with patch("aletheore.healthcheck.urllib.request.urlopen", return_value=response):
        exit_code = main()

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "/health" in captured.out
    assert "200" in captured.out


def test_main_healthcheck_without_evidence_errors_clearly(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        ["aletheore", "healthcheck", str(tmp_path), "--base-url", "http://localhost:5000"],
    )

    exit_code = main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "aletheore scan" in captured.out


def test_main_query_imports_prints_result(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    (repo / "app").mkdir()
    (repo / "app" / "config.py").write_text("SETTING = 1\n")
    (repo / "app" / "auth.py").write_text("from app import config\n")
    monkeypatch.setattr(sys, "argv", ["aletheore", "scan", str(repo)])
    main()

    monkeypatch.setattr(
        sys, "argv", ["aletheore", "query", "imports", "app/auth.py", "--path", str(repo)]
    )
    exit_code = main()

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "app/config.py" in captured.out


def test_main_query_ownership_does_not_require_a_target(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(sys, "argv", ["aletheore", "scan", str(repo)])
    main()

    monkeypatch.setattr(sys, "argv", ["aletheore", "query", "ownership", "--path", str(repo)])
    exit_code = main()

    assert exit_code == 0


def test_main_query_missing_target_errors_clearly(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(sys, "argv", ["aletheore", "scan", str(repo)])
    main()

    monkeypatch.setattr(sys, "argv", ["aletheore", "query", "imports", "--path", str(repo)])
    exit_code = main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "requires a target" in captured.out


def test_main_query_without_evidence_errors_clearly(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    monkeypatch.setattr(
        sys, "argv", ["aletheore", "query", "imports", "app/auth.py", "--path", str(repo)]
    )

    exit_code = main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "aletheore scan" in captured.out


def test_main_query_unknown_module_errors_clearly(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(sys, "argv", ["aletheore", "scan", str(repo)])
    main()

    monkeypatch.setattr(
        sys, "argv", ["aletheore", "query", "imports", "does/not/exist.py", "--path", str(repo)]
    )
    exit_code = main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "not present in evidence" in captured.out


def make_evidence_file(
    path: Path,
    findings: list[dict] | None = None,
    vulnerabilities: list[dict] | None = None,
    layer_violations: list[dict] | None = None,
) -> Path:
    evidence = {
        "repository": {"modules": [], "dependency_graph": {"nodes": [], "edges": []}},
        "git": {"total_commits": 0},
        "security": {
            "secrets": {
                "findings": findings or [],
                "history_scanned_commits": 0,
                "history_findings": [],
            },
            "dependency_vulnerabilities": {
                "checked": True,
                "reason": None,
                "findings": vulnerabilities or [],
            },
        },
        "architecture": {"layer_violations": {"violations": layer_violations or []}},
    }
    path.write_text(json.dumps(evidence))
    return path


def test_main_diff_shows_curated_diff_between_two_files(tmp_path, monkeypatch, capsys):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(
        tmp_path / "new.json",
        findings=[
            {
                "path": "a.py",
                "pattern": "aws_access_key_id",
                "match_preview": "AKIA...MNOP",
                "likely_placeholder": False,
            }
        ],
    )

    monkeypatch.setattr(sys, "argv", ["aletheore", "diff", str(old_path), str(new_path)])
    exit_code = main()

    assert exit_code == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert len(result["secrets"]["new"]) == 1


def test_main_diff_full_flag_returns_raw_diff(tmp_path, monkeypatch, capsys):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(tmp_path / "new.json")

    monkeypatch.setattr(sys, "argv", ["aletheore", "diff", str(old_path), str(new_path), "--full"])
    exit_code = main()

    assert exit_code == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert set(result.keys()) == {"added", "removed", "changed"}


def test_main_diff_fail_on_new_secrets_exits_1_for_a_real_secret(tmp_path, monkeypatch, capsys):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(
        tmp_path / "new.json",
        findings=[
            {
                "path": "a.py",
                "pattern": "aws_access_key_id",
                "match_preview": "AKIA...MNOP",
                "likely_placeholder": False,
            }
        ],
    )

    monkeypatch.setattr(
        sys, "argv", ["aletheore", "diff", str(old_path), str(new_path), "--fail-on-new-secrets"]
    )
    exit_code = main()

    assert exit_code == 1


def test_main_diff_fail_on_new_secrets_exits_0_for_a_placeholder_only(tmp_path, monkeypatch, capsys):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(
        tmp_path / "new.json",
        findings=[
            {
                "path": "tests/fixture.py",
                "pattern": "generic_credential_assignment",
                "match_preview": "test****...cret",
                "likely_placeholder": True,
            }
        ],
    )

    monkeypatch.setattr(
        sys, "argv", ["aletheore", "diff", str(old_path), str(new_path), "--fail-on-new-secrets"]
    )
    exit_code = main()

    assert exit_code == 0


def test_main_diff_fail_on_new_secrets_exits_0_for_an_accepted_baseline_secret(
    tmp_path, monkeypatch, capsys
):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(
        tmp_path / "new.json",
        findings=[
            {
                "path": "app/aws_client.py",
                "pattern": "aws_access_key_id",
                "match_preview": "AKIA...MNOP",
                "likely_placeholder": False,
                "accepted": True,
            }
        ],
    )

    monkeypatch.setattr(
        sys, "argv", ["aletheore", "diff", str(old_path), str(new_path), "--fail-on-new-secrets"]
    )
    exit_code = main()

    assert exit_code == 0


def test_main_diff_fail_on_new_secrets_works_even_with_full_flag(tmp_path, monkeypatch, capsys):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(
        tmp_path / "new.json",
        findings=[
            {
                "path": "a.py",
                "pattern": "aws_access_key_id",
                "match_preview": "AKIA...MNOP",
                "likely_placeholder": False,
            }
        ],
    )

    monkeypatch.setattr(
        sys,
        "argv",
        ["aletheore", "diff", str(old_path), str(new_path), "--full", "--fail-on-new-secrets"],
    )
    exit_code = main()

    assert exit_code == 1
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert set(result.keys()) == {"added", "removed", "changed"}


def test_main_diff_fail_on_new_vulnerabilities_exits_1_for_a_new_vulnerability(
    tmp_path, monkeypatch, capsys
):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(
        tmp_path / "new.json",
        vulnerabilities=[
            {
                "ecosystem": "PyPI",
                "package": "requests",
                "installed_version": "2.25.0",
                "advisory_id": "GHSA-xxxx",
                "summary": "...",
                "severity": [],
            }
        ],
    )

    monkeypatch.setattr(
        sys,
        "argv",
        ["aletheore", "diff", str(old_path), str(new_path), "--fail-on-new-vulnerabilities"],
    )
    exit_code = main()

    assert exit_code == 1


def test_main_diff_fail_on_new_vulnerabilities_exits_0_with_no_new_vulnerabilities(
    tmp_path, monkeypatch, capsys
):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(tmp_path / "new.json")

    monkeypatch.setattr(
        sys,
        "argv",
        ["aletheore", "diff", str(old_path), str(new_path), "--fail-on-new-vulnerabilities"],
    )
    exit_code = main()

    assert exit_code == 0


def test_main_diff_fail_on_new_layer_violations_exits_1_for_a_new_violation(
    tmp_path, monkeypatch, capsys
):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(
        tmp_path / "new.json",
        layer_violations=[
            {
                "from": "app/routes.py",
                "to": "app/db.py",
                "reason": "inner layer 'routes' imports outer layer 'db'",
            }
        ],
    )

    monkeypatch.setattr(
        sys,
        "argv",
        ["aletheore", "diff", str(old_path), str(new_path), "--fail-on-new-layer-violations"],
    )
    exit_code = main()

    assert exit_code == 1


def test_main_diff_fail_on_new_layer_violations_exits_0_with_no_new_violations(
    tmp_path, monkeypatch, capsys
):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(tmp_path / "new.json")

    monkeypatch.setattr(
        sys,
        "argv",
        ["aletheore", "diff", str(old_path), str(new_path), "--fail-on-new-layer-violations"],
    )
    exit_code = main()

    assert exit_code == 0


def test_main_diff_fail_flags_combine_any_one_triggering_causes_exit_1(
    tmp_path, monkeypatch, capsys
):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(
        tmp_path / "new.json",
        layer_violations=[
            {
                "from": "app/routes.py",
                "to": "app/db.py",
                "reason": "inner layer 'routes' imports outer layer 'db'",
            }
        ],
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "aletheore",
            "diff",
            str(old_path),
            str(new_path),
            "--fail-on-new-secrets",
            "--fail-on-new-vulnerabilities",
            "--fail-on-new-layer-violations",
        ],
    )
    exit_code = main()

    assert exit_code == 1


def test_main_diff_missing_file_errors_cleanly(tmp_path, monkeypatch, capsys):
    old_path = make_evidence_file(tmp_path / "old.json")
    missing_path = tmp_path / "does_not_exist.json"

    monkeypatch.setattr(sys, "argv", ["aletheore", "diff", str(old_path), str(missing_path)])
    exit_code = main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "not found" in captured.out


def test_main_scan_saves_a_history_snapshot(tmp_path, monkeypatch):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(
        sys, "argv", ["aletheore", "scan", str(repo), "--no-check-vulnerabilities"]
    )

    main()

    history_files = list((repo / ".aletheore" / "history").glob("*.json"))
    assert len(history_files) == 1


def test_main_query_changes_reports_no_prior_snapshot_on_first_scan(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(
        sys, "argv", ["aletheore", "scan", str(repo), "--no-check-vulnerabilities"]
    )
    main()

    monkeypatch.setattr(sys, "argv", ["aletheore", "query", "changes", "--path", str(repo)])
    exit_code = main()

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "no prior snapshot" in captured.out


def test_main_query_changes_reports_corrupt_snapshot(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(
        sys, "argv", ["aletheore", "scan", str(repo), "--no-check-vulnerabilities"]
    )
    main()
    main()

    history_dir = repo / ".aletheore" / "history"
    oldest = sorted(history_dir.glob("*.json"))[0]
    oldest.write_text("{not valid json")

    monkeypatch.setattr(sys, "argv", ["aletheore", "query", "changes", "--path", str(repo)])
    exit_code = main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "unreadable" in captured.out


def test_main_query_changes_shows_a_real_diff_between_two_scans(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(
        sys, "argv", ["aletheore", "scan", str(repo), "--no-check-vulnerabilities"]
    )
    main()

    (repo / "second.py").write_text("y = 2\n")
    main()
    capsys.readouterr()

    monkeypatch.setattr(sys, "argv", ["aletheore", "query", "changes", "--path", str(repo)])
    exit_code = main()

    assert exit_code == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["aggregate_deltas"]["module_count"] == 1


def test_main_query_changes_full_flag_returns_raw_diff(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(
        sys, "argv", ["aletheore", "scan", str(repo), "--no-check-vulnerabilities"]
    )
    main()
    main()
    capsys.readouterr()

    monkeypatch.setattr(
        sys, "argv", ["aletheore", "query", "changes", "--path", str(repo), "--full"]
    )
    exit_code = main()

    assert exit_code == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert set(result.keys()) == {"added", "removed", "changed"}
