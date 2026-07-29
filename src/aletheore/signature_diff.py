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


def _split_params(params: str) -> list[str]:
    """Top-level comma-separated parameters from a raw parameter list.

    Splits only at depth zero so a default value or generic type
    containing commas - `Callable[[str], int | None] | None = None`,
    `dict[str, int]`, `foo=(1, 2)` - stays a single parameter.
    """
    inner = params.strip()
    if inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1]
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in inner:
        if char in "([{<":
            depth += 1
        elif char in ")]}>":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if p]


def _is_optional_param(param: str) -> bool:
    """Whether an added parameter leaves existing callers working.

    A default value (`x=1`, `int x = 5`, `$x = 5`), a TypeScript optional
    (`x?: number`), and Python's keyword-only/positional-only markers
    (`*`, `/`) all add nothing a caller must pass. Variadics (`*args`,
    `**kwargs`, `...rest`) likewise. Languages without defaults - Go, Java,
    Rust - simply never match here, so additions there stay flagged, which
    is correct: they really do break callers.
    """
    if param in {"*", "/"}:
        return True
    if param.startswith(("*", "...")):
        return True
    if "=" in param:
        return True
    name = param.split(":", 1)[0].strip()
    return name.endswith("?")


def is_backward_compatible_change(old_params: str | None, new_params: str | None) -> bool:
    """True when the new signature only appends parameters that callers can
    keep omitting.

    Found by dogfooding: the first real PR this feature ran on flagged a
    function that had gained a keyword-only `context: str = "output"`
    argument. No caller could possibly break, yet the check posted a
    merge-blocking-eligible Check Run naming a file that merely imports the
    module. Flagging additive, backward-compatible changes is noise, and
    noise on a check people are told to require in branch protection is
    worse than no check.
    """
    if old_params is None or new_params is None:
        return False
    old_parts = _split_params(old_params)
    new_parts = _split_params(new_params)
    if len(new_parts) < len(old_parts):
        return False
    # Everything the old signature had must still be there, unchanged and in
    # order - a rename or reorder can break callers even with the same count.
    if new_parts[: len(old_parts)] != old_parts:
        return False
    return all(_is_optional_param(p) for p in new_parts[len(old_parts) :])


def find_changed_signatures(old_evidence: dict, new_evidence: dict) -> list[dict]:
    """Functions present in both snapshots whose params text differs.

    A function that's new or removed entirely isn't a "signature change" -
    that's a different, already-visible kind of edit (it shows up in the
    diff comment's added/removed symbols, not here). Only an existing
    function whose parameter list changed counts.

    Purely additive changes that keep every existing caller working are
    excluded too - see is_backward_compatible_change. A caller that cannot
    break is not a caller that needs updating.
    """
    old_index = _index_functions(old_evidence)
    new_index = _index_functions(new_evidence)
    changed = []
    for (path, name), new_params in new_index.items():
        old_params = old_index.get((path, name))
        if (path, name) not in old_index or new_params is None or old_params == new_params:
            continue
        if is_backward_compatible_change(old_params, new_params):
            continue
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
