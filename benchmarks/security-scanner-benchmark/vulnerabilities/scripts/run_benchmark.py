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

from aletheore.vulnerabilities import DEFAULT_TIMEOUT_SECONDS, _fetch_vuln_detail, check_vulnerabilities

CASES_DIR = Path(__file__).parent.parent / "cases"

_OSV_ALIAS_CACHE: dict[str, list[str]] = {}


def _osv_aliases(advisory_id: str) -> list[str]:
    # Flash Review finding on this PR: the original matcher only checked
    # package name + installed version, ignoring ground_truth's cve_id
    # entirely - for a package/version with multiple advisories (real for
    # both cve_id cases here: log4j-core 2.14.1 has 7 distinct OSV entries,
    # lodash 4.17.15 has 5), ANY of them would score a true positive, not
    # necessarily the specific CVE the case is meant to test for.
    # check_vulnerabilities()'s own finding dict doesn't carry `aliases`
    # (only advisory_id, summary, severity), and OSV's advisory_id for a
    # GitHub-sourced entry is its own GHSA id, not the CVE - confirmed
    # directly: log4j-core 2.14.1's Log4Shell entry is GHSA-jfh8-c2jp-5v3q
    # with CVE-2021-44228 only present as an alias. Reuses
    # aletheore.vulnerabilities' own _fetch_vuln_detail (same OSV vuln-detail
    # endpoint check_vulnerabilities itself calls) rather than a second,
    # separately-written urllib call - that would have had to rediscover the
    # certifi CA-bundle fix _fetch_vuln_detail's own module docstring already
    # explains was needed for this exact call on macOS. Cached per
    # advisory_id since a case can re-query the same id.
    if advisory_id not in _OSV_ALIAS_CACHE:
        detail = _fetch_vuln_detail(advisory_id, DEFAULT_TIMEOUT_SECONDS)
        _OSV_ALIAS_CACHE[advisory_id] = [detail.get("id", "")] + list(detail.get("aliases", []))
    return _OSV_ALIAS_CACHE[advisory_id]


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

    candidates = [
        f for f in result["findings"] if f["package"] == truth["package"] and f["installed_version"] == truth["version"]
    ]

    cve_id = truth.get("cve_id")
    if cve_id:
        # A specific CVE is under test - at least one candidate advisory
        # must actually alias it, not just share the package/version.
        match = next((f for f in candidates if cve_id in _osv_aliases(f["advisory_id"])), None)
    else:
        # No specific CVE named (both current cve_id: null cases document
        # why: several advisories apply, or the advisory shape varies) -
        # package/version presence is the documented, intentional check.
        match = candidates[0] if candidates else None

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
