"""Check the judge on reviews written by hand, before trusting it on real ones:  python judge_selftest.py MANIFEST.json"""
import json
import sys

from aletheore.council.runners import ClaudeRunner
from score import candidates, found_ids

bugs = json.load(open(sys.argv[1]))["bugs"]
by = {b["id"]: b for b in bugs}
good = f"""1. {by['M01']['file']}:{by['M01']['line']} in {by['M01']['function']}: the loop condition uses `or` where it needs `and`, so the search keeps going after the queue is empty and runs past the cutoff.
2. {by['M17']['file']}:{by['M17']['line']} in {by['M17']['function']}: `multigraph or len(G[u][v]) > 1` should be `and`; as written every edge of a multigraph is skipped as a bridge.
3. {by['M06']['file']}:{by['M06']['line']} `{by['M06']['function'].split('.')[-1]}` is hard to read and could use a docstring."""
wrong_problem = f"""1. {by['M01']['function']}: the variable names are confusing and the function lacks type hints.
2. {by['M05']['function']} is documented but the example in the docstring is out of date."""
nothing = "The code looks fine. Consider adding more tests."
for name, text, expect in (("two real + one cosmetic", good, {"M01", "M17"}), ("right function, wrong problem", wrong_problem, set()), ("nothing", nothing, set())):
    got = set(found_ids(text, bugs, ClaudeRunner()))
    print(f"{name:32} candidates {[b['id'] for b in candidates(text, bugs)]}  judge says {sorted(got)}  expected {sorted(expect)}  {'OK' if got == expect else 'MISMATCH'}")
