"""`aletheore dependents <file>`: every file that imports a file, directly or through a chain of other imports, as one
complete, deduplicated, sorted list with a footer that says how far it can be trusted.

An agent that is handed only "direct importers, once per import statement" has to loop, merge and double-check with
grep. This answers the whole question in one call and states what it covers so the answer can be used as is.
"""

from __future__ import annotations

import difflib
from datetime import datetime
from pathlib import Path

from aletheore.scope_filter import in_scope, is_test_path, normalize_under  # noqa: F401  (is_test_path re-exported)


class DependentsError(Exception):
    """The request cannot be answered (unknown file); the message is what the user should see."""


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
            if (Path(repo_path) / rel).stat().st_mtime > scan_time:
                changed.append(rel)
        except OSError:
            continue
    return changed


def dependents_of(evidence: dict, target: str | set[str]) -> tuple[list[str], set[str]]:
    """(all transitive dependents, the direct ones), none of them one of the seeds. `target` is one file or a set of
    files treated as one unit (every file of a package)."""
    seeds = {target} if isinstance(target, str) else set(target)
    by_path = {m["path"]: m for m in evidence["repository"]["modules"] if m.get("path")}
    direct = {p for s_ in seeds for p in (by_path[s_].get("imported_by") or []) if p not in seeds}
    seen = set(direct)
    queue = sorted(direct)
    while queue:
        for dep in by_path.get(queue.pop(), {}).get("imported_by") or []:
            if dep not in seeds and dep not in seen:
                seen.add(dep)
                queue.append(dep)
    return sorted(seen), direct


def _dir_of(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else "."


def dependents_report(
    evidence: dict, repo_path: Path, target: str, exclude: list[str] | None = None, under: list[str] | None = None,
    no_tests: bool = False, packages: bool = False,
) -> str:
    modules = evidence["repository"]["modules"]
    by_path = {m["path"]: m for m in modules if m.get("path")}
    target = target.removeprefix("./").rstrip("/") or "."
    if target in by_path:
        seeds = {target}
        unit = "file"
    else:
        seeds = {p for p in by_path if _dir_of(p) == target}
        unit = "package"
        if not seeds:
            close = difflib.get_close_matches(target, list(by_path) + sorted({_dir_of(p) for p in by_path}), n=3)
            hint = f"; closest: {', '.join(close)}" if close else ""
            raise DependentsError(f"{target!r} is not a module or a directory with modules in the scan{hint}")

    allp, direct = dependents_of(evidence, seeds)
    exclude = exclude or []
    prefixes = normalize_under(under)
    shown = [p for p in allp if in_scope(p, exclude, prefixes, no_tests)]
    dropped = len(allp) - len(shown)

    language = by_path[sorted(seeds)[0]].get("language") or "unknown"
    unparseable = len(evidence["repository"].get("unparseable_files") or [])
    scan_time = evidence.get("scanned_at")
    changed = _files_changed_since_scan(repo_path, [*sorted(seeds), *allp], scan_time)
    if changed is None:
        fresh = "freshness unknown (the evidence has no readable scan time), so re-scan if the code changed"
    elif not changed:
        fresh = "no listed file changed since the scan (files added after it are not covered)"
    else:
        extra = f", {', '.join(changed[:3])}{', ...' if len(changed) > 3 else ''}"
        who = f"the {unit} asked about and " if seeds & set(changed) else ""
        fresh = f"STALE: {who}{len(changed)} file(s) changed after the scan{extra}; re-scan before relying on this"
    if packages:
        dirs = sorted({_dir_of(p) for p in shown})
        # same unit as the header: packages that vanished entirely, not files
        dropped = len({_dir_of(p) for p in allp}) - len(dirs)
    excl = f" ({dropped} more left out by --exclude/--under/--no-tests)" if dropped else ""
    if packages:
        head = f"{len(dirs)} packages (directories) import {target}, directly or through other imports{excl}:"
        body = dirs
    else:
        head = f"{len(shown)} files import {target}, directly or through other imports{excl}:"
        body = shown
    what = "this file" if unit == "file" else f"this package ({len(seeds)} files)"
    lines = [
        head,
        *body,
        "",
        f"Repo-wide {what} has {len(direct)} direct importers plus {len(allp) - len(direct)} that reach it through "
        f"other files (before --under/--exclude), deduplicated. Coverage: static {language} imports only, "
        f"{len(modules)} modules indexed, {unparseable} unparseable; {fresh}. Not tracked: imports built at run time "
        "(importlib, __import__, string-based plugin loading). For static imports this list does not need to be "
        "re-derived with grep.",
    ]
    return "\n".join(lines)
