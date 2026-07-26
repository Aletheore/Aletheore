"""Builds the final cross-case scorecard from manual and LLM-judge
scoring records. Runs after both scoring passes are complete for
every case; grounding_rate (Task 4's automated, non-blind check) is
merged in separately at report-build time, keyed by real tool name
since it needs no blinding."""
import json
from pathlib import Path
import yaml


def load_manual_scores(results_dir: Path) -> dict:
    scores = {}
    for path in sorted((Path(results_dir) / "scored").glob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        scores[data["case_id"]] = data["scores"]
    return scores


def load_llm_scores(results_dir: Path) -> dict:
    scores = {}
    for path in sorted((Path(results_dir) / "llm_judged").glob("*.json")):
        scores[path.stem] = json.loads(path.read_text())
    return scores


def build_scorecard(manual_scores: dict, llm_scores: dict) -> dict:
    per_tool = {}
    agreement_counts = {"recall": 0, "actionability": 0}
    compared_counts = {"recall": 0, "actionability": 0}

    for case_id, case_scores in manual_scores.items():
        llm_case_scores = llm_scores.get(case_id, {})
        for label, manual in case_scores.items():
            bucket = per_tool.setdefault(label, {
                "hit": 0, "partial": 0, "miss": 0,
                "false_positive_count": 0,
                "actionability_total": 0, "actionability_count": 0,
            })
            recall = manual.get("recall")
            if recall in ("hit", "partial", "miss"):
                bucket[recall] += 1
            bucket["false_positive_count"] += len(manual.get("false_positives") or [])
            if manual.get("actionability") is not None:
                bucket["actionability_total"] += manual["actionability"]
                bucket["actionability_count"] += 1

            llm = llm_case_scores.get(label)
            if llm is not None:
                if "recall" in llm:
                    compared_counts["recall"] += 1
                    if llm["recall"] == recall:
                        agreement_counts["recall"] += 1
                if "actionability" in llm:
                    compared_counts["actionability"] += 1
                    if llm["actionability"] == manual.get("actionability"):
                        agreement_counts["actionability"] += 1

    human_llm_agreement = {}
    for dimension, compared in compared_counts.items():
        human_llm_agreement[dimension] = (
            agreement_counts[dimension] / compared if compared else None
        )

    return {"per_tool": per_tool, "human_llm_agreement": human_llm_agreement}
