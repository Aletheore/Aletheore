from pathlib import Path

import aletheore.cli as _aletheore_cli
from aletheore.adapters.anthropic_native import AnthropicAdapter
from aletheore.report import run_reasoning_phase


def run_managed_audit(repo_path: Path, manual_dir: str | None = None) -> str:
    adapter = AnthropicAdapter()
    report_path = run_reasoning_phase(
        adapter,
        str(repo_path),
        manual_dir or _aletheore_cli.MANUAL_DIR,
    )
    return Path(report_path).read_text()
