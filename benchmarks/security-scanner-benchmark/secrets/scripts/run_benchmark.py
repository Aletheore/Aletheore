"""Runs aletheore.secrets.find_secrets against every case in
benchmarks/security-scanner-benchmark/secrets/cases/, scores each against
its ground_truth.yaml, and prints/writes recall + false-positive numbers.

A case's repo/ tree is materialized (placeholders expanded) into a
tempdir before scanning - see fixtures.py's docstring for why the
placeholders exist in the first place. Nothing here mutates the corpus.
"""
import sys
import tempfile
from pathlib import Path

import yaml

from aletheore.secrets import find_secrets

sys.path.insert(0, str(Path(__file__).parent))
from fixtures import materialize_case_repo  # noqa: E402

CASES_DIR = Path(__file__).parent.parent / "cases"


def _load_ground_truth(case_dir: Path) -> dict:
    return yaml.safe_load((case_dir / "ground_truth.yaml").read_text())


def _score_case(case_dir: Path, tmp_root: Path) -> dict:
    truth = _load_ground_truth(case_dir)
    materialized = materialize_case_repo(case_dir, tmp_root / case_dir.name)
    result = find_secrets(materialized)

    match = None
    for finding in result["findings"]:
        if finding["path"] == truth["expected_path"] and finding["line"] == truth["expected_line"]:
            match = finding
            break

    verdict = {"case_id": truth["case_id"], "category": truth["category"]}

    if truth["category"] == "true_positive":
        if match is None:
            verdict["outcome"] = "FN"
        elif match["pattern"] != truth["expected_pattern"]:
            verdict["outcome"] = "FN"  # wrong pattern fired at that location, not a real hit
        elif match["likely_placeholder"] != truth["expected_likely_placeholder"]:
            verdict["outcome"] = "FN"  # detected but suppressed as a placeholder
        else:
            verdict["outcome"] = "TP"
    elif truth["category"] == "true_negative":
        if match is None:
            verdict["outcome"] = "TN"
        elif match["likely_placeholder"]:
            verdict["outcome"] = "TN"  # matched a pattern but correctly flagged as placeholder
        else:
            verdict["outcome"] = "FP"
    else:
        raise ValueError(f"unknown category {truth['category']!r} in {case_dir}")

    return verdict


def main() -> int:
    case_dirs = sorted(p for p in CASES_DIR.iterdir() if p.is_dir())
    with tempfile.TemporaryDirectory(prefix="aletheore-secrets-bench-") as tmp:
        verdicts = [_score_case(case_dir, Path(tmp)) for case_dir in case_dirs]

    tp_cases = [v for v in verdicts if v["category"] == "true_positive"]
    tn_cases = [v for v in verdicts if v["category"] == "true_negative"]
    hits = sum(1 for v in tp_cases if v["outcome"] == "TP")
    false_positives = sum(1 for v in tn_cases if v["outcome"] == "FP")

    print(f"{'case_id':<30} {'category':<16} outcome")
    for v in verdicts:
        print(f"{v['case_id']:<30} {v['category']:<16} {v['outcome']}")

    print()
    print(f"Recall (true positives detected): {hits}/{len(tp_cases)}")
    print(f"False positives (on true negatives): {false_positives}/{len(tn_cases)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
