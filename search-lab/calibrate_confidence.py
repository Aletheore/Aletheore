#!/usr/bin/env python3
"""Calibrate the confidence thresholds used by `skill_hub.find_skill`.

Run the pilot first, check it, then the full run:

    python3 calibrate_confidence.py --pilot
    python3 calibrate_confidence.py

Scores are cached in results/calibration_scores.jsonl keyed by "<set>:<query key>",
so an interrupted run resumes. The search benchmark is data/queries_stage4.jsonl,
flattened to one row per query. DEV/TEST split by md5 parity of the skill id,
exactly as search_lab.py does. data/queries_heldout.jsonl is only reported,
never used to choose the thresholds.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import common

common.use_clean_source()

from aletheore import skill_hub  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
BENCH = DATA / "queries_stage4.jsonl"
HELDOUT = DATA / "queries_heldout.jsonl"
OUT = DATA / "confidence_calibration.json"
CACHE = "calibration_scores"

# The brief's target: at least 85% of "high" answers must have the target (or an
# acceptable duplicate) on top. A 2.65-point safety margin keeps the chosen
# constants off the sampling-noise knife edge (one standard error on ~1,000 dev
# queries is about 1.1 points) and rejects configs whose dev precision is only
# barely at the target. When coverage ties, prefer the higher absolute score floor.
TARGET_PRECISION = 0.85
SAFETY_MARGIN = 0.0265
MIN_DEV_PRECISION = TARGET_PRECISION + SAFETY_MARGIN

JUNK = ("help me", "do the thing", "fix it", "make it better")

_NAMES: dict[str, frozenset[str]] = {}


def name_tokens(skill_id: str) -> frozenset[str]:
    """The stemmed tokens of a skill's own name, for acceptable duplicates."""
    cached = _NAMES.get(skill_id)
    if cached is None:
        name = skill_id.split("/", 1)[-1].replace("-", " ").replace("_", " ")
        cached = frozenset(skill_hub._tokens(name))
        _NAMES[skill_id] = cached
    return cached


def split_of(skill_id: str) -> str:
    return "dev" if int(hashlib.md5(skill_id.encode()).hexdigest(), 16) % 2 == 0 else "test"


def load_stage4() -> list[dict]:
    rows: list[dict] = []
    for line in BENCH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        skill_id = obj.get("skill_id")
        if not skill_id:
            continue
        for index, query in enumerate(obj.get("queries") or []):
            if query and query.strip():
                rows.append({"key": f"{skill_id}|{index}", "skill_id": skill_id, "query": query})
    return rows


def load_heldout() -> list[dict]:
    rows: list[dict] = []
    if not HELDOUT.exists():
        return rows
    for line in HELDOUT.read_text(encoding="utf-8").splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("skill_id") and obj.get("query"):
            rows.append({"key": obj.get("key") or obj["skill_id"], "skill_id": obj["skill_id"], "query": obj["query"]})
    return rows


def score_rows(rows: list[dict], dataset: str, pilot: bool) -> list[dict]:
    done = {row["key"]: row for row in common.read_jsonl(CACHE)}
    out: list[dict] = []
    todo = 0
    for position, row in enumerate(rows, 1):
        cache_key = f"{dataset}:{row['key']}"
        cached = done.get(cache_key)
        if cached is not None:
            out.append(cached)
            continue
        todo += 1
        results = skill_hub.search_skills_scored(row["query"], limit=5)
        top_score = results[0][1] if results else 0.0
        second_score = results[1][1] if len(results) > 1 else 0.0
        correct = bool(results) and name_tokens(results[0][0].skill_id) == name_tokens(row["skill_id"])
        scored = {
            "key": cache_key,
            "dataset": dataset,
            "skill_id": row["skill_id"],
            "query": row["query"],
            "top_score": round(top_score, 6),
            "second_score": round(second_score, 6),
            "correct": correct,
        }
        common.append_jsonl(CACHE, scored)
        out.append(scored)
        if todo % 100 == 0 or (pilot and todo % 10 == 0):
            print(f"  {dataset}: scored {position}/{len(rows)}", flush=True)
    return out


def evaluate(rows: list[dict], t1: float, gap: float, ratio: float) -> dict:
    high = [
        row for row in rows
        if row["top_score"] >= t1
        and (row["top_score"] - row["second_score"] >= gap
             or (row["second_score"] > 0 and row["top_score"] / row["second_score"] >= ratio))
    ]
    coverage = len(high) / len(rows) if rows else 0.0
    precision = sum(row["correct"] for row in high) / len(high) if high else 0.0
    return {"n": len(rows), "high": len(high), "coverage": coverage, "precision": precision}


def search(dev: list[dict]) -> tuple[dict, list[dict]]:
    candidates: list[dict] = []
    t1_values = [float(v) for v in range(20, 71, 1)]
    gap_values = [float(v) for v in range(0, 21, 1)]
    ratio_values = [1.0 + 0.05 * i for i in range(31)]
    for t1 in t1_values:
        for gap in gap_values:
            for ratio in ratio_values:
                result = evaluate(dev, t1, gap, ratio)
                if result["precision"] >= MIN_DEV_PRECISION:
                    candidates.append({"t1": t1, "gap": gap, "ratio": round(ratio, 2), **result})
    if not candidates:
        return {}, []
    candidates.sort(
        key=lambda row: (-row["coverage"], -row["t1"], -row["precision"], row["gap"], row["ratio"])
    )
    return candidates[0], candidates[:10]


def report(rows: list[dict], chosen: dict, label: str) -> None:
    result = evaluate(rows, chosen["t1"], chosen["gap"], chosen["ratio"])
    print(
        f"{label}: n={result['n']} high={result['high']} "
        f"precision={result['precision']:.3f} coverage={result['coverage']:.3f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot", action="store_true", help="tiny subset, for a quick smoke test")
    args = parser.parse_args()

    stage = load_stage4()
    dev_rows = [row for row in stage if split_of(row["skill_id"]) == "dev"]
    test_rows = [row for row in stage if split_of(row["skill_id"]) == "test"]
    held_rows = load_heldout()

    if args.pilot:
        dev_rows, test_rows, held_rows = dev_rows[:80], test_rows[:40], held_rows[:40]
        print(f"pilot: dev={len(dev_rows)} test={len(test_rows)} held={len(held_rows)}", flush=True)

    print("scoring queries (cached rows are skipped)...", flush=True)
    dev = score_rows(dev_rows, "dev", args.pilot)
    test = score_rows(test_rows, "test", args.pilot)
    held = score_rows(held_rows, "heldout", args.pilot)

    chosen, top = search(dev)
    if not chosen:
        raise SystemExit("no thresholds reach the required precision; widen the grid")

    print("\ncalibration table")
    print(f"  target precision >= {TARGET_PRECISION:.2f}; selection requires dev precision >= "
          f"{MIN_DEV_PRECISION:.3f} (a {SAFETY_MARGIN:.3f} safety margin)")
    print(f"  chosen: T1={chosen['t1']:.1f} M={chosen['gap']:.1f} R={chosen['ratio']:.2f}")
    report(dev, chosen, "  dev")
    report(test, chosen, "  test")
    report(held, chosen, "  heldout")

    print("\n  near-best dev configs (coverage, precision, T1, M, R):")
    for row in top[:8]:
        print(f"    {row['coverage']:.3f} {row['precision']:.3f} "
              f"{row['t1']:.1f} {row['gap']:.1f} {row['ratio']:.2f}")

    print("\n  vague junk (must be none or low):")
    for query in JUNK:
        result = skill_hub.find_skill(query)
        print(f"    {query!r}: {result['confidence']}")

    payload = {
        "target_precision": TARGET_PRECISION,
        "min_dev_precision": MIN_DEV_PRECISION,
        "chosen": {key: chosen[key] for key in ("t1", "gap", "ratio")},
        "dev": evaluate(dev, chosen["t1"], chosen["gap"], chosen["ratio"]),
        "test": evaluate(test, chosen["t1"], chosen["gap"], chosen["ratio"]),
        "heldout": evaluate(held, chosen["t1"], chosen["gap"], chosen["ratio"]),
        "pilot": args.pilot,
    }
    if not args.pilot:
        OUT.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
        print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
