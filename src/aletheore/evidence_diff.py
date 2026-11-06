"""The "what is new and risky" comparison of two evidence snapshots.

`aletheore diff --fail-on-new-*` and Shield's guard answer the same question - did this change introduce a secret,
a vulnerability, a layer violation or an import cycle - so both read it from here. Dict-level and pure: no file or
network access.
"""

from __future__ import annotations

from collections.abc import Callable

from aletheore.history import compute_diff
from aletheore.query import _shortest_cycle, import_cycle_components


def real_new_secrets(curated: dict) -> list[dict]:
    """New working-tree and git-history secrets from a curated `compute_diff`, minus placeholders and accepted ones."""
    return [
        f
        for key in ("secrets", "history_secrets")
        for f in curated[key]["new"]
        if not f.get("likely_placeholder", False) and not f.get("accepted", False)
    ]


def vulnerability_check_skipped_in(old: dict, new: dict) -> list[str]:
    """Which snapshots ("the old", "the new") ran without the vulnerability check; empty when both ran it.

    With the check off in either snapshot, "new" vulnerabilities only reflect it being toggled, not a real change."""
    checked_old = old["security"]["dependency_vulnerabilities"].get("checked", True)
    checked_new = new["security"]["dependency_vulnerabilities"].get("checked", True)
    return [name for name, checked in (("the old", checked_old), ("the new", checked_new)) if not checked]


def _module_paths(evidence: dict) -> set[str]:
    return {m.get("path") or "" for m in evidence["repository"]["modules"]}


def _basename(path: str) -> str:
    return path.rpartition("/")[2]


def same_file_name(gone: str, came: str) -> bool:
    """The default rename judge: a file moved to another folder under the same name."""
    return _basename(gone) == _basename(came)


def _each_became_a_distinct_file(removed: list[str], added: list[str], renamed: Callable[[str, str], bool]) -> bool:
    """Whether every removed member can be paired with its own added file that `renamed` accepts (a one-to-one
    matching, found with augmenting paths: two deleted files can never both have become the same new one)."""
    accepted = {gone: [came for came in added if renamed(gone, came)] for gone in removed}
    owner: dict[str, str] = {}

    def assign(gone: str, seen: set[str]) -> bool:
        for came in accepted[gone]:
            if came in seen:
                continue
            seen.add(came)
            if came not in owner or assign(owner[came], seen):
                owner[came] = gone
                return True
        return False

    return all(assign(gone, set()) for gone in removed)


def _familiar(
    group: set[str], old_groups: list[set[str]], old_paths: set[str], new_paths: set[str],
    renamed: Callable[[str, str], bool] = same_file_name,
) -> bool:
    """Whether the new cycle group `group` is an old cycle group that changed, rather than a new cycle.

    - It meets exactly one old group and holds all of it or only modules of it: the cycle grew, shrank or was found
      from another start module. A group joining two or more old groups into one loop is new.
    - It meets exactly one old group, files of that group were deleted and files new to the repository joined, every
      member of the old group that still exists is still in the loop, and `renamed(gone, came)` says each deleted
      member became a new file of its own (one to one): members were renamed or moved. The graph alone cannot tell a rename from a
      deleted member plus an unrelated new file closing the loop, so without that a group is new. When a surviving
      member left the loop, the new file closed a different loop and the group is new.
    - It meets no old group, but an old group was moved whole (`git mv pkg new`): none of its files is left, every
      member of this group is new, and the file names are the same.
    """
    met = [old for old in old_groups if old & group]
    if len(met) > 1:
        return False
    if len(met) == 1:
        old = met[0]
        if group <= old or old <= group:
            return True
        removed = old - new_paths
        added = group - old_paths
        return (bool(removed) and bool(added) and (old - removed) <= group
                and _each_became_a_distinct_file(sorted(removed), sorted(added), renamed))
    added = group - old_paths
    if added != group:
        return False
    names = sorted(map(_basename, group))
    return any(not (old & new_paths) and sorted(map(_basename, old)) == names for old in old_groups)


def new_cycles(old: dict, new: dict, renamed: Callable[[str, str], bool] | None = None) -> list[dict]:
    """Import-cycle groups in `new` that are not an old cycle group changed (see `_familiar`): a cycle that grew,
    shrank, had members renamed or moved, or was found from a different start module is not new; a group that meets
    no old group, joins several old groups into one loop, or replaces members of an old group while one of its
    surviving members left the loop is new.

    Works on the full strongly-connected components computed from each snapshot's module graph (evidence stores the
    graph, not a cycle list), so the listing caps of `find_import_cycles` cannot hide an old cycle. Old self-imports
    do not make a new mutual cycle familiar. `renamed(gone, came)` judges whether a deleted member became a new file
    (default `same_file_name`; Shield compares the two files' contents)."""
    old_components, old_self, _ = import_cycle_components(old)
    old_groups = [set(group) for group in old_components]
    seen = {m for group in old_groups for m in group} | set(old_self)
    new_groups, new_self, graph = import_cycle_components(new)
    old_paths, new_paths = _module_paths(old), _module_paths(new)
    found = []
    for component in sorted(new_groups, key=lambda c: (-len(c), c[0])):
        if not _familiar(set(component), old_groups, old_paths, new_paths, renamed or same_file_name):
            found.append(
                {
                    "size": len(component),
                    "example_cycle": _shortest_cycle(component, graph),
                    "members": component,
                    "members_truncated": False,
                }
            )
    for path in new_self:
        if path not in seen:
            found.append({"size": 1, "example_cycle": [path, path], "members": [path], "members_truncated": False})
    return found


def new_risks(old: dict, new: dict, renamed: Callable[[str, str], bool] | None = None) -> dict[str, list[dict]]:
    """Risks present in `new` and not in `old`: keys secrets, vulnerabilities, layer_violations, cycles.

    Secrets exclude placeholders and accepted ones; vulnerabilities are empty when either snapshot skipped the check.
    """
    curated = compute_diff(old, new, full=False)
    return {
        "secrets": real_new_secrets(curated),
        "vulnerabilities": [] if vulnerability_check_skipped_in(old, new) else list(curated["vulnerabilities"]["new"]),
        "layer_violations": list(curated["layer_violations"]["new"]),
        "cycles": new_cycles(old, new, renamed),
    }
