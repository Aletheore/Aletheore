"""Generates a blank manual/LLM scoring template for a case, keyed
only by anonymized label -- never by real tool name."""
from pathlib import Path
import yaml


def build_blank_scorecard(case_id: str, labels: list[str]) -> dict:
    return {
        "case_id": case_id,
        "scores": {
            label: {"recall": None, "false_positives": [], "actionability": None}
            for label in labels
        },
    }


def write_blank_scorecard(case_id: str, labels: list[str], out_path: Path) -> None:
    Path(out_path).write_text(
        yaml.safe_dump(build_blank_scorecard(case_id, labels), sort_keys=False)
    )
