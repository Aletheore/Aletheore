"""Orchestrates one full case run: prepare checkout, invoke each tool
adapter, normalize output, run the automated grounding check, and
store raw + grounding results."""
import json
from pathlib import Path

from scripts.cases import load_case
from scripts.build_case_repo import prepare_case_checkout
from scripts.check_citations import verify_findings_against_checkout


def run_case(case_dir: Path, workdir: Path, results_dir: Path, adapters: dict, normalizers: dict) -> dict:
    case = load_case(case_dir)
    checkout_dir = prepare_case_checkout(case["repo"], case["diff_path"], workdir)

    raw_dir = Path(results_dir) / "raw" / case["case_id"]
    raw_dir.mkdir(parents=True, exist_ok=True)
    grounding_dir = Path(results_dir) / "grounding" / case["case_id"]
    grounding_dir.mkdir(parents=True, exist_ok=True)

    findings_by_tool = {}
    for tool_name, adapter in adapters.items():
        raw_output = adapter(checkout_dir, case)
        (raw_dir / f"{tool_name}.json").write_text(json.dumps(raw_output, indent=2))

        findings = normalizers[tool_name](raw_output)
        grounding = verify_findings_against_checkout(findings, checkout_dir)
        (grounding_dir / f"{tool_name}.json").write_text(json.dumps(grounding, indent=2))

        findings_by_tool[tool_name] = findings

    return {"case_id": case["case_id"], "checkout_dir": checkout_dir, "findings_by_tool": findings_by_tool}
