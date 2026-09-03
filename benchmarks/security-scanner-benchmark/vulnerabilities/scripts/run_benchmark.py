"""Runs aletheore.vulnerabilities.check_vulnerabilities against every case
in benchmarks/security-scanner-benchmark/vulnerabilities/cases/, scores
each against its ground_truth.yaml, and prints/writes recall +
false-positive numbers.

Hits the real, free, keyless OSV.dev API - the same one
aletheore_vulnerabilities calls in production. Uses a scratch cache file
(not the user's real ~/.cache/aletheore cache) so this doesn't perturb or
depend on unrelated scan state.
"""
import tempfile
from pathlib import Path

import yaml

from aletheore.vulnerabilities import check_vulnerabilities

CASES_DIR = Path(__file__).parent.parent / "cases"


def _load_ground_truth(case_dir: Path) -> dict:
    return yaml.safe_load((case_dir / "ground_truth.yaml").read_text())


def _score_case(case_dir: Path, cache_path: Path) -> dict:
    truth = _load_ground_truth(case_dir)
    result = check_vulnerabilities(case_dir / "repo", cache_path=cache_path)

    verdict = {"case_id": truth["case_id"], "category": truth["category"]}

    if not result["checked"]:
        verdict["outcome"] = "ERROR"
        verdict["detail"] = result["reason"]
        return verdict

    match = next(
        (
            f
            for f in result["findings"]
            if f["package"] == truth["package"] and f["installed_version"] == truth["version"]
        ),
        None,
    )

    if truth["category"] == "true_positive":
        verdict["outcome"] = "TP" if match else "FN"
    elif truth["category"] == "true_negative":
        verdict["outcome"] = "FP" if match else "TN"
    else:
        raise ValueError(f"unknown category {truth['category']!r} in {case_dir}")

    return verdict


def main() -> int:
    case_dirs = sorted(p for p in CASES_DIR.iterdir() if p.is_dir())
    with tempfile.TemporaryDirectory(prefix="aletheore-vuln-bench-") as tmp:
        cache_path = Path(tmp) / "vulnerability-cache.json"
        verdicts = [_score_case(case_dir, cache_path) for case_dir in case_dirs]

    tp_cases = [v for v in verdicts if v["category"] == "true_positive"]
    tn_cases = [v for v in verdicts if v["category"] == "true_negative"]
    hits = sum(1 for v in tp_cases if v["outcome"] == "TP")
    false_positives = sum(1 for v in tn_cases if v["outcome"] == "FP")
    errors = sum(1 for v in verdicts if v["outcome"] == "ERROR")

    print(f"{'case_id':<40} {'category':<16} outcome")
    for v in verdicts:
        extra = f" ({v['detail']})" if v.get("detail") else ""
        print(f"{v['case_id']:<40} {v['category']:<16} {v['outcome']}{extra}")

    print()
    print(f"Recall (true positives detected): {hits}/{len(tp_cases)}")
    print(f"False positives (on true negatives): {false_positives}/{len(tn_cases)}")
    if errors:
        print(f"OSV.dev errors (excluded from the above): {errors}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
