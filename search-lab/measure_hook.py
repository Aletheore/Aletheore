#!/usr/bin/env python3
"""Measure the UserPromptSubmit hook: hit rate, output size and subprocess wall time.

Runs hook_suggestion in-process over 200 random benchmark queries and the 30
chatty prompts in data/chatty_prompts.json, then times 20 calls to
`python -m aletheore.hallbook_hook` and (for comparison) 20 calls to
`python -m aletheore.cli hallbook suggest`, reporting median and p95.

    python3 measure_hook.py --pilot   # tiny subset, check the output first
    python3 measure_hook.py           # full run
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aletheore import skill_hub  # noqa: E402

BENCHMARK = HERE / "data" / "queries_stage4.jsonl"
CHATTY = HERE / "data" / "chatty_prompts.json"
SEED = 5
TIMED_CALLS = 20


def load_benchmark_queries(limit: int | None = None) -> list[str]:
    queries: list[str] = []
    for line in BENCHMARK.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        queries.extend(row.get("queries") or [])
    rng = random.Random(SEED)
    rng.shuffle(queries)
    return queries[:limit] if limit is not None else queries[:200]


def load_chatty() -> list[dict]:
    data = json.loads(CHATTY.read_text(encoding="utf-8"))
    rows = []
    for label in ("task", "non_task"):
        for prompt in data.get(label) or []:
            rows.append({"label": label, "prompt": prompt})
    return rows


def measure_in_process(prompts: list[str], label: str) -> dict:
    hints = []
    injects = []
    hits = 0
    for index, prompt in enumerate(prompts, 1):
        hint = skill_hub.hook_suggestion(prompt)
        inject = skill_hub.hook_suggestion(prompt, mode="inject")
        if hint is not None:
            hits += 1
        hints.append(hint)
        injects.append(inject)
        if index % 25 == 0:
            print(f"  {label}: {index}/{len(prompts)}", flush=True)

    hint_lengths = [len(value) for value in hints if value is not None]
    inject_lengths = [len(value) for value in injects if value is not None]
    return {
        "prompts": len(prompts),
        "suggestions": hits,
        "share": hits / len(prompts) if prompts else 0.0,
        "mean_hint_chars": statistics.mean(hint_lengths) if hint_lengths else 0.0,
        "mean_inject_chars": statistics.mean(inject_lengths) if inject_lengths else 0.0,
        "mean_hint_chars_all": statistics.mean(len(value) if value else 0 for value in hints) if hints else 0.0,
        "mean_inject_chars_all": statistics.mean(len(value) if value else 0 for value in injects) if injects else 0.0,
    }


def time_subprocess(prompts: list[str], module: str) -> dict:
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    durations = []
    for index, prompt in enumerate(prompts, 1):
        started = time.perf_counter()
        subprocess.run(
            [sys.executable, "-m", module, "hallbook", "suggest", "--mode", "hint"]
            if module == "aletheore.cli"
            else [sys.executable, "-m", module, "--mode", "hint"],
            input=json.dumps({"prompt": prompt, "session_id": "measure", "cwd": str(HERE)}),
            text=True,
            capture_output=True,
            cwd=HERE,
            env=env,
            timeout=120,
        )
        durations.append(time.perf_counter() - started)
        if index % 5 == 0:
            print(f"  {module}: {index}/{len(prompts)}", flush=True)
    ordered = sorted(durations)
    return {
        "calls": len(durations),
        "median_ms": statistics.median(durations) * 1000 if durations else 0.0,
        "p95_ms": ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)] * 1000 if ordered else 0.0,
        "mean_ms": statistics.mean(durations) * 1000 if durations else 0.0,
    }


def print_result(label: str, result: dict, timing: dict | None = None) -> None:
    print(f"\n{label}")
    print(f"  prompts:              {result['prompts']}")
    print(f"  suggestions:          {result['suggestions']} ({result['share']:.1%})")
    print(f"  mean hint chars:      {result['mean_hint_chars']:.0f} (over suggestions)")
    print(f"  mean inject chars:    {result['mean_inject_chars']:.0f} (over suggestions)")
    print(f"  mean hint chars all:  {result['mean_hint_chars_all']:.0f} (None counted as 0)")
    print(f"  mean inject chars all:{result['mean_inject_chars_all']:.0f} (None counted as 0)")
    if timing is not None:
        print(
            f"  subprocess ms:        median {timing['median_ms']:.0f}, "
            f"p95 {timing['p95_ms']:.0f}, mean {timing['mean_ms']:.0f} "
            f"({timing['calls']} calls)"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot", action="store_true", help="run a tiny subset first")
    args = parser.parse_args()

    benchmark = load_benchmark_queries(10 if args.pilot else None)
    chatty = load_chatty()
    chatty_prompts = [row["prompt"] for row in chatty]
    if args.pilot:
        chatty_prompts = chatty_prompts[:5]
    timed = benchmark[:TIMED_CALLS] if not args.pilot else benchmark[:3]

    print(
        f"benchmark queries: {len(benchmark)}; chatty prompts: {len(chatty_prompts)}; "
        f"timed runs: {len(timed)}",
        flush=True,
    )
    benchmark_result = measure_in_process(benchmark, "benchmark")
    chatty_result = measure_in_process(chatty_prompts, "chatty")
    hook_timing = time_subprocess(timed, "aletheore.hallbook_hook")
    cli_timing = time_subprocess(timed, "aletheore.cli")

    print_result("Benchmark (data/queries_stage4.jsonl, 200 random)", benchmark_result)
    print_result("Chatty prompts (20 task + 10 non-task)", chatty_result)
    print("\nSubprocess wall time (hint mode)")
    print(
        f"  python -m aletheore.hallbook_hook: median {hook_timing['median_ms']:.0f} ms, "
        f"p95 {hook_timing['p95_ms']:.0f} ms"
    )
    print(
        f"  python -m aletheore.cli hallbook suggest: median {cli_timing['median_ms']:.0f} ms, "
        f"p95 {cli_timing['p95_ms']:.0f} ms"
    )

    print("\nChatty prompts by label")
    for label in ("task", "non_task"):
        rows = [row for row in chatty if row["label"] == label]
        fired = [
            row["prompt"]
            for row in rows
            if skill_hub.hook_suggestion(row["prompt"]) is not None
        ]
        print(f"  {label}: {len(fired)}/{len(rows)} fire ({len(fired) / len(rows):.0%})")
        for prompt in fired:
            print(f"    fired: {prompt!r}")


if __name__ == "__main__":
    main()
