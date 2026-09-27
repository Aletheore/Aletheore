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


def count_direct_dependents(evidence: dict, changed_files: list[str]) -> dict[str, int]:
    """How many OTHER files (not already in `changed_files`) import each of
    `changed_files`, read directly from evidence's own `imported_by`
    adjacency - no BFS, no `find_blast_radius`, no
    `aletheore.query._BLAST_RADIUS_MAX_DIRECT` cap.

    Real gap found on final review: `compute_blast_radius`'s `per_target`
    counts come from `find_blast_radius`, which truncates a single
    target's `direct_dependents` to 50 *before* the already-in-PR
    exclusion runs - fine for a rendered, human-read, already-capped list
    (`blast_radius_summary`'s own output), but a per-file count surfaced
    as an exact number (the PR file-overview section's "N dependents")
    must not silently undercount a hub file just because the cap fired
    first. This reads the raw adjacency list instead, so there's nothing
    to truncate.

    Returns {path: count} only for paths in `changed_files` that both
    exist as a scanned module in `evidence` and have at least one such
    dependent - callers should treat a missing path as 0.
    """
    modules_by_path = {
        m["path"]: m for m in (evidence.get("repository", {}).get("modules") or []) if m.get("path")
    }
    changed = set(changed_files)
    counts: dict[str, int] = {}
    for path in changed_files:
        module = modules_by_path.get(path)
        if module is None:
            continue
        dependents = [p for p in (module.get("imported_by") or []) if p not in changed]
        if dependents:
            counts[path] = len(dependents)
    return counts


def compute_blast_radius(evidence: dict, changed_files: list[str]) -> dict:
    """Direct/transitive dependents of each of `changed_files`, from the
    import graph of `evidence` (must be scanned at the commit being
    described - see this module's own docstring). Pulled out of
    `blast_radius_summary` so its rendering can be tested against this
    computation directly. (The PR file-overview section's per-file
    dependents count uses `count_direct_dependents` instead, not this
    function - `per_target` below is capped at `find_blast_radius`'s own
    50-per-target limit, which is fine for this module's rendered,
    human-read list but would silently undercount an exact per-file
    count.)

    Returns {"per_target": {path: [direct dependent paths]}, "direct":
    set[str], "indirect": set[str], "truncated": bool, "analysed": int}.
    `per_target` only carries a path when it has at least one direct
    dependent NOT already in `changed_files` (dependents already under
    review don't need calling out); `direct`/`indirect` are the pooled
    sets across every analysed target, matching what `blast_radius_
    summary`'s own rendering already reported before this refactor.
    """
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
    return {
        "per_target": per_target,
        "direct": direct,
        "indirect": indirect,
        "truncated": truncated,
        "analysed": analysed,
    }


def blast_radius_summary(evidence: dict | None, changed_files: list[str]) -> str:
    if not evidence:
        return ""
    result = compute_blast_radius(evidence, changed_files)
    if not result["analysed"]:
        return ""

    direct, indirect = result["direct"], result["indirect"]
    if not direct and not indirect:
        return (
            "\n\n_Blast radius: no other file in the repo imports the changed file(s), "
            "per this commit's import graph._"
        )

    per_target, truncated = result["per_target"], result["truncated"]
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
