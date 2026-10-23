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

_TEST_PATH = re.compile(
    r"(^|/)(tests?|testing|__tests__|spec)/|_test\.|\.test\.|\.spec\.|(^|/)test_[^/]*$|/testdata/|conftest"
    # PascalCase test files and test projects: FooTests.cs, FooTest.java, UnitTests/, Foo.Tests/
    r"|[A-Za-z0-9]Tests?\.(?:cs|java|kt|kts|php|scala|swift)$|(^|/)[\w.\-]*Tests?/"
)
_EXT = re.compile(r"\.[A-Za-z0-9]+$")


class TestsForError(Exception):
    """The request cannot be answered (unknown file); the message is what the user should see."""

    # Starts with "Test", pytest's default class-collection prefix - this stops it being probed as a test class
    # (it has no no-args __init__ pytest could use anyway, but this keeps the warning out of a clean test run).
    __test__ = False


def _is_test_path(path: str) -> bool:
    return bool(_TEST_PATH.search(path))


def _direct_importers(by_path: dict, target: str) -> set[str]:
    return {p for p in (by_path[target].get("imported_by") or []) if p != target}


def _transitive_importers(by_path: dict, target: str) -> list[str]:
    """Every module that imports `target`, directly or through other imports - same walk as `dependents`.

    Measured against a real call-tracer oracle (which test files actually execute a function from the target
    file, not just import something that imports it) on click/marshmallow/attrs/rich: the transitive walk alone
    has perfect recall but terrible precision (as low as 0.06) - a package's own `__init__.py` is both imported
    by nearly every test file AND imports the target, so almost the whole test suite becomes a "transitive
    importer" of almost everything. No fixed depth cap or fan-in cutoff tested fixed this uniformly across repos
    (different repos need different thresholds), so `tests_for_report` reports this set separately, clearly
    labeled as lower-confidence, instead of silently blending it into one undifferentiated list.
    """
    direct = _direct_importers(by_path, target)
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
            mtime = (Path(repo_path) / rel).stat().st_mtime
        except OSError:
            # Could not check - most commonly because the file (often a listed test file) was deleted after the
            # scan. "Could not check" must never look like "nothing changed", so this counts as changed too.
            changed.append(rel)
            continue
        if mtime > scan_time:
            changed.append(rel)
    return changed


def tests_for_report(evidence: dict, repo_path: Path, target: str) -> str:
    modules = evidence["repository"]["modules"]
    by_path = {m["path"]: m for m in modules if m.get("path")}
    target = target.removeprefix("./")
    if target not in by_path:
        close = difflib.get_close_matches(target, list(by_path), n=3)
        hint = f"; closest: {', '.join(close)}" if close else ""
        raise TestsForError(f"{target!r} is not a module in the scan{hint}")

    direct_importers = _direct_importers(by_path, target)
    via_imports_direct = {p for p in direct_importers if _is_test_path(p)}
    via_imports_transitive = {p for p in _transitive_importers(by_path, target) if _is_test_path(p)}
    stem = PurePosixPath(target).stem
    via_naming = {
        m["path"]
        for m in modules
        if m.get("path") and m["path"] != target and _matches_naming_convention(stem, m["path"])
    }

    # Split into two confidence tiers rather than one blended list: a direct import or a naming match is a strong
    # signal, but a purely transitive import (reachable only through other files, often a package's own
    # __init__.py) is not - see _transitive_importers' docstring for the measured precision gap.
    high_confidence = sorted(via_imports_direct | via_naming)
    lower_confidence = sorted(via_imports_transitive - set(high_confidence))
    both = sorted({*high_confidence, *lower_confidence})

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

    lines: list[str] = []
    if high_confidence:
        lines.append(f"{len(high_confidence)} test file(s) likely exercise {target} (direct import or naming match):")
        lines.extend(high_confidence)
    else:
        lines.append(f"no test file directly imports or is named for {target}, but:")
    if lower_confidence:
        if high_confidence:
            lines.append("")
        lines.append(
            f"{len(lower_confidence)} more test file(s) reach it only through other imports - lower confidence, "
            "some may not actually run code from this file (see Coverage):"
        )
        lines.extend(lower_confidence)
    lines.append("")
    lines.append(
        f"Coverage: {len(via_imports_direct)} found via direct import, {len(via_naming)} via filename convention "
        f"(test_{stem}, {stem}_test, {stem}.test, {stem}.spec), {len(lower_confidence)} more reachable only "
        "transitively (shown separately above - a transitive import, often through a package's own __init__.py, "
        "does not prove the file's code actually runs). Not tracked: tests that reach this file only through a "
        f"fixture, a dynamic import, or a parametrize id that never names it or matches its filename. {fresh}."
    )
    return "\n".join(lines)
