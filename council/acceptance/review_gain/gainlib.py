"""Small helpers for the review-gain runner: file groups, caps, resuming."""
import json
import math
from pathlib import Path


def groups(files, lines, k):
    """k groups of about equal line count (greedy, biggest file first). `lines` maps file -> line count."""
    g = [[] for _ in range(k)]
    sizes = [0] * k
    for f in sorted(files, key=lambda f: -lines[f]):
        i = sizes.index(min(sizes))
        g[i].append(f)
        sizes[i] += lines[f]
    return g


def cap_for(n_files, mode, fixed=25):
    """The most findings a condition may report for `n_files` files: 1.5 per file ("per-file", so the executors' caps add up to the single agent's), or a fixed number."""
    return math.ceil(1.5 * n_files) if mode == "per-file" else fixed


def already_done(out_dir, condition):
    """A condition recorded as run, judged, and ok is not run again on resume. One whose judge failed or whose run failed is."""
    f = Path(out_dir) / "results.jsonl"
    if not f.exists():
        return False
    for line in f.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("condition") == condition and r.get("ok") and not r.get("judge_failed"):
            return True
    return False
