import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aletheore.report import (
    NO_AGENT_MESSAGE,
    RAW_OUTPUT_FALLBACK_NOTICE,
    NoAdapterAvailableError,
    run_reasoning_phase,
    select_adapter,
)


def _adapter(name, available=True):
    adapter = MagicMock()
    adapter.name = name
    adapter.is_available.return_value = available
    return adapter


def test_a_local_model_server_alone_is_not_offered_as_a_coding_agent(monkeypatch, capsys):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    claude, ollama = _adapter("claude", available=False), _adapter("ollama")  # the server answers, no `ollama` command
    with pytest.raises(NoAdapterAvailableError) as raised:
        select_adapter([claude, ollama], forced_name=None, interactive=True)
    assert str(raised.value) == NO_AGENT_MESSAGE
    assert "Available agent providers" not in capsys.readouterr().out  # no empty menu, no "Which one?"


def test_ollama_is_offered_once_its_command_is_installed(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/ollama" if name == "ollama" else None)
    monkeypatch.setattr("builtins.input", lambda prompt="": "1")
    ollama = _adapter("ollama")
    assert select_adapter([_adapter("claude", available=False), ollama], forced_name=None, interactive=True) is ollama


def test_only_usable_providers_are_listed_in_the_choice(monkeypatch, capsys):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    claude, ollama, codex = _adapter("claude"), _adapter("ollama"), _adapter("codex", available=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "1")
    assert select_adapter([claude, ollama, codex], forced_name=None, interactive=True) is claude
    out = capsys.readouterr().out
    assert "1. claude" in out and "ollama" not in out and "codex" not in out and "2." not in out


def test_an_explicit_agent_still_works_even_for_ollama_without_its_command(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    ollama = _adapter("ollama")
    assert select_adapter([ollama], forced_name="ollama", interactive=False) is ollama


def test_the_no_agent_message_says_what_to_install_or_configure():
    assert "no coding agent found" in NO_AGENT_MESSAGE and "PATH" in NO_AGENT_MESSAGE and "--agent" in NO_AGENT_MESSAGE


def test_fallback_notice_precedes_raw_output_when_agent_does_not_write_the_file(tmp_path):
    repo = tmp_path
    (repo / ".aletheore").mkdir()

    adapter = MagicMock()
    adapter.invoke.return_value = "The bug is at `app.py:2`."

    report_path = run_reasoning_phase(adapter, repo_path=str(repo), manual_dir="manual")

    content = Path(report_path).read_text()
    assert content == RAW_OUTPUT_FALLBACK_NOTICE + "The bug is at `app.py:2`."


def test_no_fallback_notice_when_the_agent_writes_the_report_itself(tmp_path):
    repo = tmp_path
    (repo / ".aletheore").mkdir()
    report_file = repo / ".aletheore" / "audit-report.md"

    def fake_invoke(instruction, cwd):
        report_file.write_text("# Real Report\n\nreal findings\n")
        return "done"

    adapter = MagicMock()
    adapter.invoke.side_effect = fake_invoke

    report_path = run_reasoning_phase(adapter, repo_path=str(repo), manual_dir="manual")

    content = Path(report_path).read_text()
    assert content == "# Real Report\n\nreal findings\n"
    assert "Note:" not in content
