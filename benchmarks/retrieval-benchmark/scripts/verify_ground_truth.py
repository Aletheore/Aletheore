import json
"""Verify every ground-truth claim against the corpus before any run.

A benchmark whose ground truth is asserted rather than checked is worthless.
This caught one real error when the set was authored: Flask 3.2 merged
``RequestContext`` into ``AppContext``, so an anchor naming the old class
pointed at a symbol that no longer existed.

Usage:  python3 verify_ground_truth.py /path/to/flask
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bench

repo = sys.argv[1] if len(sys.argv) > 1 else _bench.FLASK
qs = json.load(open(os.path.join(_bench.ROOT, "questions", "location.json")))

bad = 0
for q in qs:
    hit = False
    for f in q["ground_truth_files"]:
        p = os.path.join(repo, f)
        if not os.path.exists(p):
            print(f"MISSING FILE  {q['id']}  {f}")
            bad += 1
            continue
        if q["verification_anchor"] in open(p, errors="ignore").read():
            hit = True
    if not hit:
        print(f"ANCHOR NOT FOUND  {q['id']}  {q['verification_anchor']!r} "
              f"in {q['ground_truth_files']}")
        bad += 1

print(f"\nverified {len(qs) - bad}/{len(qs)}")
sys.exit(1 if bad else 0)
