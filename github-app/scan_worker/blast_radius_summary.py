"""Deterministic "blast radius" section for the Flash Review summary comment.

Which files elsewhere in the repo import the files a PR changes, taken straight from the scan
evidence's import graph (aletheore.query.find_blast_radius). No model is involved and nothing is
guessed: the caller only passes evidence scanned at the PR's own head commit, and this returns ""
when there is no such evidence or none of the changed files is a module the scan knows about.
"""
import logging
from pathlib import Path

from aletheore.query import find_blast_radius

logger = logging.getLogger(__name__)

MAX_TARGETS_SHOWN = 6
MAX_DEPENDENTS_PER_TARGET = 4
MAX_INDIRECT_SHOWN = 6


def _names(paths: list[str], limit: int) -> str:
    shown = ", ".join(f"`{p}`" for p in paths[:limit])
    extra = len(paths) - limit
    return f"{shown} (+{extra} more)" if extra > 0 else shown


def blast_radius_summary(evidence: dict | None, changed_files: list[str]) -> str:
    if not evidence:
        return ""
    modules = evidence.get("repository", {}).get("modules") or []
    known = {m.get("path") for m in modules if m.get("path")}
    changed = set(changed_files)

    per_target: dict[str, list[str]] = {}
    direct: set[str] = set()
    indirect: set[str] = set()
    truncated = False
    analysed = 0
    for path in changed_files:
        if path not in known:
            continue
        try:
            radius = find_blast_radius(evidence, Path("."), path)
        except Exception:  # noqa: BLE001 - a malformed module entry must never block the review
            logger.debug("blast radius skipped %s: malformed module entry", path, exc_info=True)
            continue
        analysed += 1
        # Files already in this PR are being reviewed anyway; the useful signal is who ELSE is affected.
        direct_here = [p for p in radius["direct_dependents"] if p not in changed]
        indirect_here = [p for p in radius["transitive_dependents"] if p not in changed]
        direct.update(direct_here)
        indirect.update(indirect_here)
        truncated = truncated or radius["direct_dependents_truncated"] or radius["transitive_dependents_truncated"]
        if direct_here:
            per_target[path] = sorted(direct_here)
    indirect -= direct
    if not analysed:
        return ""

    if not direct and not indirect:
        return (
            "\n\n_Blast radius: no other file in the repo imports the changed file(s), "
            "per this commit's import graph._"
        )

    total = len(direct) + len(indirect)
    lines = [
        f"\n\n<details><summary>Blast radius: {total} other file(s) depend on what this PR changes "
        f"({len(direct)} directly, {len(indirect)} indirectly)</summary>\n",
        "Computed from the import graph of this exact commit, not guessed by a model. "
        "It sees imports only, so dynamic imports, reflection and non-code references are not counted.\n",
    ]
    ordered = sorted(per_target.items(), key=lambda item: -len(item[1]))
    for path, dependents in ordered[:MAX_TARGETS_SHOWN]:
        lines.append(f"- `{path}` is imported by {_names(dependents, MAX_DEPENDENTS_PER_TARGET)}")
    if len(ordered) > MAX_TARGETS_SHOWN:
        lines.append(f"- ...and {len(ordered) - MAX_TARGETS_SHOWN} more changed file(s) with dependents")
    if indirect:
        lines.append(f"\nIndirectly affected: {_names(sorted(indirect), MAX_INDIRECT_SHOWN)}")
    if truncated:
        lines.append("\n_The graph is large; this list is capped and not exhaustive._")
    lines.append("\n</details>")
    return "\n".join(lines)
