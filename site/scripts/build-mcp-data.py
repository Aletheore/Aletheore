"""Writes src/data/mcp-tools.json from the installed aletheore package.

Run with the release you want to describe on PYTHONPATH, e.g.
    PYTHONPATH=/path/to/unzipped-0.9.20 python3 scripts/build-mcp-data.py
"""
import asyncio, json, pathlib, tempfile

import aletheore
from aletheore.cli import QUERY_KIND_GROUPS
from aletheore.mcp_server import build_server

repo = pathlib.Path(tempfile.mkdtemp())


def tools_of(**kw):
    server = build_server(repo, **kw)
    return asyncio.run(server.list_tools())


def names(**kw):
    return sorted(t.name for t in tools_of(**kw))


default = names()
everything = names(answer_adapter=object(), allow=frozenset({"external", "network", "write"}))
optional = [n for n in everything if n not in default]

query_groups = {group: list(kinds) for group, kinds in QUERY_KIND_GROUPS.items()}

# One-line description per tool, taken from the tool's own registered description (first sentence).
def first_sentence(text):
    text = " ".join((text or "").split())
    end = text.find(". ")
    while end != -1 and text[max(0, end - 3) : end + 1].lower() in ("e.g.", "i.e."):
        end = text.find(". ", end + 1)
    out = (text[: end + 1] if end != -1 else text).strip()
    for tail in (" - e.g.", " - i.e."):
        if out.endswith(tail):
            out = out[: -len(tail)].rstrip() + "."
    return out.replace(" - ", ": ").replace("`", "")


described = {t.name: first_sentence(t.description) for t in tools_of(answer_adapter=object(), allow=frozenset({"external", "network", "write"}))}

# Category per tool: the query group when the tool wraps a query kind, otherwise a role name.
group_of = {}
for group, kinds in query_groups.items():
    for kind in kinds:
        group_of["aletheore_" + kind.replace("-", "_")] = group
group_of.update({
    "aletheore_find_evidence_for_endpoint": "Evidence", "aletheore_find_evidence_for_symbol": "Evidence",
    "aletheore_find_evidence_for_dependency": "Evidence",
    "aletheore_scan": "Run", "aletheore_index": "Run", "aletheore_healthcheck": "Run", "aletheore_managed_audit": "Run",
    "aletheore_overview": "Navigate", "aletheore_list": "Navigate", "aletheore_neighborhood": "Navigate",
    "aletheore_get_blast_radius": "Navigate", "aletheore_search": "Search", "aletheore_search_codebase": "Search",
    "aletheore_symbol_source": "Search", "aletheore_verify_citations": "Evidence", "aletheore_ast_pattern": "Search",
})
tool_info = [
    {"name": n, "group": group_of.get(n, "Other"), "description": described.get(n, "")}
    for n in sorted(described)
]

out = {
    "source": "aletheore package build_server() and cli.QUERY_KIND_GROUPS",
    "version": getattr(aletheore, "__version__", "unknown"),
    "default": default,
    "optional": optional,
    "queryGroups": query_groups,
    "tools": tool_info,
}
dest = pathlib.Path(__file__).resolve().parent.parent / "src/data/mcp-tools.json"
dest.write_text(json.dumps(out, indent=2) + "\n")
print(len(default), "default,", optional, "optional,", sum(len(v) for v in query_groups.values()), "query kinds, version", out["version"])
