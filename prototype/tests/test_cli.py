import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from veridion.cli import main
from veridion.report import (
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
    assert ".veridion/evidence.json" in instruction


def test_run_reasoning_phase_writes_report(tmp_path):
    repo = tmp_path
    (repo / ".veridion").mkdir()
    (repo / ".veridion" / "evidence.json").write_text("{}")

    adapter = MagicMock()
    adapter.invoke.return_value = "# Audit Report\n\nfindings here\n"

    report_path = run_reasoning_phase(adapter, repo_path=str(repo), manual_dir="manual")

    written = Path(report_path)
    assert written == repo / ".veridion" / "audit-report.md"
    assert written.read_text() == "# Audit Report\n\nfindings here\n"
    adapter.invoke.assert_called_once()


def test_run_reasoning_phase_does_not_clobber_report_the_agent_wrote_itself(tmp_path):
    repo = tmp_path
    (repo / ".veridion").mkdir()
    (repo / ".veridion" / "evidence.json").write_text("{}")
    report_file = repo / ".veridion" / "audit-report.md"

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


def test_main_requires_a_command(capsys):
    with patch("sys.argv", ["veridion"]):
        with pytest.raises(SystemExit):
            main()


def test_main_audit_invokes_audit_flow(tmp_path):
    with patch("sys.argv", ["veridion", "audit", str(tmp_path), "--agent", "claude"]):
        with patch("veridion.cli._audit", return_value=0) as mock_audit:
            exit_code = main()
    assert exit_code == 0
    mock_audit.assert_called_once_with(str(tmp_path), "claude", True, True)


def test_main_audit_threads_no_check_vulnerabilities_flag(tmp_path, monkeypatch):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(
        sys,
        "argv",
        ["veridion", "audit", str(repo), "--no-check-vulnerabilities", "--agent", "nonexistent"],
    )

    main()

    evidence = json.loads((repo / ".veridion" / "evidence.json").read_text())
    assert evidence["security"]["dependency_vulnerabilities"]["checked"] is False
    assert (
        evidence["security"]["dependency_vulnerabilities"]["reason"]
        == "skipped (--no-check-vulnerabilities)"
    )


def test_main_audit_threads_no_scan_git_history_flag(tmp_path, monkeypatch):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "veridion",
            "audit",
            str(repo),
            "--no-check-vulnerabilities",
            "--no-scan-git-history",
            "--agent",
            "nonexistent",
        ],
    )

    main()

    evidence = json.loads((repo / ".veridion" / "evidence.json").read_text())
    assert evidence["security"]["secrets"]["history_scanned_commits"] == 0
    assert evidence["security"]["secrets"]["history_findings"] == []


def test_main_scan_writes_evidence_without_invoking_an_agent(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(sys, "argv", ["veridion", "scan", str(repo)])

    exit_code = main()

    assert exit_code == 0
    assert (repo / ".veridion" / "evidence.json").exists()
    captured = capsys.readouterr()
    assert "audit-report.md" not in captured.out
    assert "Running audit with" not in captured.out


def test_main_query_imports_prints_result(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    (repo / "app").mkdir()
    (repo / "app" / "config.py").write_text("SETTING = 1\n")
    (repo / "app" / "auth.py").write_text("from app import config\n")
    monkeypatch.setattr(sys, "argv", ["veridion", "scan", str(repo)])
    main()

    monkeypatch.setattr(
        sys, "argv", ["veridion", "query", "imports", "app/auth.py", "--path", str(repo)]
    )
    exit_code = main()

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "app/config.py" in captured.out


def test_main_query_ownership_does_not_require_a_target(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(sys, "argv", ["veridion", "scan", str(repo)])
    main()

    monkeypatch.setattr(sys, "argv", ["veridion", "query", "ownership", "--path", str(repo)])
    exit_code = main()

    assert exit_code == 0


def test_main_query_missing_target_errors_clearly(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(sys, "argv", ["veridion", "scan", str(repo)])
    main()

    monkeypatch.setattr(sys, "argv", ["veridion", "query", "imports", "--path", str(repo)])
    exit_code = main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "requires a target" in captured.out


def test_main_query_without_evidence_errors_clearly(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    monkeypatch.setattr(
        sys, "argv", ["veridion", "query", "imports", "app/auth.py", "--path", str(repo)]
    )

    exit_code = main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "veridion scan" in captured.out


def test_main_query_unknown_module_errors_clearly(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(sys, "argv", ["veridion", "scan", str(repo)])
    main()

    monkeypatch.setattr(
        sys, "argv", ["veridion", "query", "imports", "does/not/exist.py", "--path", str(repo)]
    )
    exit_code = main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "not present in evidence" in captured.out


def make_evidence_file(path: Path, findings: list[dict] | None = None) -> Path:
    evidence = {
        "repository": {"modules": [], "dependency_graph": {"nodes": [], "edges": []}},
        "git": {"total_commits": 0},
        "security": {
            "secrets": {
                "findings": findings or [],
                "history_scanned_commits": 0,
                "history_findings": [],
            },
            "dependency_vulnerabilities": {"checked": True, "reason": None, "findings": []},
        },
        "architecture": {"layer_violations": {"violations": []}},
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

    monkeypatch.setattr(sys, "argv", ["veridion", "diff", str(old_path), str(new_path)])
    exit_code = main()

    assert exit_code == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert len(result["secrets"]["new"]) == 1


def test_main_diff_full_flag_returns_raw_diff(tmp_path, monkeypatch, capsys):
    old_path = make_evidence_file(tmp_path / "old.json")
    new_path = make_evidence_file(tmp_path / "new.json")

    monkeypatch.setattr(sys, "argv", ["veridion", "diff", str(old_path), str(new_path), "--full"])
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
        sys, "argv", ["veridion", "diff", str(old_path), str(new_path), "--fail-on-new-secrets"]
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
        sys, "argv", ["veridion", "diff", str(old_path), str(new_path), "--fail-on-new-secrets"]
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
        ["veridion", "diff", str(old_path), str(new_path), "--full", "--fail-on-new-secrets"],
    )
    exit_code = main()

    assert exit_code == 1
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert set(result.keys()) == {"added", "removed", "changed"}


def test_main_diff_missing_file_errors_cleanly(tmp_path, monkeypatch, capsys):
    old_path = make_evidence_file(tmp_path / "old.json")
    missing_path = tmp_path / "does_not_exist.json"

    monkeypatch.setattr(sys, "argv", ["veridion", "diff", str(old_path), str(missing_path)])
    exit_code = main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "not found" in captured.out


def test_main_scan_saves_a_history_snapshot(tmp_path, monkeypatch):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(
        sys, "argv", ["veridion", "scan", str(repo), "--no-check-vulnerabilities"]
    )

    main()

    history_files = list((repo / ".veridion" / "history").glob("*.json"))
    assert len(history_files) == 1


def test_main_query_changes_reports_no_prior_snapshot_on_first_scan(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(
        sys, "argv", ["veridion", "scan", str(repo), "--no-check-vulnerabilities"]
    )
    main()

    monkeypatch.setattr(sys, "argv", ["veridion", "query", "changes", "--path", str(repo)])
    exit_code = main()

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "no prior snapshot" in captured.out


def test_main_query_changes_reports_corrupt_snapshot(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(
        sys, "argv", ["veridion", "scan", str(repo), "--no-check-vulnerabilities"]
    )
    main()
    main()

    history_dir = repo / ".veridion" / "history"
    oldest = sorted(history_dir.glob("*.json"))[0]
    oldest.write_text("{not valid json")

    monkeypatch.setattr(sys, "argv", ["veridion", "query", "changes", "--path", str(repo)])
    exit_code = main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "unreadable" in captured.out


def test_main_query_changes_shows_a_real_diff_between_two_scans(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(
        sys, "argv", ["veridion", "scan", str(repo), "--no-check-vulnerabilities"]
    )
    main()

    (repo / "second.py").write_text("y = 2\n")
    main()
    capsys.readouterr()

    monkeypatch.setattr(sys, "argv", ["veridion", "query", "changes", "--path", str(repo)])
    exit_code = main()

    assert exit_code == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["aggregate_deltas"]["module_count"] == 1


def test_main_query_changes_full_flag_returns_raw_diff(tmp_path, monkeypatch, capsys):
    repo = tmp_path
    (repo / "main.py").write_text("x = 1\n")
    monkeypatch.setattr(
        sys, "argv", ["veridion", "scan", str(repo), "--no-check-vulnerabilities"]
    )
    main()
    main()
    capsys.readouterr()

    monkeypatch.setattr(
        sys, "argv", ["veridion", "query", "changes", "--path", str(repo), "--full"]
    )
    exit_code = main()

    assert exit_code == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert set(result.keys()) == {"added", "removed", "changed"}
