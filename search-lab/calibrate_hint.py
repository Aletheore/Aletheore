#!/usr/bin/env python3
"""Calibrate the medium hint tier used by `skill_hub.hook_suggestion`.

Run the pilot first, check it, then the full run:

    python3 calibrate_hint.py --pilot
    python3 calibrate_hint.py

The DEV/TEST split is the same as search_lab.py and calibrate_confidence.py:
`int(md5(skill_id), 16) % 2 == 0` is DEV. data/queries_heldout.jsonl is only
reported, never used to choose the thresholds. Scores are cached in
results/hint_calibration_scores.jsonl keyed by "<set>:<query key>", so an
interrupted run resumes.

The selected pair is the loosest pair that keeps hit@2 at or above 60% on DEV and
keeps the 10 non-task chatty prompts under the 20% false-fire aim. The score grid
starts at 10 because below that the score floor does no useful work: every BM25F
match is a near-zero keyword accident. Ratios below 1.05 are excluded because a
ratio of 1.0 makes the second-place guard vacuous (the top score is always >= the
second score).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import common

common.use_clean_source()

from aletheore import skill_hub  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
BENCH = DATA / "queries_stage4.jsonl"
HELDOUT = DATA / "queries_heldout.jsonl"
CHATTY = DATA / "chatty_prompts.json"
CACHE = "hint_calibration_scores"

TARGET_HIT_AT_2 = 0.60
MAX_FALSE_FIRE = 0.20
SCORE_GRID = [float(value) for value in range(10, 41)]
RATIO_GRID = [round(1.0 + 0.05 * index, 2) for index in range(1, 21)]

_NAMES: dict[str, frozenset[str]] = {}
_HIGH: dict[str, bool] = {}


def load_high_scores() -> dict[str, bool]:
    """The strict find_skill verdict per query, from calibrate_confidence.py's cache."""
    high: dict[str, bool] = {}
    for row in common.read_jsonl("calibration_scores"):
        key = row.get("key")
        if not key:
            continue
        top = float(row.get("top_score") or 0.0)
        second = float(row.get("second_score") or 0.0)
        gap = top - second
        ratio = top / second if second > 0 else math.inf
        high[key] = top >= skill_hub.FIND_SKILL_MIN_SCORE and (
            gap >= skill_hub.FIND_SKILL_MIN_GAP or ratio >= skill_hub.FIND_SKILL_MIN_RATIO
        )
    return high


def high_for(cache_key: str, row: dict, text: str | None) -> bool:
    if cache_key in _HIGH:
        return _HIGH[cache_key]
    if text is None:
        return False
    return skill_hub.find_skill(text)["confidence"] == "high"


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


def load_chatty() -> list[dict]:
    if not CHATTY.exists():
        return []
    data = json.loads(CHATTY.read_text(encoding="utf-8"))
    rows: list[dict] = []
    for label in ("task", "non_task"):
        for index, prompt in enumerate(data.get(label) or []):
            if isinstance(prompt, str) and prompt.strip():
                rows.append({"key": f"{label}|{index}", "label": label, "query": prompt})
    return rows


def score_rows(rows: list[dict], dataset: str, pilot: bool) -> list[dict]:
    done = {row["key"]: row for row in common.read_jsonl(CACHE)}
    out: list[dict] = []
    todo = 0
    for position, row in enumerate(rows, 1):
        cache_key = f"{dataset}:{row['key']}"
        cached = done.get(cache_key)
        if cached is not None:
            cached = {**cached}
            cached["high"] = high_for(cache_key, row, cached.get("query") if cached.get("eligible") else None)
            out.append(cached)
            continue
        todo += 1
        text = skill_hub._hook_prompt_text(row["query"])
        results = skill_hub.search_skills_scored(text, limit=2, min_matches=2, prefix=False, phrase=False) if text is not None else []
        ids = [meta.skill_id for meta, _ in results]
        scores = [round(score, 6) for _, score in results]
        target = row.get("skill_id")
        hit1 = bool(target and ids and name_tokens(ids[0]) == name_tokens(target))
        hit2 = bool(target and any(name_tokens(skill_id) == name_tokens(target) for skill_id in ids[:2]))
        scored = {
            "key": cache_key,
            "dataset": dataset,
            "skill_id": target,
            "query": row["query"],
            "label": row.get("label"),
            "eligible": text is not None,
            "ids": ids,
            "scores": scores,
            "hit1": hit1,
            "hit2": hit2,
            "high": high_for(cache_key, row, text),
        }
        common.append_jsonl(CACHE, scored)
        out.append(scored)
        if todo % 100 == 0 or (pilot and todo % 10 == 0):
            print(f"  {dataset}: scored {position}/{len(rows)}", flush=True)
    return out


def is_high(row: dict) -> bool:
    """The strict find_skill bar. High rows return the one-skill hint, not medium."""
    if "high" in row:
        return bool(row["high"])
    if not row.get("scores"):
        return False
    top = row["scores"][0]
    second = row["scores"][1] if len(row["scores"]) > 1 else 0.0
    gap = top - second
    ratio = top / second if second > 0 else math.inf
    return top >= skill_hub.FIND_SKILL_MIN_SCORE and (
        gap >= skill_hub.FIND_SKILL_MIN_GAP or ratio >= skill_hub.FIND_SKILL_MIN_RATIO
    )


def fires(row: dict, score_floor: float, ratio_floor: float) -> bool:
    if not row.get("eligible") or not row.get("scores") or is_high(row):
        return False
    top = row["scores"][0]
    second = row["scores"][1] if len(row["scores"]) > 1 else 0.0
    return top >= score_floor and (second <= 0 or top >= ratio_floor * second)


def evaluate(rows: list[dict], score_floor: float, ratio_floor: float) -> dict:
    fired = [row for row in rows if fires(row, score_floor, ratio_floor)]
    hit2 = sum(row["hit2"] for row in fired) / len(fired) if fired else 0.0
    return {
        "n": len(rows),
        "fired": len(fired),
        "fire_rate": len(fired) / len(rows) if rows else 0.0,
        "hit_at_2": hit2,
    }


def choose(dev: list[dict], non_task: list[dict]) -> dict:
    candidates: list[dict] = []
    for score_floor in SCORE_GRID:
        for ratio_floor in RATIO_GRID:
            dev_result = evaluate(dev, score_floor, ratio_floor)
            if dev_result["hit_at_2"] < TARGET_HIT_AT_2:
                continue
            false_fire = evaluate(non_task, score_floor, ratio_floor)["fire_rate"]
            if false_fire >= MAX_FALSE_FIRE:
                continue
            candidates.append(
                {
                    "score": score_floor,
                    "ratio": ratio_floor,
                    "false_fire": false_fire,
                    **dev_result,
                }
            )
    if not candidates:
        raise SystemExit("no thresholds meet the DEV hit@2 target and false-fire guard")
    candidates.sort(key=lambda row: (-row["fire_rate"], row["score"], row["ratio"]))
    return candidates[0]


def report(rows: list[dict], chosen: dict, label: str) -> None:
    result = evaluate(rows, chosen["score"], chosen["ratio"])
    print(
        f"{label}: n={result['n']} fired={result['fired']} "
        f"fire_rate={result['fire_rate']:.3f} hit@2={result['hit_at_2']:.3f}"
    )


def report_chatty(chatty: list[dict], chosen: dict) -> None:
    task = [row for row in chatty if row["label"] == "task"]
    non_task = [row for row in chatty if row["label"] == "non_task"]
    print("\nchatty prompts")
    for label, rows in (("task", task), ("non_task", non_task)):
        fired = [row for row in rows if fires(row, chosen["score"], chosen["ratio"])]
        print(f"  {label}: {len(fired)}/{len(rows)} fire ({len(fired) / len(rows):.0%})")
        for row in fired:
            print(f"    fired: {row['query']!r} -> {row['ids'][:2]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot", action="store_true", help="tiny subset, for a quick smoke test")
    args = parser.parse_args()

    stage = load_stage4()
    dev_rows = [row for row in stage if split_of(row["skill_id"]) == "dev"]
    test_rows = [row for row in stage if split_of(row["skill_id"]) == "test"]
    held_rows = load_heldout()
    chatty_rows = load_chatty()

    if args.pilot:
        dev_rows, test_rows, held_rows = dev_rows[:80], test_rows[:40], held_rows[:40]
        chatty_rows = [row for row in chatty_rows if row["label"] == "task"][:5] + [
            row for row in chatty_rows if row["label"] == "non_task"
        ][:2]
        print(
            f"pilot: dev={len(dev_rows)} test={len(test_rows)} held={len(held_rows)} "
            f"chatty={len(chatty_rows)}",
            flush=True,
        )

    global _HIGH
    _HIGH = load_high_scores()
    print(f"loaded {len(_HIGH)} strict high-confidence verdicts", flush=True)
    print("scoring queries (cached rows are skipped)...", flush=True)
    dev = score_rows(dev_rows, "dev", args.pilot)
    test = score_rows(test_rows, "test", args.pilot)
    held = score_rows(held_rows, "heldout", args.pilot)
    chatty = score_rows(chatty_rows, "chatty", args.pilot)

    non_task = [row for row in chatty if row["label"] == "non_task"]
    chosen = choose(dev, non_task)

    print("\ncalibration table")
    print(f"  target hit@2 >= {TARGET_HIT_AT_2:.2f}; non-task false fire < {MAX_FALSE_FIRE:.2f}")
    print(f"  chosen: HINT_MIN_SCORE={chosen['score']:.1f} HINT_MIN_RATIO={chosen['ratio']:.2f}")
    report(dev, chosen, "  dev")
    report(test, chosen, "  test")
    report(held, chosen, "  heldout")
    report_chatty(chatty, chosen)

    if not args.pilot:
        print(
            f"\nset in skill_hub.py: HINT_MIN_SCORE = {chosen['score']:.1f}, "
            f"HINT_MIN_RATIO = {chosen['ratio']:.2f}"
        )


if __name__ == "__main__":
    main()
