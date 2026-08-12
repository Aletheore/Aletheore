import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bench, os, subprocess, sys, time, statistics

REPO = _bench.FLASK_RW
MODE = sys.argv[1] if len(sys.argv) > 1 else "semantic"

env = dict(os.environ)
env.update({
    "COLUMNS": "300", "TERM": "dumb",
    # Without these, _resolve_embedder(None) returns the 8-dim MockEmbedder,
    # LanceDB raises on the dimension mismatch, and search_cmd's bare
    # `except Exception: pass` silently degrades semantic mode into FTS.
    "REPOWISE_EMBEDDER": "ollama",
    "OLLAMA_BASE_URL": "http://localhost:11434",
    "REPOWISE_EMBEDDING_MODEL": "nomic-embed-text",
    "REPOWISE_EMBEDDING_DIMS": "768",
})
for line in open(_bench.ENV_FILE):
    line = line.strip()
    if "=" in line:
        k, v = line.split("=", 1)
        env[k] = v


def parse(out):
    """Pull the Path column out of the rich table, in rank order."""
    files, seen = [], set()
    for ln in out.splitlines():
        if not ln.startswith("│"):
            continue
        cols = [c.strip() for c in ln.split("│")]
        if len(cols) < 6:
            continue
        path = cols[4]
        if not path or path == "Path":
            continue
        path = path.split("::")[0]          # symbol pages -> their file
        if path not in seen:
            seen.add(path)
            files.append(path)
    return files


qs = _bench.load_questions("location")
# warm-up
subprocess.run(["repowise", "search", "warm up", "--mode", MODE, "--limit", "5"],
               cwd=REPO, env=env, capture_output=True, text=True, timeout=300)

out, lats = [], []
for q in qs:
    t0 = time.perf_counter()
    r = subprocess.run(
        ["repowise", "search", q["q"], "--mode", MODE, "--limit", "10"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=300,
    )
    dt = time.perf_counter() - t0
    lats.append(dt)
    files = parse(r.stdout)
    out.append({"id": q["id"], "q": q["q"], "gt": q["gt"],
                "ranked_files": files, "latency_s": dt})
    print(f"{q['id']} {dt*1000:7.1f}ms {files[:2]}", file=sys.stderr)

json.dump(out, open(os.path.join(_bench.OUT, f"results_repowise_{MODE}.json"), "w"), indent=2)
lats.sort()
print(f"\nREPOWISE[{MODE}] latency n={len(lats)}", file=sys.stderr)
print(f"  mean   {statistics.mean(lats)*1000:7.1f} ms", file=sys.stderr)
print(f"  median {statistics.median(lats)*1000:7.1f} ms", file=sys.stderr)
print(f"  p95    {lats[int(len(lats)*0.95)-1]*1000:7.1f} ms", file=sys.stderr)
print(f"  min    {lats[0]*1000:7.1f} ms   max {lats[-1]*1000:7.1f} ms", file=sys.stderr)
