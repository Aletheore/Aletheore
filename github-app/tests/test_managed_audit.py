from pathlib import Path
from unittest.mock import MagicMock

from scan_worker.managed_audit import run_managed_audit


def test_run_managed_audit_returns_report_text(tmp_path, monkeypatch):
    repo_path = tmp_path / "repo"
    (repo_path / ".aletheore").mkdir(parents=True)
    (repo_path / ".aletheore" / "evidence.toon").write_text("fake toon evidence")

    fake_adapter = MagicMock()

    def fake_run_reasoning_phase(adapter, repo_path_arg, manual_dir):
        report_path = Path(repo_path_arg) / ".aletheore" / "audit-report.md"
        report_path.write_text("# Real Report\n\nfindings here")
        return str(report_path)

    monkeypatch.setattr("scan_worker.managed_audit.AnthropicAdapter", lambda: fake_adapter)
    monkeypatch.setattr("scan_worker.managed_audit.run_reasoning_phase", fake_run_reasoning_phase)

    assert "Real Report" in run_managed_audit(repo_path)
