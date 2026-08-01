from pathlib import Path
from unittest.mock import MagicMock

from aletheore.report import RAW_OUTPUT_FALLBACK_NOTICE, run_reasoning_phase


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
