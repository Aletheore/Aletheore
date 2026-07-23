from pathlib import Path
from typing import Callable

import aletheore.cli as _aletheore_cli
from aletheore.report import run_reasoning_phase
from scan_worker.model_tiers import writing_adapter_for_plan


def run_managed_audit(
    repo_path: Path,
    manual_dir: str | None = None,
    on_usage: Callable[[int, int], None] | None = None,
    plan: str = "indie",
) -> str:
    adapter = writing_adapter_for_plan(plan, on_usage=on_usage)
    report_path = run_reasoning_phase(
        adapter,
        str(repo_path),
        manual_dir or _aletheore_cli.MANUAL_DIR,
    )
    return Path(report_path).read_text()
