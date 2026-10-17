"""`aletheore dependents <file>`: every file that imports a file, directly or through a chain of other imports, as one
complete, deduplicated, sorted list with a footer that says how far it can be trusted.

An agent that is handed only "direct importers, once per import statement" has to loop, merge and double-check with
grep. This answers the whole question in one call and states what it covers so the answer can be used as is.
"""

from __future__ import annotations

import difflib
from datetime import datetime
from pathlib import Path



def _files_changed_since_scan(repo_path: Path, paths: list[str], scanned_at: object) -> list[str]:
    """Files whose mtime is later than the scan, so a stale answer can say so."""
    try:
        scan_time = datetime.fromisoformat(str(scanned_at)).timestamp()
    except ValueError:
        return []
    changed = []
    for rel in paths:
        try:
            if (Path(repo_path) / rel).stat().st_mtime > scan_time:
                changed.append(rel)
        except OSError:
            continue
    return changed


def _matches(path: str, pattern: str) -> bool:
    """`pattern` is a whole directory name or file name ("tests", "locale"), not a substring: "locale" must not drop
    a module called django/middleware/locale.py."""
    return pattern in path.split("/")


def dependents_of(evidence: dict, target: str) -> tuple[list[str], set[str]]:
    """(all transitive dependents, the direct ones), neither containing `target`."""
    by_path = {m["path"]: m for m in evidence["repository"]["modules"] if m.get("path")}
    direct = {p for p in (by_path[target].get("imported_by") or []) if p != target}
    seen = set(direct)
    queue = sorted(direct)
    while queue:
        for dep in by_path.get(queue.pop(), {}).get("imported_by") or []:
            if dep != target and dep not in seen:
                seen.add(dep)
                queue.append(dep)
    return sorted(seen), direct


def dependents_report(
    evidence: dict, repo_path: Path, target: str, exclude: list[str] | None = None, under: list[str] | None = None
) -> str:
    modules = evidence["repository"]["modules"]
    by_path = {m["path"]: m for m in modules if m.get("path")}
    target = target.removeprefix("./")
    if target not in by_path:
        close = difflib.get_close_matches(target, list(by_path), n=3)
        hint = f"; closest: {', '.join(close)}" if close else ""
        return f"error: {target!r} is not a module in the scan{hint}"

    allp, direct = dependents_of(evidence, target)
    exclude = exclude or []
    under = [u.strip('/') + '/' for u in (under or [])]
    shown = [p for p in allp if not any(_matches(p, e) for e in exclude) and (not under or p.startswith(tuple(under)))]
    dropped = len(allp) - len(shown)

    language = by_path[target].get("language") or "unknown"
    unparseable = len(evidence["repository"].get("unparseable_files") or [])
    changed = _files_changed_since_scan(repo_path, [target, *allp], evidence.get("scanned_at"))
    fresh = "scan is current" if not changed else f"STALE: {len(changed)} of these files changed after the scan ({', '.join(changed[:3])}{', ...' if len(changed) > 3 else ''})"
    excl = f" ({dropped} more left out by --exclude/--under)" if dropped else ""
    lines = [
        f"{len(shown)} files import {target}, directly or through other imports{excl}:",
        *shown,
        "",
        f"This list is complete for static {language} imports: {len(direct)} direct importers plus "
        f"{len(allp) - len(direct)} that reach it through other files, deduplicated, {len(modules)} modules indexed, "
        f"{unparseable} unparseable; {fresh}. Not tracked: imports built at run time (importlib, __import__, "
        "string-based plugin loading). Nothing here needs to be re-derived with grep.",
    ]
    return "\n".join(lines)
