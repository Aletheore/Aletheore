"""Deterministic cluster brief extraction for the Live Wiki.

Gathers, for each architecture cluster, its member files and each file's
key symbols directly from the scanner's own evidence - no LLM involved.
This is the "map" a naming/writing model is later pointed at, so it never
has to rediscover structure the scanner already knows.
"""

import os

MAX_SYMBOLS_PER_FILE = 15


def _key_symbols(module: dict) -> list[dict]:
    symbols = module.get("symbols", {})
    entries = [
        {"name": s["name"], "kind": "function", "start_line": s["start_line"], "end_line": s["end_line"]}
        for s in symbols.get("functions", [])
    ] + [
        {"name": s["name"], "kind": "class", "start_line": s["start_line"], "end_line": s["end_line"]}
        for s in symbols.get("classes", [])
    ]
    return entries[:MAX_SYMBOLS_PER_FILE]


def _fallback_name(file_paths: list[str]) -> str:
    """A readable name derived purely from the files themselves, used if
    the naming model is unavailable - never blocks the wiki on an LLM call.
    """
    if not file_paths:
        return "Unnamed subsystem"
    common = os.path.commonpath(file_paths) if len(file_paths) > 1 else os.path.dirname(file_paths[0])
    return common or file_paths[0]


def build_cluster_briefs(evidence: dict) -> list[dict]:
    clusters = evidence.get("architecture", {}).get("clusters", [])
    modules_by_path = {m["path"]: m for m in evidence.get("repository", {}).get("modules", [])}

    briefs = []
    for cluster in clusters:
        member_paths = cluster.get("modules", [])
        files = []
        for path in member_paths:
            module = modules_by_path.get(path)
            if module is None:
                continue
            files.append(
                {
                    "path": path,
                    "language": module.get("language"),
                    "key_symbols": _key_symbols(module),
                }
            )
        briefs.append(
            {
                "cluster_id": cluster["id"],
                "files": files,
                "fallback_name": _fallback_name(member_paths),
            }
        )
    return briefs
