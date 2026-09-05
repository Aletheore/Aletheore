"""Runs aletheore's dead-code detection (find_dead_code, via a full
scan_repository()) against every case in ../cases/, scores each against
its ground_truth.yaml, and prints recall + false-positive numbers.

Real end-to-end scans, not mocked - scan_repository() does the same
parsing/import-graph work `aletheore scan` does, with vulnerabilities/
licenses/git-history/endpoint-mapping disabled (irrelevant to dead code,
and slow) the same way watch.py's rebuild() does for its own fast-path
scans.
"""
from pathlib import Path

import yaml

from aletheore.evidence import scan_repository

CASES_DIR = Path(__file__).parent.parent / "cases"


def _load_ground_truth(case_dir: Path) -> dict:
    return yaml.safe_load((case_dir / "ground_truth.yaml").read_text())


def _score_case(case_dir: Path) -> dict:
    truth = _load_ground_truth(case_dir)
    evidence = scan_repository(
        case_dir / "repo",
        check_vulnerabilities=False,
        scan_git_history=False,
        check_licenses=False,
        map_endpoints=False,
        map_schema=False,
    )
    dead_code = evidence["repository"]["dead_code"]

    verdict = {"case_id": truth["case_id"], "category": truth["category"]}

    if truth["finding_type"] == "unreachable_module":
        flagged_paths = {m["path"] for m in dead_code["unreachable_modules"]}
        is_flagged = truth["expected_path"] in flagged_paths
    elif truth["finding_type"] == "unused_dependency":
        flagged = {
            (d["ecosystem"], d["package"]) for d in dead_code["unused_dependencies"]
        }
        is_flagged = (truth["expected_ecosystem"], truth["expected_package"]) in flagged
    else:
        raise ValueError(f"unknown finding_type {truth['finding_type']!r} in {case_dir}")

    if truth["category"] == "true_positive":
        verdict["outcome"] = "TP" if is_flagged else "FN"
    else:
        verdict["outcome"] = "FP" if is_flagged else "TN"

    return verdict


def main() -> int:
    case_dirs = sorted(p for p in CASES_DIR.iterdir() if p.is_dir())
    verdicts = [_score_case(case_dir) for case_dir in case_dirs]

    tp_cases = [v for v in verdicts if v["category"] == "true_positive"]
    tn_cases = [v for v in verdicts if v["category"] == "true_negative"]
    hits = sum(1 for v in tp_cases if v["outcome"] == "TP")
    false_positives = sum(1 for v in tn_cases if v["outcome"] == "FP")

    print(f"{'case_id':<45} {'category':<16} outcome")
    for v in verdicts:
        print(f"{v['case_id']:<45} {v['category']:<16} {v['outcome']}")

    print()
    print(f"Recall (true positives detected): {hits}/{len(tp_cases)}")
    print(f"False positives (on true negatives): {false_positives}/{len(tn_cases)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
