import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bench, os, sys

FLASK = _bench.FLASK


def is_real_file(p):
    return os.path.exists(os.path.join(FLASK, p))


def score(path, label, filt=False):
    res = json.load(open(path))
    tot = len(res)
    rows = []
    for r in res:
        f = r["ranked_files"]
        if filt:
            f = [x for x in f if is_real_file(x)]
        rows.append((r["id"], f, r["gt"]))
    print(f"\n{label}{' [non-file pages dropped]' if filt else ''}  (n={tot})")
    for k in (1, 3, 5):
        h = sum(1 for _, f, gt in rows if any(x in gt for x in f[:k]))
        print(f"  top-{k}: {h}/{tot} = {100.0*h/tot:5.1f}%")
    dropped = sum(1 for r in res for x in r["ranked_files"][:5] if not is_real_file(x))
    print(f"  non-file entries inside top-5: {dropped}")


for a in sys.argv[1:]:
    p, l = a.split("=")
    score(p, l, filt=False)
    score(p, l, filt=True)
