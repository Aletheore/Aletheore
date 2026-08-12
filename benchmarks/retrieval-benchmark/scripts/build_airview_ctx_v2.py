"""Retrieve over AIRview units the same way RepoWise retrieves over wiki pages:
embed each unit with nomic-embed-text, rank against the question, fill the same
12,000-char budget. Talks to Ollama over HTTP so it does not import the package
(the working tree's search_index.py currently has conflict markers)."""
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bench, math
import httpx

BUDGET = 12000
OLLAMA = "http://localhost:11434/api/embed"
MODEL = "nomic-embed-text"

av = json.load(open(os.path.join(_bench.OUT,"airview_v3.json")))
ovd = av["overview"].get("description", "")

units = [f"# Repository overview\n{ovd}"]
for s in av["subsystems"]:
    units.append(f"# Subsystem: {s['name']}\n{s['description']}")
    for f in s.get("files") or []:
        blk = f"## {f.get('path')} (subsystem: {s['name']})\nRole: {f.get('role','')}\n"
        for k in f.get("key_symbols") or []:
            blk += f"- `{k.get('name')}` (line {k.get('line')}): {k.get('explanation','')}\n"
        if f.get("detail"):
            blk += "\n" + f["detail"]
        units.append(blk)
print("AIRview units:", len(units))


def embed(texts):
    out = []
    for i in range(0, len(texts), 32):
        r = httpx.post(OLLAMA, json={"model": MODEL, "input": texts[i:i + 32]}, timeout=600)
        r.raise_for_status()
        out.extend(r.json()["embeddings"])
    return out


def cos(a, b):
    d = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(x * x for x in b))
    return d / (na * nb + 1e-9)


uvecs = embed(units)
base = json.load(open(os.path.join(_bench.OUT,"arch_context2.json")))
qvecs = embed([c["q"] for c in base])

out = []
for c, qv in zip(base, qvecs):
    ranked = sorted(range(len(units)), key=lambda i: -cos(qv, uvecs[i]))
    buf = ""
    for i in ranked:
        if len(buf) >= BUDGET:
            break
        buf += units[i][: BUDGET - len(buf)] + "\n\n"
    c["airview_v2"] = buf[:BUDGET]
    out.append(c)
    print(c["id"], len(c["airview_full"]))

json.dump(out, open(os.path.join(_bench.OUT,"arch_context2.json"), "w"), indent=2)
