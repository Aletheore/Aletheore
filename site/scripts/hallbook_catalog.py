"""Shared helpers for the Hallbook page's data: reads the bundled skill catalogue through aletheore.skill_hub.
Needs the Hallbook catalogue code on the path (PYTHONPATH=../src from site/). Used by build-hallbook-data.py and hallbook-dev-api.py."""
import collections
import re
from aletheore import skill_hub as sh

# The page shows the bundled catalogue only. A developer's own custom skills must never rank into the snapshot
# (it would differ per machine and could crash the build on an id that is not in CAT).
assert hasattr(sh, "list_custom_skill_names"), "skill_hub no longer has list_custom_skill_names: update the override below"
sh.list_custom_skill_names = lambda: []

ROOT = sh.bundled_skills_dir()
SHELF_NAMES = {"build": "Build", "quality": "Quality", "ship-operate": "Ship & Operate", "ai-agents": "AI & Agents",
               "product-design": "Product & Design", "content-media": "Content & Media"}
CAT = {}
for _m in sh.list_bundled_skills():
    _head = (ROOT / _m.skill_id / "SKILL.md").read_text(encoding="utf-8", errors="replace")[:3500]
    _src = re.search(r"^source_url:\s*(.+)$", _head, re.M)
    CAT[_m.skill_id] = {"id": _m.skill_id, "title": _m.title, "description": _m.description, "category": _m.category, "shelf": _m.shelf,
                        "license": _m.license, "quality": _m.quality, "source": (_src.group(1).strip().strip('"') if _src else "")}


def card(skill_id, score=None):
    c = dict(CAT[skill_id])
    c["description"] = c["description"][:240]
    c["alternates"] = sh.skill_alternates(skill_id)
    if score is not None:
        c["score"] = round(score, 1)
    return c


def find(query):
    query = query.strip()
    if len(query) < 3:
        return {"confidence": "none", "note": "Describe the task in a few words."}
    r = sh.find_skill(query)
    out = {"confidence": r.get("confidence"), "note": r.get("note", "")}
    if r.get("top") and r["top"]["skill_id"] in CAT:
        out["top"] = card(r["top"]["skill_id"])
    for key in ("alternatives", "candidates"):
        if r.get(key):
            out[key] = [card(x["skill_id"]) for x in r[key] if x["skill_id"] in CAT]
    if r.get("skill"):
        body = r["skill"].get("content") or ""
        out["chars"] = len(body)
        out["preview"] = body[:700]
    return out


def skill_head(skill_id, lines=34):
    text = (ROOT / skill_id / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    return {"id": skill_id, "head": "\n".join(text.splitlines()[:lines])}


def stats():
    shelves = collections.Counter(s["shelf"] for s in CAT.values())
    cats = collections.defaultdict(collections.Counter)
    for s in CAT.values():
        cats[s["shelf"]][s["category"]] += 1
    lic = collections.Counter(s["license"] for s in CAT.values())
    return {"total": len(CAT), "categories": len({s["category"] for s in CAT.values()}),
            "shelves": [{"key": k, "name": SHELF_NAMES.get(k, k), "count": shelves[k], "top": [c for c, _ in cats[k].most_common(4)]}
                        for k, _ in shelves.most_common()], "licenses": lic.most_common(6)}
