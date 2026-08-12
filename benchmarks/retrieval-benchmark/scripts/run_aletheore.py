import json, sys, time, statistics
from pathlib import Path
from aletheore.search_index import search_index

REPO = Path("/private/tmp/bench-flask")
qs = json.load(open("/private/tmp/bench/questions.json"))

# warm-up so the first query does not absorb one-time model/table load cost
search_index(REPO, "warm up the index and embedder", k=10)

out, lats = [], []
for q in qs:
    t0 = time.perf_counter()
    hits = search_index(REPO, q["q"], k=10)
    dt = time.perf_counter() - t0
    lats.append(dt)
    files, seen = [], set()
    for h in hits:
        mp = h.get("module_path")
        if mp and mp not in seen:
            seen.add(mp)
            files.append(mp)
    out.append({"id": q["id"], "q": q["q"], "gt": q["gt"],
                "ranked_files": files, "latency_s": dt})
    print(f"{q['id']} {dt*1000:7.1f}ms {files[:2]}", file=sys.stderr)

json.dump(out, open("/private/tmp/bench/results_aletheore.json", "w"), indent=2)
lats.sort()
print(f"\nALETHEORE latency  n={len(lats)}", file=sys.stderr)
print(f"  mean   {statistics.mean(lats)*1000:7.1f} ms", file=sys.stderr)
print(f"  median {statistics.median(lats)*1000:7.1f} ms", file=sys.stderr)
print(f"  p95    {lats[int(len(lats)*0.95)-1]*1000:7.1f} ms", file=sys.stderr)
print(f"  min    {lats[0]*1000:7.1f} ms   max {lats[-1]*1000:7.1f} ms", file=sys.stderr)
