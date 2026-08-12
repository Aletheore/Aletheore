import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _bench, os, re, sys, time
import httpx

KEY = None
for line in open(_bench.ENV_FILE):
    if line.startswith("DEEPSEEK_API_KEY"):
        KEY = line.strip().split("=", 1)[1]
MODEL = os.environ.get("JUDGE_MODEL", "deepseek-chat")

RUBRIC = """You are grading retrieval systems for a code-comprehension task.

A developer new to the Flask codebase asked the QUESTION below. Two systems each
returned a bundle of retrieved material (roughly equal length). You are grading
ONLY the retrieved material — not writing the answer yourself.

For each system, score how well its material would let a competent engineer write
a correct, specific answer to the question:

3 = fully sufficient; covers the key mechanisms with specifics
2 = mostly sufficient; minor gaps a reader could bridge
1 = partially relevant; major gaps or mostly tangential
0 = irrelevant or misleading

Judge substance, not format. Prose is not automatically better than code, and code
is not automatically better than prose. Ignore which system appears first.

Return ONLY strict JSON:
{"system_a": {"score": <0-3>, "why": "<one sentence>"},
 "system_b": {"score": <0-3>, "why": "<one sentence>"},
 "better": "a" | "b" | "tie"}"""


def scrub(t):
    t = re.sub(r"(?i)repowise", "the tool", t)
    t = re.sub(r"(?i)aletheore", "the tool", t)
    return t


def ask(question, a, b):
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": RUBRIC},
            {"role": "user", "content":
                f"QUESTION:\n{question}\n\n=== SYSTEM A MATERIAL ===\n{a}\n\n"
                f"=== SYSTEM B MATERIAL ===\n{b}"},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    for attempt in range(4):
        try:
            r = httpx.post("https://api.deepseek.com/chat/completions",
                           headers={"Authorization": f"Bearer {KEY}"},
                           json=body, timeout=300)
            r.raise_for_status()
            return json.loads(r.json()["choices"][0]["message"]["content"])
        except Exception as e:
            print(f"  retry {attempt}: {str(e)[:120]}", file=sys.stderr)
            time.sleep(3 * (attempt + 1))
    return None


import os
ARM=os.environ["ARM"]
ctx = json.load(open(os.path.join(_bench.OUT,"arch_context2.json")))
rows = []
for c in ctx:
    al, rw = scrub(c[ARM]), scrub(c["repowise"])
    # pass 1: aletheore = A ; pass 2: swapped, to cancel position bias
    r1 = ask(c["q"], al, rw)
    r2 = ask(c["q"], rw, al)
    if not r1 or not r2:
        print("FAILED", c["id"], file=sys.stderr)
        continue
    a_scores = [r1["system_a"]["score"], r2["system_b"]["score"]]
    r_scores = [r1["system_b"]["score"], r2["system_a"]["score"]]
    pref = []
    pref.append({"a": "aletheore", "b": "repowise", "tie": "tie"}[r1["better"]])
    pref.append({"a": "repowise", "b": "aletheore", "tie": "tie"}[r2["better"]])
    rows.append({"id": c["id"], "q": c["q"],
                 "aletheore": sum(a_scores) / 2, "repowise": sum(r_scores) / 2,
                 "aletheore_raw": a_scores, "repowise_raw": r_scores,
                 "pref": pref,
                 "why_a": r1["system_a"]["why"], "why_r": r1["system_b"]["why"]})
    print(f"{c['id']}  aletheore={sum(a_scores)/2:.1f} repowise={sum(r_scores)/2:.1f}  pref={pref}",
          file=sys.stderr)

json.dump(rows, open(os.path.join(_bench.OUT,"arch_scores_"+ARM+".json"),"w"), indent=2)
n = len(rows)
if n:
    print(f"\nARCHITECTURE QUESTIONS (n={n}, 0-3 scale, order-swapped mean)", file=sys.stderr)
    print(f"  Aletheore : {sum(r['aletheore'] for r in rows)/n:.2f}", file=sys.stderr)
    print(f"  RepoWise  : {sum(r['repowise'] for r in rows)/n:.2f}", file=sys.stderr)
    flat = [p for r in rows for p in r["pref"]]
    for k in ("aletheore", "repowise", "tie"):
        print(f"  pref {k}: {flat.count(k)}/{len(flat)}", file=sys.stderr)

