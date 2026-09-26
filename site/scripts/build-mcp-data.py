"""Writes src/data/mcp-tools.json from the installed aletheore package.

Run with the release you want to describe on PYTHONPATH, e.g.
    PYTHONPATH=/path/to/unzipped-0.9.20 python3 scripts/build-mcp-data.py
"""
import asyncio, json, pathlib, tempfile

import aletheore
from aletheore.cli import QUERY_KIND_GROUPS
from aletheore.mcp_server import build_server

repo = pathlib.Path(tempfile.mkdtemp())


def names(**kw):
    server = build_server(repo, **kw)
    return sorted(t.name for t in asyncio.run(server.list_tools()))


default = names()
everything = names(answer_adapter=object(), allow=frozenset({"external", "network", "write"}))
optional = [n for n in everything if n not in default]

query_groups = {group: list(kinds) for group, kinds in QUERY_KIND_GROUPS.items()}

out = {
    "source": "aletheore package build_server() and cli.QUERY_KIND_GROUPS",
    "version": getattr(aletheore, "__version__", "unknown"),
    "default": default,
    "optional": optional,
    "queryGroups": query_groups,
}
dest = pathlib.Path(__file__).resolve().parent.parent / "src/data/mcp-tools.json"
dest.write_text(json.dumps(out, indent=2) + "\n")
print(len(default), "default,", optional, "optional,", sum(len(v) for v in query_groups.values()), "query kinds, version", out["version"])
