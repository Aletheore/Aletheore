"""Aletheore retrieval over the location question set.

Usage:  python3 scripts/run_aletheore.py
Env:    BENCH_FLASK (corpus path), BENCH_OUT (results dir)
"""
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bench

from aletheore.search_index import search_index

_bench.check_corpus_commit()
REPO = Path(_bench.FLASK)
questions = _bench.load_questions("location")

# Warm-up so the first query does not absorb one-time model/table load cost.
search_index(REPO, "warm up the index and embedder", k=10)

out, latencies = [], []
for q in questions:
    started = time.perf_counter()
    hits = search_index(REPO, q["q"], k=10)
    elapsed = time.perf_counter() - started
    latencies.append(elapsed)

    files, seen = [], set()
    for hit in hits:
        path = hit.get("module_path")
        if path and path not in seen:
            seen.add(path)
            files.append(path)

    out.append({"id": q["id"], "q": q["q"], "gt": q["gt"],
                "ranked_files": files, "latency_s": elapsed})
    print(f"{q['id']} {elapsed * 1000:7.1f}ms {files[:2]}", file=sys.stderr)

os.makedirs(_bench.OUT, exist_ok=True)
target = os.path.join(_bench.OUT, "results_aletheore.json")
json.dump(out, open(target, "w"), indent=2)

latencies.sort()
print(f"\nALETHEORE latency  n={len(latencies)}", file=sys.stderr)
print(f"  mean   {statistics.mean(latencies) * 1000:7.1f} ms", file=sys.stderr)
print(f"  median {statistics.median(latencies) * 1000:7.1f} ms", file=sys.stderr)
print(f"  p95    {latencies[int(len(latencies) * 0.95) - 1] * 1000:7.1f} ms", file=sys.stderr)
print(f"wrote {target}", file=sys.stderr)
