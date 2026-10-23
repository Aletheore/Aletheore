"""Measure Hallbook search quality on the held-out queries and the chatty prompts.

    python benchmarks/hallbook-search/eval.py                 # all queries, prints metrics
    python benchmarks/hallbook-search/eval.py --split dev     # tune on this half only
    python benchmarks/hallbook-search/eval.py --split test    # report on this half, never tune on it
    python benchmarks/hallbook-search/eval.py --limit 200     # quick look
    python benchmarks/hallbook-search/eval.py --write FILE    # save the metrics as JSON
    python benchmarks/hallbook-search/eval.py --compare FILE  # fail if anything got worse

The split is by skill (crc32 of the id), so a skill's queries never straddle dev and test.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import random
import re
import sys
import zlib
from pathlib import Path

os.environ.setdefault("ALETHEORE_CRASH_REPORTING", "0")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))

from aletheore import skill_hub  # noqa: E402


def split_of(skill_id: str) -> str:
    return "dev" if zlib.crc32(skill_id.encode()) % 2 == 0 else "test"


def load_queries(split: str) -> list[dict]:
    rows = [json.loads(line) for line in (HERE / "queries_heldout.jsonl").read_text(encoding="utf-8").splitlines() if line]
    return [r for r in rows if split == "all" or split_of(r["skill_id"]) == split]


_WORD = re.compile(r"[A-Za-z]{5,}")


def _perturb_word(word: str, rng: random.Random) -> str | None:
    """One seeded typo in a word, or None when no safe edit is possible.

    The first letter is kept, because the fuzzy matcher is deliberately restricted
    to the same first letter, so a typo there is outside what it can recover.
    """
    lower = word.lower()
    if lower in skill_hub._STOPWORDS:
        return None
    operations = ["transpose", "drop", "double"]
    rng.shuffle(operations)
    for operation in operations:
        if operation == "transpose":
            spots = [i for i in range(1, len(word) - 1) if word[i] != word[i + 1]]
            if spots:
                i = rng.choice(spots)
                return word[:i] + word[i + 1] + word[i] + word[i + 2:]
        elif operation == "drop":
            spots = list(range(1, len(word)))
            if spots:
                i = rng.choice(spots)
                return word[:i] + word[i + 1:]
        else:
            spots = list(range(1, len(word)))
            if spots:
                i = rng.choice(spots)
                return word[:i] + word[i] + word[i:]
    return None


def perturb_query(query: str) -> str | None:
    """The query with one content word mistyped, deterministically for the same text."""
    rng = random.Random(zlib.crc32(query.encode("utf-8")))
    matches = list(_WORD.finditer(query))
    rng.shuffle(matches)
    for match in matches:
        changed = _perturb_word(match.group(0), rng)
        if changed is not None:
            return query[: match.start()] + changed + query[match.end():]
    return None


def _top3_hit(row: dict, query: str) -> bool:
    result = skill_hub.find_skill(query)
    ids = [result["top"]["skill_id"]] if result.get("top") else []
    ids += [alt["skill_id"] for alt in result.get("alternatives") or []]
    return row["skill_id"] in ids[:3]


def measure(split: str = "all", limit: int | None = None) -> dict:
    rows = load_queries(split)[:limit]
    hits = {1: 0, 3: 0, 5: 0}
    by_conf: collections.Counter = collections.Counter()
    right_by_conf: collections.Counter = collections.Counter()
    for row in rows:
        result = skill_hub.find_skill(row["query"])
        ids = [result["top"]["skill_id"]] if result.get("top") else []
        ids += [alt["skill_id"] for alt in result.get("alternatives") or []]
        conf = result.get("confidence", "none")
        by_conf[conf] += 1
        right_by_conf[conf] += row["skill_id"] == (ids[0] if ids else None)
        for k in hits:
            hits[k] += row["skill_id"] in ids[:k]
    typo_rows = [(row, perturbed) for row in rows if (perturbed := perturb_query(row["query"])) is not None]
    typo_clean_hits = sum(_top3_hit(row, row["query"]) for row, _ in typo_rows)
    typo_hits = sum(_top3_hit(row, perturbed) for row, perturbed in typo_rows)
    typos = len(typo_rows)
    chatty = json.loads((HERE / "chatty_prompts.json").read_text(encoding="utf-8"))
    non_task_quiet = sum(skill_hub.find_skill(q).get("confidence") in ("none", "low") for q in chatty["non_task"])
    non_task_none = sum(skill_hub.find_skill(q).get("confidence") == "none" for q in chatty["non_task"])
    n = len(rows) or 1
    return {
        "split": split,
        "queries": len(rows),
        "top1": round(hits[1] / n, 4),
        "top3": round(hits[3] / n, 4),
        "top5": round(hits[5] / n, 4),
        "typo_queries": typos,
        "typo_clean_top3": round(typo_clean_hits / typos, 4) if typos else None,
        "typo_top3": round(typo_hits / typos, 4) if typos else None,
        "confidence": dict(by_conf),
        "high_precision": round(right_by_conf["high"] / by_conf["high"], 4) if by_conf["high"] else None,
        "wrong_high": by_conf["high"] - right_by_conf["high"],
        "non_task_prompts": len(chatty["non_task"]),
        "non_task_quiet": non_task_quiet,
        "non_task_none": non_task_none,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", choices=("all", "dev", "test"), default="all")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--write")
    parser.add_argument("--compare")
    args = parser.parse_args()
    metrics = measure(args.split, args.limit)
    print(json.dumps(metrics, indent=2))
    if args.write:
        Path(args.write).write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    if args.compare:
        base = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        worse = [
            key
            for key in ("top1", "top3", "top5", "typo_top3", "high_precision", "non_task_quiet")
            if (metrics[key] or 0) < (base[key] or 0) - 1e-9
        ]
        if metrics["wrong_high"] > base["wrong_high"]:
            worse.append("wrong_high")
        if worse:
            print("WORSE than baseline:", ", ".join(worse))
            return 1
        print("not worse than baseline on any metric")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
