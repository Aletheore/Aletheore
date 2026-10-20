"""Snapshot the Hallbook page's numbers and example lookups into src/data/hallbook.json.

The counts, shelves, sample skill file and example answers on /hallbook come from this file, so they are read from the
catalogue and not typed by hand. Re-run it whenever the catalogue or the ranking changes (from site/):

    PYTHONPATH=../src python3 scripts/build-hallbook-data.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import hallbook_catalog as hc  # noqa: E402

EXAMPLES = ["plan a zero downtime database migration", "create a stripe webhook handler", "write a good git commit message",
            "design a REST API for invoices", "fix the thing"]
out = {"stats": hc.stats(), "skill": hc.skill_head("debugging/systematic-debugging"), "examples": {q: hc.find(q) for q in EXAMPLES}}
path = os.path.join(os.path.dirname(__file__), "..", "src", "data", "hallbook.json")
with open(path, "w") as fh:
    json.dump(out, fh, indent=1)
print("wrote src/data/hallbook.json:", out["stats"]["total"], "skills,", len(EXAMPLES), "examples")
