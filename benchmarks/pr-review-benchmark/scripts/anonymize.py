"""Blind relabeling of tool outputs per case, so manual and LLM
scoring never see which tool produced which output. Re-randomized
independently per case so a scorer can't learn "label X is always
tool Y" across the corpus."""
import json
import random
from pathlib import Path

LABELS = ["Tool A", "Tool B", "Tool C", "Tool D"]


def assign_labels(tool_names: list[str], rng: random.Random) -> dict:
    if len(tool_names) > len(LABELS):
        raise ValueError("more tools than available labels")
    shuffled = list(tool_names)
    rng.shuffle(shuffled)
    return dict(zip(LABELS, shuffled))


def write_anonymized_case(
    case_id: str, findings_by_tool: dict, results_dir: Path, rng: random.Random
) -> dict:
    label_to_tool = assign_labels(list(findings_by_tool.keys()), rng)
    tool_to_label = {tool: label for label, tool in label_to_tool.items()}

    anon_dir = Path(results_dir) / "anon" / case_id
    anon_dir.mkdir(parents=True, exist_ok=True)
    for tool, findings in findings_by_tool.items():
        label = tool_to_label[tool]
        out_path = anon_dir / f"{label.replace(' ', '_').lower()}.json"
        out_path.write_text(json.dumps(findings, indent=2))

    sealed_dir = Path(results_dir) / "sealed"
    sealed_dir.mkdir(parents=True, exist_ok=True)
    sealed_path = sealed_dir / f"{case_id}.json"
    sealed_path.write_text(json.dumps(label_to_tool, indent=2))

    return {"anon_dir": anon_dir, "sealed_path": sealed_path}


def reveal_mapping(case_id: str, results_dir: Path) -> dict:
    sealed_path = Path(results_dir) / "sealed" / f"{case_id}.json"
    return json.loads(sealed_path.read_text())
