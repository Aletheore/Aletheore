#!/usr/bin/env python3
"""S2 lab: honest ranking measurements for Hallbook skill search.

Commands (run --pilot first for the held-out generation):

    python3 search_lab.py --eval baseline
    python3 search_lab.py --gen-heldout --pilot
    python3 search_lab.py --gen-heldout
    python3 search_lab.py --eval lab
    python3 search_lab.py --tune

The benchmark is data/queries_stage4.jsonl (flattened to one row per query).
Skills are split into DEV/TEST deterministically by md5 parity of the skill id.
The held-out set (data/queries_heldout.jsonl) is written by a different model and
is never used for tuning.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import common

common.use_clean_source()

from aletheore import skill_hub  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
BENCH = DATA / "queries_stage4.jsonl"
HELDOUT = DATA / "queries_heldout.jsonl"
WEIGHTS = DATA / "lab_weights.json"
INTENTS = DATA / "intents.json"

KS = (1, 3, 5, 10, 25)
MAX_K = max(KS)
SPLIT_CACHE: dict[str, str] = {}
NAME_CACHE: dict[str, frozenset[str]] = {}
_write_lock = threading.Lock()


# ---------------------------------------------------------------- benchmark / splits

def load_benchmark() -> list[dict]:
    rows: list[dict] = []
    for line in BENCH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        skill_id = obj.get("skill_id")
        if not skill_id:
            continue
        for i, query in enumerate(obj.get("queries") or []):
            if query and query.strip():
                rows.append({"key": f"{skill_id}|{i}", "skill_id": skill_id, "query": query})
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
            rows.append({"key": obj.get("key", ""), "skill_id": obj["skill_id"], "query": obj["query"]})
    return rows


def split_of(skill_id: str) -> str:
    cached = SPLIT_CACHE.get(skill_id)
    if cached is None:
        cached = "dev" if int(hashlib.md5(skill_id.encode()).hexdigest(), 16) % 2 == 0 else "test"
        SPLIT_CACHE[skill_id] = cached
    return cached


def split_rows(rows: list[dict], split: str) -> list[dict]:
    if split == "all":
        return rows
    return [row for row in rows if split_of(row["skill_id"]) == split]


# ---------------------------------------------------------------- acceptable duplicates

def _name_tokens(skill_id: str) -> frozenset[str]:
    from lab_rankers import tokenize

    cached = NAME_CACHE.get(skill_id)
    if cached is None:
        name = skill_id.split("/", 1)[-1].replace("-", " ").replace("_", " ")
        cached = frozenset(tokenize(name))
        NAME_CACHE[skill_id] = cached
    return cached


def build_name_index() -> None:
    for meta in skill_hub.list_bundled_skills():
        _name_tokens(meta.skill_id)


# ---------------------------------------------------------------- evaluation

def _first_hit(returned: list[str], target: str, acceptable: bool) -> int | None:
    if acceptable:
        wanted = _name_tokens(target)
        for pos, skill_id in enumerate(returned, 1):
            if _name_tokens(skill_id) == wanted:
                return pos
        return None
    for pos, skill_id in enumerate(returned, 1):
        if skill_id == target:
            return pos
    return None


def evaluate(ranker, rows: list[dict], label: str = "", verbose: bool = False) -> dict:
    """Run `ranker.rank(query, limit)` over rows and return recall@k / MRR / ms per query."""
    build_name_index()
    n = len(rows)
    if not n:
        return {"label": label, "n": 0, "strict": {}, "acceptable": {}, "ms": 0.0}
    strict_hits = {k: 0 for k in KS}
    accept_hits = {k: 0 for k in KS}
    strict_rr = 0.0
    accept_rr = 0.0
    elapsed = 0.0
    misses: list[dict] = []
    for row in rows:
        started = time.perf_counter()
        returned = ranker.rank(row["query"], MAX_K)
        elapsed += time.perf_counter() - started
        sr = _first_hit(returned, row["skill_id"], acceptable=False)
        ar = _first_hit(returned, row["skill_id"], acceptable=True)
        if sr is not None:
            strict_rr += 1.0 / sr
            for k in KS:
                if sr <= k:
                    strict_hits[k] += 1
        else:
            misses.append({"query": row["query"], "target": row["skill_id"], "returned": returned[:5]})
        if ar is not None:
            accept_rr += 1.0 / ar
            for k in KS:
                if ar <= k:
                    accept_hits[k] += 1
    result = {
        "label": label,
        "n": n,
        "strict": {k: strict_hits[k] / n for k in KS},
        "strict_mrr": strict_rr / n,
        "acceptable": {k: accept_hits[k] / n for k in KS},
        "acceptable_mrr": accept_rr / n,
        "ms": 1000.0 * elapsed / n,
    }
    if verbose:
        result["misses"] = misses[:25]
    return result


def print_table(results: list[dict], title: str = "") -> None:
    if title:
        print(f"\n{title}")
    head = f"{'ranker':<10} {'set':<7} {'n':>5} {'R@1':>6} {'R@3':>6} {'R@5':>6} {'R@10':>6} {'R@25':>6} {'MRR':>6} {'ms':>7}"
    print(head)
    print("-" * len(head))
    for res in results:
        for kind in ("strict", "acceptable"):
            hits = res[kind]
            mrr = res[f"{kind}_mrr"]
            tag = "S" if kind == "strict" else "A"
            print(
                f"{res['label']:<10} {res['set'] + ' ' + tag:<7} {res['n']:>5} "
                + " ".join(f"{hits[k]:>6.3f}" for k in KS)
                + f" {mrr:>6.3f} {res['ms']:>7.2f}"
            )
    print("(S = strict skill id, A = acceptable: same stemmed name tokens)")


# ---------------------------------------------------------------- held-out generation

HOLDOUT_SKILLS = 500
HOLDOUT_PER_SKILL = 2
HOLDOUT_SEED = 11
HOLDOUT_PILOT_SKILLS = 4
HOLDOUT_MODEL = "deepseek-v4.1-flash"
_WORD = re.compile(r"[A-Za-z0-9]+(?:['\u2019-][A-Za-z0-9]+)*")
_LEAD = re.compile(r"^\s*(?:[-*\u2022]+|\d+[.)])\s*")

# Short on purpose: the model spends its 200-token budget on reasoning when the prompt is
# long, and returns empty content. The leak rules are enforced on the output instead.
HOLDOUT_PROMPT = """Topic: {title}
Summary: {description}

One short request (6 to 18 words) a non-expert would type to an AI assistant. Do not name any product, company or tool. End with a question mark or a period."""
HOLDOUT_SYSTEM = "Reply with only the requested line."


def _skill_name_words(skill_id: str) -> list[str]:
    name = skill_id.split("/", 1)[-1].replace("_", " ").replace("-", " ")
    return [word.lower() for word in re.findall(r"[A-Za-z]+", name)]


def _leaks(text: str, forbidden: set[str]) -> bool:
    low = text.lower()
    if re.search(r"\bskills?\b", low):
        return True
    words = set(re.findall(r"[a-z]+", low))
    return bool(words & forbidden)


def _parse_holdout(text: str, forbidden: set[str], require_punct: bool = True) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = _LEAD.sub("", raw).strip().strip("\"'`\u201c\u201d\u2018\u2019").strip()
        if not line:
            continue
        if require_punct and line[-1] not in ".?!":
            continue  # cut off by the token budget
        count = len(_WORD.findall(line))
        if not (5 <= count <= 35):
            continue
        if line.split()[-1].lower() in {"and", "or", "of", "the", "to", "a", "an", "with", "for",
                                       "my", "your", "our", "that", "this", "in", "on", "at", "by",
                                       "from", "is", "are", "be", "can", "how", "what"}:
            continue  # cut off by the token budget
        if _leaks(line, forbidden):
            continue
        folded = line.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        out.append(line)
    return out


def _gen_one(skill_id: str, n: int) -> list[str]:
    skill = skill_hub.get_bundled_skill(skill_id)
    if skill is None:
        return []
    forbidden = {word for word in _skill_name_words(skill_id) if len(word) >= 5}
    forbidden_text = ", ".join(sorted(forbidden)) if forbidden else "(none)"
    prompts = [
        f"Summary: {skill.description[:220]}\n\nOne short request (6 to 18 words) a non-expert would type to an AI assistant. "
        f"Do not name any product, company or tool. Avoid these words: {forbidden_text}. "
        "End with a question mark or a period.",
        HOLDOUT_PROMPT.format(title=skill.title, description=skill.description[:180]),
        f"Topic: {skill.title}\n\nOne short request (6 to 18 words) a non-expert would type to an AI assistant. "
        f"Do not name any product, company or tool. Avoid these words: {forbidden_text}. "
        "End with a question mark or a period.",
    ]
    accepted: list[str] = []
    for _ in range(n):
        start = len(accepted)
        for require_punct in (True, False):
            for prompt in prompts:
                for _attempt in range(2):
                    raw = common.chat(prompt, model=HOLDOUT_MODEL, temperature=0.9, max_tokens=200,
                                      stage="s2", system=HOLDOUT_SYSTEM)
                    lines = [line for line in _parse_holdout(raw, forbidden, require_punct)
                             if line.casefold() not in {item.casefold() for item in accepted}]
                    if lines:
                        accepted.append(lines[0])
                        break
                if len(accepted) > start:
                    break
            if len(accepted) > start:
                break
    return accepted[:n]


def _done_holdout_keys() -> set[str]:
    keys: set[str] = set()
    if not HELDOUT.exists():
        return keys
    for line in HELDOUT.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("key"):
            keys.add(row["key"])
    return keys


def _append_holdout(row: dict) -> None:
    with _write_lock, open(HELDOUT, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def generate_heldout(pilot: bool = False) -> None:
    skills = sorted(meta.skill_id for meta in skill_hub.list_bundled_skills())
    rng = random.Random(HOLDOUT_SEED)
    chosen = sorted(rng.sample(skills, min(HOLDOUT_SKILLS, len(skills))))
    if pilot:
        chosen = chosen[:HOLDOUT_PILOT_SKILLS]
    done = _done_holdout_keys()
    todo = [skill_id for skill_id in chosen if any(f"{skill_id}|{i}" not in done for i in range(1, HOLDOUT_PER_SKILL + 1))]
    print(f"held-out {'pilot' if pilot else 'full'}: {len(todo)} skills to do, {len(done)} rows already saved", flush=True)
    if pilot:
        print("pilot skills:", ", ".join(todo), flush=True)

    failures = 0
    completed = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_gen_one, skill_id, HOLDOUT_PER_SKILL): skill_id for skill_id in todo}
        for future in as_completed(futures):
            skill_id = futures[future]
            try:
                queries = future.result()
            except Exception as exc:  # retried on the next run
                failures += 1
                print(f"  {skill_id}: {type(exc).__name__}: {exc}", flush=True)
                continue
            for i, query in enumerate(queries, 1):
                key = f"{skill_id}|{i}"
                if key in done:
                    continue
                _append_holdout({"key": key, "skill_id": skill_id, "query": query})
            completed += 1
            if pilot:
                print(f"\n{skill_id} ({len(queries)})", flush=True)
                for query in queries:
                    print(f"  - {query}", flush=True)
            elif completed % 25 == 0:
                print(f"  {completed}/{len(todo)} skills done", flush=True)
    total = len(_done_holdout_keys())
    print(f"held-out: {total} queries saved in {HELDOUT}; failures this run: {failures}", flush=True)


# ---------------------------------------------------------------- main

def _load_ranker(name: str):
    from lab_rankers import BaselineRanker, LabRanker

    if name == "baseline":
        return BaselineRanker()
    if name == "lab":
        weights = json.loads(WEIGHTS.read_text()) if WEIGHTS.exists() else None
        return LabRanker(weights=weights)
    raise SystemExit(f"unknown ranker {name!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eval", choices=["baseline", "lab", "both"], help="evaluate a ranker on the benchmark")
    parser.add_argument("--eval-heldout", choices=["baseline", "lab", "both"], help="evaluate on data/queries_heldout.jsonl")
    parser.add_argument("--gen-heldout", action="store_true", help="generate the held-out query set")
    parser.add_argument("--tune", action="store_true", help="coordinate-ascent weight tuning on DEV")
    parser.add_argument("--pilot", action="store_true", help="tiny subset for held-out generation")
    parser.add_argument("--verbose", action="store_true", help="include sample misses")
    args = parser.parse_args()

    if args.gen_heldout:
        generate_heldout(pilot=args.pilot)
        return

    bench = load_benchmark()
    if args.tune:
        from lab_rankers import tune

        dev = split_rows(bench, "dev")
        if args.pilot:
            dev = dev[:60]
            print(f"tuning pilot on {len(dev)} DEV queries", flush=True)
        weights = tune(dev, evaluate, passes=1 if args.pilot else 2)
        WEIGHTS.write_text(json.dumps(weights, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {WEIGHTS}")
        return

    if not args.eval and not args.eval_heldout:
        args.eval = "both"

    names = ["baseline", "lab"] if (args.eval or args.eval_heldout) == "both" else [args.eval or args.eval_heldout]
    rankers = {name: _load_ranker(name) for name in names}
    for name, ranker in rankers.items():
        if hasattr(ranker, "index"):
            ranker.index.ensure_loaded()

    if args.eval:
        results = []
        for name, ranker in rankers.items():
            for split in ("dev", "test", "all"):
                res = evaluate(ranker, split_rows(bench, split), label=name, verbose=args.verbose)
                res["set"] = split
                results.append(res)
        print_table(results, "BENCHMARK (data/queries_stage4.jsonl)")

    if args.eval_heldout:
        heldout = load_heldout()
        if not heldout:
            print(f"\nno held-out rows yet ({HELDOUT})")
            return
        results = []
        for name, ranker in rankers.items():
            res = evaluate(ranker, heldout, label=name, verbose=args.verbose)
            res["set"] = "heldout"
            results.append(res)
        print_table(results, "HELD-OUT (data/queries_heldout.jsonl)")


if __name__ == "__main__":
    main()
