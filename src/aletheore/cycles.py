"""`aletheore cycles`: every circular import dependency in the repo, as one complete answer with a concrete example
chain for each one - something grep cannot answer at all, since a cycle is a property of the whole import graph,
not of any single file's text.

Finds strongly connected components (Tarjan's algorithm, iterative to avoid Python's recursion limit on a long
import chain) of size > 1 in the forward import graph; each one is a group of files that all transitively import
each other. For each group, a concrete shortest cycle through one of its files is rendered so the answer is a real
chain an agent can follow, not just a set membership claim.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from pathlib import Path

CYCLES_MAX_GROUPS = 50
CYCLE_MAX_CHAIN_LEN = 15


def _build_adjacency(evidence: dict) -> dict[str, list[str]]:
    by_path = {m["path"]: m for m in evidence["repository"]["modules"] if m.get("path")}
    adj: dict[str, list[str]] = {}
    for path, module in by_path.items():
        adj[path] = sorted({imp for imp in (module.get("imports") or []) if imp in by_path and imp != path})
    return adj


def _tarjan_sccs(adj: dict[str, list[str]]) -> list[list[str]]:
    """Strongly connected components of `adj`, iterative (no recursion depth tied to import-chain length)."""
    index_counter = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    result: list[list[str]] = []

    for start in adj:
        if start in indices:
            continue
        work: list[tuple[str, int]] = [(start, 0)]
        while work:
            node, pi = work[-1]
            succs = adj.get(node, [])
            if pi == 0:
                indices[node] = index_counter
                lowlink[node] = index_counter
                index_counter += 1
                stack.append(node)
                on_stack.add(node)
            if pi < len(succs):
                work[-1] = (node, pi + 1)
                nxt = succs[pi]
                if nxt not in indices:
                    work.append((nxt, 0))
                elif nxt in on_stack:
                    lowlink[node] = min(lowlink[node], indices[nxt])
            else:
                work.pop()
                if work:
                    parent = work[-1][0]
                    lowlink[parent] = min(lowlink[parent], lowlink[node])
                if lowlink[node] == indices[node]:
                    component = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        component.append(w)
                        if w == node:
                            break
                    result.append(component)
    return result


def _shortest_path(adj: dict[str, list[str]], members: set[str], src: str, dst: str) -> list[str] | None:
    """Shortest path from `src` to `dst` using only edges between `members`; None if unreachable."""
    if src == dst:
        return [src]
    queue = deque([src])
    prev: dict[str, str | None] = {src: None}
    while queue:
        node = queue.popleft()
        if node == dst:
            break
        for nxt in adj.get(node, []):
            if nxt in members and nxt not in prev:
                prev[nxt] = node
                queue.append(nxt)
    if dst not in prev:
        return None
    path = [dst]
    while path[-1] != src:
        path.append(prev[path[-1]])
    path.reverse()
    return path


def _example_cycle(adj: dict[str, list[str]], members: list[str]) -> list[str]:
    """One concrete shortest cycle through the lexicographically smallest member of an SCC."""
    member_set = set(members)
    start = min(members)
    best: list[str] | None = None
    for nxt in adj.get(start, []):
        if nxt not in member_set:
            continue
        path = _shortest_path(adj, member_set, nxt, start)
        if path is not None and (best is None or len(path) < len(best)):
            best = path
    return [start, *(best or [])]


def _files_changed_since_scan(repo_path: Path, paths: list[str], scanned_at: object) -> list[str] | None:
    """Files whose mtime is later than the scan, so a stale answer can say so. None when the scan time is missing
    or unreadable: "nothing changed" and "could not check" must never look the same."""
    try:
        scan_time = datetime.fromisoformat(str(scanned_at)).timestamp()
    except ValueError:
        return None
    changed = []
    for rel in paths:
        try:
            mtime = (Path(repo_path) / rel).stat().st_mtime
        except OSError:
            changed.append(rel)
            continue
        if mtime > scan_time:
            changed.append(rel)
    return changed


def _scoped(path: str, exclude: list[str], under: list[str]) -> bool:
    """In scope when below one of the --under directories (if any) and not under a directory/file named in --exclude
    (whole path parts, so "locale" does not drop "middleware/locale.py")."""
    if exclude and any(e in path.split("/") for e in exclude):
        return False
    return not under or path.startswith(tuple(under))


def cycles_report(
    evidence: dict, repo_path: Path, exclude: list[str] | None = None, under: list[str] | None = None
) -> str:
    exclude = exclude or []
    under = [u + "/" for u in (u.removeprefix("./").strip("/") for u in (under or [])) if u not in ("", ".")]
    adj = _build_adjacency(evidence)
    groups = sorted(
        (sorted(comp) for comp in _tarjan_sccs(adj) if len(comp) > 1),
        key=lambda g: (-len(g), g[0]),
    )

    scan_time = evidence.get("scanned_at")
    all_paths = [p for g in groups for p in g]
    changed = _files_changed_since_scan(repo_path, all_paths, scan_time)
    if changed is None:
        fresh = "freshness unknown (the evidence has no readable scan time), so re-scan if the code changed"
    elif not changed:
        fresh = "no file in a reported group changed since the scan"
    else:
        extra = f", {', '.join(changed[:3])}{', ...' if len(changed) > 3 else ''}"
        fresh = f"STALE: {len(changed)} file(s) changed after the scan{extra}; re-scan before relying on this"

    if not groups:
        return (
            "no circular import dependency found.\n\n"
            f"Coverage: static imports only, {len(adj)} modules considered. {fresh}."
        )

    if exclude or under:
        # Scoped answer: cycles are still found over the whole graph (a cycle may run through files outside the scope),
        # but only files inside the scope are listed, as one flat answer-ready list.
        in_scope = sorted({p for g in groups for p in g if _scoped(p, exclude, under)})
        lines = [f"{len(in_scope)} file(s) in the requested scope are part of an import cycle:", *in_scope, ""]
        for group in groups:
            mine = [p for p in group if _scoped(p, exclude, under)]
            if mine:
                chain = _example_cycle(adj, group)
                arrow = " -> ".join(chain[:CYCLE_MAX_CHAIN_LEN]) + (" -> ..." if len(chain) > CYCLE_MAX_CHAIN_LEN else "")
                lines.append(f"group of {len(group)} file(s) repo-wide, {len(mine)} in scope; example cycle: {arrow}")
        lines.append(
            f"\nCoverage: static imports only, {len(adj)} modules considered; cycles were found over the whole repo and "
            f"only files matching --under/--exclude are listed. Not tracked: imports built at run time (importlib, "
            f"__import__, string-based plugin loading). {fresh}. For static imports this list does not need to be "
            "re-derived with grep or a script."
        )
        return "\n".join(lines)

    shown = groups[:CYCLES_MAX_GROUPS]
    truncated = len(groups) - len(shown)
    lines = [f"{len(groups)} circular dependency group(s) found" + (f" (showing first {len(shown)})" if truncated else "") + ":"]
    for group in shown:
        chain = _example_cycle(adj, group)
        chain_shown = chain[:CYCLE_MAX_CHAIN_LEN]
        arrow = " -> ".join(chain_shown)
        if len(chain) > CYCLE_MAX_CHAIN_LEN:
            arrow += f" -> ... ({len(chain) - CYCLE_MAX_CHAIN_LEN} more hops)"
        lines.append(f"\n{len(group)} file(s): {', '.join(group)}\nexample cycle: {arrow}")
    if truncated:
        lines.append(f"\n... {truncated} more group(s) not shown")
    lines.append(
        f"\nCoverage: static imports only, {len(adj)} modules considered. Not tracked: imports built at run time "
        f"(importlib, __import__, string-based plugin loading) - those cannot create a cycle this can see, and "
        f"cannot be broken by fixing one this reports either. {fresh}."
    )
    return "\n".join(lines)
