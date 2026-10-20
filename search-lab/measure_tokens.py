#!/usr/bin/env python3
"""Measure the context cost of skill lookup on 40 random benchmark queries.

    python3 measure_tokens.py --pilot
    python3 measure_tokens.py

Compares the character size of:
  (a) the old first step, a search result at limit 8 (the new compact row format,
      since the brief only asks for the new search row sizes), and
  (b) the one-call find_skill response, split by confidence class.
Also prints the share of queries that end in "high".
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
from pathlib import Path

import common

common.use_clean_source()

from aletheore import skill_hub  # noqa: E402
from aletheore.mcp_server import _skill_summary, _toon_result  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
BENCH = DATA / "queries_stage4.jsonl"
OUT = DATA / "token_measurements.json"
SEED = 42
SAMPLE = 40


def load_queries() -> list[dict]:
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


def summarize(values: list[int]) -> dict:
    if not values:
        return {"n": 0, "mean": 0, "median": 0, "min": 0, "max": 0}
    return {
        "n": len(values),
        "mean": round(statistics.mean(values)),
        "median": round(statistics.median(values)),
        "min": min(values),
        "max": max(values),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot", action="store_true", help="five queries, for a quick smoke test")
    args = parser.parse_args()

    queries = load_queries()
    sample_size = 5 if args.pilot else SAMPLE
    sampled = random.Random(SEED).sample(queries, min(sample_size, len(queries)))

    search_sizes: list[int] = []
    find_sizes: dict[str, list[int]] = {"high": [], "low": [], "none": []}
    details: list[dict] = []
    for position, row in enumerate(sampled, 1):
        search_rows = skill_hub.search_skills(row["query"], limit=8)
        search_text = _toon_result([_skill_summary(meta) for meta in search_rows])
        found = skill_hub.find_skill(row["query"])
        find_text = _toon_result(found)
        search_sizes.append(len(search_text))
        find_sizes[found["confidence"]].append(len(find_text))
        details.append({
            "key": row["key"],
            "query": row["query"],
            "confidence": found["confidence"],
            "search_chars": len(search_text),
            "find_chars": len(find_text),
        })
        if args.pilot or position % 10 == 0:
            print(f"  {position}/{len(sampled)}", flush=True)

    share_high = len(find_sizes["high"]) / len(sampled)
    calibration_path = DATA / "confidence_calibration.json"
    if calibration_path.exists():
        calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
        chosen = calibration["chosen"]
        print("\ncalibration table (from data/confidence_calibration.json)")
        print(f"  T1={chosen['t1']:.1f} M={chosen['gap']:.1f} R={chosen['ratio']:.2f}")
        for split in ("dev", "test", "heldout"):
            row = calibration[split]
            print(f"  {split:7s}: n={row['n']} high={row['high']} "
                  f"precision={row['precision']:.3f} coverage={row['coverage']:.3f}")

    print(f"\nqueries: {len(sampled)} (seed {SEED})")
    print("\n(a) search result, limit 8 (compact rows)")
    print(f"  {summarize(search_sizes)}")
    print("\n(b) find_skill response, by confidence class")
    for label in ("high", "low", "none"):
        print(f"  {label:4s}: {summarize(find_sizes[label])}")
    print(f"\nshare high: {share_high:.3f} ({len(find_sizes['high'])}/{len(sampled)})")

    payload = {
        "seed": SEED,
        "queries": len(sampled),
        "search_limit_8": summarize(search_sizes),
        "find_skill": {label: summarize(values) for label, values in find_sizes.items()},
        "share_high": share_high,
        "details": details,
    }
    OUT.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
