"""Detects exported function signature changes between two evidence
snapshots (a PR's base and head scans), and cross-references the
dependency graph's own imported_by data to find files that import the
changed function's module but weren't touched in the same PR - the
"you changed this signature, but N dependents weren't updated" gap
Regression Fencing closes.

Deliberately file/import-level, not a full call graph: a file being
flagged here means it imports the module containing the changed
function, not necessarily that it calls that specific function - no
call-graph/reference tracking exists (or is planned) at that
granularity. This matches what the dependency graph actually tracks
today and is a genuine, real signal even at that coarser level.
"""


def _index_functions(evidence: dict) -> dict[tuple[str, str], str | None]:
    """(file_path, function_name) -> params string, across the whole repo."""
    index: dict[tuple[str, str], str | None] = {}
    for module in evidence.get("repository", {}).get("modules", []):
        path = module.get("path")
        for fn in module.get("symbols", {}).get("functions", []):
            index[(path, fn["name"])] = fn.get("params")
    return index


def find_changed_signatures(old_evidence: dict, new_evidence: dict) -> list[dict]:
    """Functions present in both snapshots whose params text differs.

    A function that's new or removed entirely isn't a "signature change" -
    that's a different, already-visible kind of edit (it shows up in the
    diff comment's added/removed symbols, not here). Only an existing
    function whose parameter list changed counts.
    """
    old_index = _index_functions(old_evidence)
    new_index = _index_functions(new_evidence)
    changed = []
    for (path, name), new_params in new_index.items():
        old_params = old_index.get((path, name))
        if (path, name) in old_index and old_params != new_params and new_params is not None:
            changed.append(
                {"file": path, "function": name, "old_params": old_params, "new_params": new_params}
            )
    return changed


def find_regression_fence_violations(
    old_evidence: dict, new_evidence: dict, changed_files: list[str]
) -> list[dict]:
    """Each changed_signatures entry, enriched with the subset of its
    module's imported_by files that weren't touched in this PR. Only
    signature changes with at least one such untouched dependent are
    returned - a change where every importer was already updated in the
    same PR isn't a violation."""
    changed_files_set = set(changed_files)
    changed_signatures = find_changed_signatures(old_evidence, new_evidence)
    if not changed_signatures:
        return []

    module_imported_by = {
        module.get("path"): module.get("imported_by") or []
        for module in new_evidence.get("repository", {}).get("modules", [])
    }

    violations = []
    for change in changed_signatures:
        importers = module_imported_by.get(change["file"], [])
        untouched = sorted(f for f in importers if f not in changed_files_set)
        if untouched:
            violations.append({**change, "untouched_callers": untouched})
    return violations
