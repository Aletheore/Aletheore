"""Diffs a fresh full-repo scan result (build_module_graph's modules,
map_api_endpoints's endpoints) against the previously persisted code
graph state, to compute the incremental delta a push should write.

Reuses build_module_graph/map_api_endpoints as-is - the scan itself
still walks and re-parses the whole repo every time (see
scan_worker/code_graph_store.py's module docstring for why true
parse-level incrementality - skipping unchanged files' AST parsing
entirely - is a separate, larger piece of work requiring a persistent
per-repo checkout the hosted service doesn't keep today; every hosted
scan clones fresh and deletes the clone afterward). What's incremental
here is the WRITE: only files/edges/endpoints that actually changed get
upserted, and rows for anything no longer present get deleted, instead
of "wipe and reinsert everything" (repo_history's whole-blob snapshot
approach).
"""
import hashlib
import json


def module_content_hash(module: dict) -> str:
    """A stable fingerprint of everything about a module that this graph
    tracks (language, resolved imports, symbols) - deliberately NOT the
    module's path or imported_by (both are either identity, not content,
    or derived from OTHER files' imports - a change there doesn't mean
    THIS file changed) and NOT raw file bytes, so a comment-only or
    whitespace-only edit (no symbol/import change) correctly produces no
    delta."""
    stable = json.dumps(
        {
            "language": module.get("language"),
            "imports": sorted(module.get("imports", [])),
            "symbols": module.get("symbols", {}),
        },
        sort_keys=True,
    )
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def diff_modules(previous_hashes: dict[str, str], modules: list[dict]) -> tuple[list[dict], list[str]]:
    """previous_hashes: path -> content_hash already persisted.

    Returns (changed_modules, deleted_paths). changed_modules is the
    subset of `modules` (each with an added "content_hash" key) that's
    new or whose content_hash differs from what's already persisted.
    """
    changed = []
    current_paths = set()
    for module in modules:
        path = module["path"]
        current_paths.add(path)
        content_hash = module_content_hash(module)
        if previous_hashes.get(path) != content_hash:
            changed.append({**module, "content_hash": content_hash})
    deleted_paths = [path for path in previous_hashes if path not in current_paths]
    return changed, deleted_paths


def _endpoint_key(endpoint: dict) -> tuple:
    return (endpoint.get("method"), endpoint.get("path"))


def diff_endpoints(
    previous: dict[tuple, dict], endpoints: list[dict]
) -> tuple[list[dict], list[tuple]]:
    """previous: (method, path) -> {"file": ..., "line": ...} already
    persisted. Returns (changed_endpoints, deleted_keys)."""
    changed = []
    current_keys = set()
    for endpoint in endpoints:
        key = _endpoint_key(endpoint)
        current_keys.add(key)
        prior = previous.get(key)
        if (
            prior is None
            or prior.get("file") != endpoint.get("file")
            or prior.get("line") != endpoint.get("line")
        ):
            changed.append(endpoint)
    deleted_keys = [key for key in previous if key not in current_keys]
    return changed, deleted_keys
