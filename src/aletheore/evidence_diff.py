"""The "what is new and risky" comparison of two evidence snapshots.

`aletheore diff --fail-on-new-*` and Shield's guard answer the same question - did this change introduce a secret,
a vulnerability, a layer violation or an import cycle - so both read it from here. Dict-level and pure: no file or
network access.
"""

from __future__ import annotations

from aletheore.history import compute_diff
from aletheore.query import find_import_cycles


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


def _cycle_groups(evidence: dict) -> list[dict]:
    result = find_import_cycles(evidence)
    groups = list(result.get("cycle_groups") or [])
    for path in result.get("self_importing_modules") or []:
        groups.append({"size": 1, "example_cycle": [path, path], "members": [path], "members_truncated": False})
    return groups


def new_cycles(old: dict, new: dict) -> list[dict]:
    """Import-cycle groups in `new` that share no module with any cycle group in `old`. A cycle that merely grew
    or shrank is the same cycle, not a new one."""
    seen = {m for group in _cycle_groups(old) for m in group["members"]}
    return [g for g in _cycle_groups(new) if not seen.intersection(g["members"])]


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
