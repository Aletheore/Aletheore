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
    # Flash Review finding on this PR: the original version of this function
    # only checked for a finding at the exact (expected_path, expected_line)
    # - a true_negative case with an unrelated real false positive elsewhere
    # in the same fixture tree (a different file, or a different line in the
    # same file) would still score a clean "TN", silently understating the
    # false-positive count this benchmark exists to measure. Every finding
    # in the tree is now checked, not just the one at the expected location.
    truth = _load_ground_truth(case_dir)
    materialized = materialize_case_repo(case_dir, tmp_root / case_dir.name)
    result = find_secrets(materialized)

    # Some real credentials legitimately fire two patterns on the same line
    # (e.g. case 003's Stripe key also matches generic_credential_assignment) -
    # matches_at_location can have more than one entry, so `match` picks out
    # the specific pattern ground truth names rather than whichever happened
    # to be appended to result["findings"] last (a real bug caught while
    # fixing the finding above: an earlier version of this rewrite dropped
    # the original code's break and let a same-location second match
    # silently overwrite the first).
    expected_location = (truth["expected_path"], truth["expected_line"])
    matches_at_location = [f for f in result["findings"] if (f["path"], f["line"]) == expected_location]
    match = next((f for f in matches_at_location if f["pattern"] == truth["expected_pattern"]), None)
    unexpected_reported = [
        f for f in result["findings"] if (f["path"], f["line"]) != expected_location and not f["likely_placeholder"]
    ]

    verdict = {"case_id": truth["case_id"], "category": truth["category"]}

    if truth["category"] == "true_positive":
        if match is None:
            verdict["outcome"] = "FN"
        elif match["pattern"] != truth["expected_pattern"]:
            verdict["outcome"] = "FN"  # wrong pattern fired at that location, not a real hit
        elif match["likely_placeholder"] != truth["expected_likely_placeholder"]:
            verdict["outcome"] = "FN"  # detected but suppressed as a placeholder
        elif unexpected_reported:
            # The expected finding was correctly caught, but the fixture
            # also produced an unrelated live finding nowhere named in
            # ground truth - a real defect in the fixture or the scanner,
            # distinct from (and not papered over by) the expected hit.
            verdict["outcome"] = "TP_WITH_UNEXPECTED_FINDING"
        else:
            verdict["outcome"] = "TP"
    elif truth["category"] == "true_negative":
        if (match is not None and not match["likely_placeholder"]) or unexpected_reported:
            verdict["outcome"] = "FP"
        else:
            verdict["outcome"] = "TN"
    else:
        raise ValueError(f"unknown category {truth['category']!r} in {case_dir}")

    return verdict


def main() -> int:
    case_dirs = sorted(p for p in CASES_DIR.iterdir() if p.is_dir())
    with tempfile.TemporaryDirectory(prefix="aletheore-secrets-bench-") as tmp:
        verdicts = [_score_case(case_dir, Path(tmp)) for case_dir in case_dirs]

    tp_cases = [v for v in verdicts if v["category"] == "true_positive"]
    tn_cases = [v for v in verdicts if v["category"] == "true_negative"]
    hits = sum(1 for v in tp_cases if v["outcome"] in ("TP", "TP_WITH_UNEXPECTED_FINDING"))
    false_positives = sum(1 for v in tn_cases if v["outcome"] == "FP")
    unexpected = [v for v in verdicts if v["outcome"] == "TP_WITH_UNEXPECTED_FINDING"]

    print(f"{'case_id':<30} {'category':<16} outcome")
    for v in verdicts:
        print(f"{v['case_id']:<30} {v['category']:<16} {v['outcome']}")

    print()
    print(f"Recall (true positives detected): {hits}/{len(tp_cases)}")
    print(f"False positives (on true negatives): {false_positives}/{len(tn_cases)}")
    if unexpected:
        print(
            f"WARNING: {len(unexpected)} true-positive case(s) also produced an unrelated, "
            "unexpected live finding elsewhere in the fixture tree - see TP_WITH_UNEXPECTED_FINDING rows above."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
