"""Deterministic audit of the AIRview generation pipeline. No LLM calls."""
import json, os, sys
sys.path.insert(0, "/Users/arihantkaul/Documents/GitHub/Veridion/github-app")
from aletheore.wiki_mapping import build_cluster_briefs, rank_files_by_importance, MAX_SYMBOLS_PER_FILE
from scan_worker.live_wiki import select_file_page_paths

REPOS = {
    "flask": "/private/tmp/bench-flask",
    "requests": "/private/tmp/multi-requests",
    "httpx": "/private/tmp/multi-httpx",
    "attrs": "/private/tmp/multi-attrs",
    "express(js)": "/private/tmp/poly-express",
    "gin(go)": "/private/tmp/poly-gin",
    "serde(rust)": "/private/tmp/poly-serde",
    "axios(js+ts)": "/private/tmp/poly-axios",
}

for name, path in REPOS.items():
    air = os.path.join(path, ".aletheore", "air.json")
    if not os.path.exists(air):
        print(f"{name}: NO SCAN"); continue
    e = json.load(open(air))
    mods = e["repository"]["modules"]
    by_path = {m["path"]: m for m in mods}
    clusters = e.get("architecture", {}).get("clusters", [])
    briefs = build_cluster_briefs(e)
    ranked = rank_files_by_importance(e)
    planned = select_file_page_paths(e)

    print(f"\n{'='*66}\n{name}  ({len(mods)} modules, {len(clusters)} clusters)")

    # G1: cluster membership referencing modules that do not exist
    missing = sum(1 for c in clusters for p in c.get("modules", []) if p not in by_path)
    print(f"  G1 cluster members not in modules       : {missing}")

    # G2: singleton clusters become one-file "subsystems"
    singles = [c for c in clusters if len(c.get("modules", [])) == 1]
    print(f"  G2 singleton clusters (junk subsystems) : {len(singles)}/{len(clusters)}")

    # G3: clusters that are entirely tests/examples
    def demoted(p):
        return any(s in "/" + p for s in ("/tests/", "/test/", "/examples/", "/docs/"))
    junk = [c for c in clusters if c.get("modules") and all(demoted(p) for p in c["modules"])]
    print(f"  G3 clusters that are entirely tests/docs: {len(junk)}/{len(clusters)}")

    # G4: symbol truncation in briefs
    trunc = [f["path"] for b in briefs for f in b["files"] if len(f["key_symbols"]) == MAX_SYMBOLS_PER_FILE]
    print(f"  G4 files hitting the {MAX_SYMBOLS_PER_FILE}-symbol brief cap : {len(trunc)}")

    # G5: files with zero symbols -> no page possible
    nosym = [m["path"] for m in mods
             if not (m.get("symbols", {}).get("functions") or m.get("symbols", {}).get("classes")
                     or m.get("symbols", {}).get("constants"))]
    print(f"  G5 modules with zero extracted symbols  : {len(nosym)}/{len(mods)}")

    # G6: non-Python modules (constants extraction is Python-only)
    langs = {}
    for m in mods:
        langs[m.get("language", "?")] = langs.get(m.get("language", "?"), 0) + 1
    nonpy = sum(v for k, v in langs.items() if k != "python")
    print(f"  G6 non-python modules (no constants)    : {nonpy}  {langs}")

    # G7: page coverage of the public API surface
    reexp = set()
    for m in mods:
        if os.path.basename(m["path"]) == "__init__.py":
            reexp.update(m.get("imports", []) or [])
    uncovered = [p for p in reexp if p not in planned]
    print(f"  G7 public-API files WITHOUT a page      : {len(uncovered)}/{len(reexp)}  {uncovered[:4]}")

    # G8: demoted files that still take a page slot
    dem_planned = [p for p in planned if demoted(p)]
    print(f"  G8 tests/docs taking a page slot        : {len(dem_planned)}  {dem_planned[:3]}")

    # G9: overall page coverage
    print(f"  G9 pages planned                        : {len(planned)}/{len(mods)} "
          f"({100*len(planned)/max(len(mods),1):.0f}%)")

    # G10: modules absent from every cluster -> unreachable by the wiki
    inclu = {p for c in clusters for p in c.get("modules", [])}
    orphan = [m["path"] for m in mods if m["path"] not in inclu]
    print(f"  G10 modules in NO cluster (invisible)   : {len(orphan)}  {orphan[:3]}")
