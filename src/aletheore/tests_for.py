"""`aletheore tests-for <file>`: every test file that actually exercises a file, so an agent can run those test
files instead of the whole suite. Combines two independent signals as one complete answer with a trust footer:

1. import graph - test files that import the file, directly or through other imports (same transitive walk as
   `dependents`, filtered to paths that look like tests).
2. naming convention - test_<name>.py / <name>_test.py / <name>.test.* / <name>.spec.* for the file's own name,
   found even when nothing imports the file directly (pytest fixtures, monkeypatching, and parametrized
   decorators commonly exercise a module without a static import of it).
"""

from __future__ import annotations

import difflib
import re
from datetime import datetime
from pathlib import Path, PurePosixPath

_TEST_PATH = re.compile(r"(^|/)(tests?|testing|__tests__|spec)/|_test\.|\.test\.|\.spec\.|(^|/)test_[^/]*$|/testdata/|conftest")
_EXT = re.compile(r"\.[A-Za-z0-9]+$")


class TestsForError(Exception):
    """The request cannot be answered (unknown file); the message is what the user should see."""

    # Starts with "Test", pytest's default class-collection prefix - this stops it being probed as a test class
    # (it has no no-args __init__ pytest could use anyway, but this keeps the warning out of a clean test run).
    __test__ = False


def _is_test_path(path: str) -> bool:
    return bool(_TEST_PATH.search(path))


def _transitive_importers(by_path: dict, target: str) -> list[str]:
    """Every module that imports `target`, directly or through other imports - same walk as `dependents`."""
    direct = {p for p in (by_path[target].get("imported_by") or []) if p != target}
    seen = set(direct)
    queue = sorted(direct)
    while queue:
        for dep in by_path.get(queue.pop(), {}).get("imported_by") or []:
            if dep != target and dep not in seen:
                seen.add(dep)
                queue.append(dep)
    return sorted(seen)


def _matches_naming_convention(target_stem: str, path: str) -> bool:
    name = PurePosixPath(path).name
    without_ext = _EXT.sub("", name)
    return without_ext in (f"test_{target_stem}", f"{target_stem}_test", f"{target_stem}.test", f"{target_stem}.spec")


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


def tests_for_report(evidence: dict, repo_path: Path, target: str) -> str:
    modules = evidence["repository"]["modules"]
    by_path = {m["path"]: m for m in modules if m.get("path")}
    target = target.removeprefix("./")
    if target not in by_path:
        close = difflib.get_close_matches(target, list(by_path), n=3)
        hint = f"; closest: {', '.join(close)}" if close else ""
        raise TestsForError(f"{target!r} is not a module in the scan{hint}")

    via_imports = {p for p in _transitive_importers(by_path, target) if _is_test_path(p)}
    stem = PurePosixPath(target).stem
    via_naming = {m["path"] for m in modules if m.get("path") and _matches_naming_convention(stem, m["path"])}

    both = sorted(via_imports | via_naming)
    only_naming = sorted(via_naming - via_imports)

    scan_time = evidence.get("scanned_at")
    changed = _files_changed_since_scan(repo_path, [target, *both], scan_time)
    if changed is None:
        fresh = "freshness unknown (the evidence has no readable scan time), so re-scan if the code changed"
    elif not changed:
        fresh = "no listed file changed since the scan (files added after it are not covered)"
    else:
        extra = f", {', '.join(changed[:3])}{', ...' if len(changed) > 3 else ''}"
        who = "the file asked about and " if target in changed else ""
        fresh = f"STALE: {who}{len(changed)} file(s) changed after the scan{extra}; re-scan before relying on this"

    if not both:
        return (
            f"no test file imports {target} (directly or transitively) or matches its naming convention.\n\n"
            f"Coverage: {fresh}. A test may still exercise this file through a fixture or integration path this "
            "cannot see; this says only that the two signals it checks found nothing, not that the file is untested."
        )

    naming_note = f" ({len(only_naming)} by naming convention only, no import found)" if only_naming else ""
    lines = [
        f"{len(both)} test file(s) exercise {target}{naming_note}:",
        *both,
        "",
        f"Coverage: {len(via_imports)} found via the import graph (direct or transitive), {len(via_naming)} via "
        f"filename convention (test_{stem}, {stem}_test, {stem}.test, {stem}.spec). Not tracked: tests that reach "
        "this file only through a fixture, a dynamic import, or a parametrize id that never names it or matches "
        f"its filename. {fresh}.",
    ]
    return "\n".join(lines)
