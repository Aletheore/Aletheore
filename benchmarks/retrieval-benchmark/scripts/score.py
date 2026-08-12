import json, sys

def score(path, label):
    res = json.load(open(path))
    tot = len(res)
    out = {}
    for k in (1, 3, 5):
        hits = sum(
            1 for r in res
            if any(f in r["gt"] for f in r["ranked_files"][:k])
        )
        out[k] = (hits, tot, 100.0 * hits / tot)
    print(f"\n{label}  (n={tot})")
    for k in (1, 3, 5):
        h, t, p = out[k]
        print(f"  top-{k}: {h}/{t}  = {p:5.1f}%")
    misses = [r["id"] for r in res if not any(f in r["gt"] for f in r["ranked_files"][:5])]
    print(f"  top-5 misses: {', '.join(misses) if misses else 'none'}")
    return out

for p, l in [a.split("=") for a in sys.argv[1:]]:
    score(p, l)
