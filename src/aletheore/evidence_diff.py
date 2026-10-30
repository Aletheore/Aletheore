"""The "what is new and risky" comparison of two evidence snapshots.

`aletheore diff --fail-on-new-*` and Shield's guard answer the same question - did this change introduce a secret,
a vulnerability, a layer violation or an import cycle - so both read it from here. Dict-level and pure: no file or
network access.
"""

from __future__ import annotations

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


def new_cycles(old: dict, new: dict) -> list[dict]:
    """Import-cycle groups in `new` that share no module with any cycle group in `old`. A cycle that merely grew,
    shrank or was found from a different start module is the same cycle, not a new one.

    Works on the full strongly-connected components computed from each snapshot's module graph (evidence stores the
    graph, not a cycle list), so the listing caps of `find_import_cycles` cannot hide an old cycle."""
    old_groups, old_self, _ = import_cycle_components(old)
    seen = {m for group in old_groups for m in group} | set(old_self)
    new_groups, new_self, graph = import_cycle_components(new)
    found = []
    for component in sorted(new_groups, key=lambda c: (-len(c), c[0])):
        if not seen.intersection(component):
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


def new_risks(old: dict, new: dict) -> dict[str, list[dict]]:
    """Risks present in `new` and not in `old`: keys secrets, vulnerabilities, layer_violations, cycles.

    Secrets exclude placeholders and accepted ones; vulnerabilities are empty when either snapshot skipped the check.
    """
    curated = compute_diff(old, new, full=False)
    return {
        "secrets": real_new_secrets(curated),
        "vulnerabilities": [] if vulnerability_check_skipped_in(old, new) else list(curated["vulnerabilities"]["new"]),
        "layer_violations": list(curated["layer_violations"]["new"]),
        "cycles": new_cycles(old, new),
    }
