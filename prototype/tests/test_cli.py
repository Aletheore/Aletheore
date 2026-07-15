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
    mock_audit.assert_called_once_with(str(tmp_path), "claude", True)


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
