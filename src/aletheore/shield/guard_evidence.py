"""Shield guard checks that read the scan evidence: what a change reaches, which signatures moved, which risks are new.

Hard rules, from the hook-contract spike (a full re-parse takes 2.5 s on a 976-file repo, 9 s on 8k files, and a hook
has 2 s):

- Nothing here ever re-scans inline. Checks read `.aletheore/air.json` as it is on disk.
- When that evidence is older than a changed file, the checks that would be wrong on stale data (dependents,
  signatures, new risks) are skipped and say so in `not_checked`; a stale graph never produces a "no dependents".
  A detached refresh is started instead (`start_background_refresh`), at most once a minute, except that a
  finished refresh lets the next one start at once for a file edited after it began.
- The refresh marker records when the last refresh started; the refresh process passes that start back in a
  completion record with the nanosecond mtime of the evidence it wrote. While `air.json` still has that mtime, every
  file modified before the start is in the evidence (the refresh read files after it started). Otherwise (no record,
  or another writer rewrote `air.json`) freshness falls back to `scanned_at` minus a 15 s margin.
- Messages state facts with counts and names and give no advice. Signature findings are file-level: they name the
  files that import the module, never claim anything about call sites.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import shlex
import stat
import subprocess  # nosec B404 - starts this package's own refresh module with a fixed argument list, no shell
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from aletheore.shield import state
from aletheore.shield.fsutil import debug_note
from aletheore.shield.gitutil import UNLIMITED, Budget
from aletheore.shield.locking import file_lock

# The evidence readers and diff helpers (aletheore.evidence, dependents, evidence_diff, signature_diff, tests_for)
# cost about 400 ms to import. They are imported inside the functions that use them, so the per-edit hook can call
# save_base_evidence without paying for them, and a repository with no scan never loads them at all.

REFRESH_MIN_INTERVAL_S = 60.0
# A refresh whose marker is younger than this and whose evidence has not been rewritten since it started is taken to
# be still running, and no second one starts; an older one is taken to have died, so it cannot block forever.
REFRESH_RUNNING_TIMEOUT_S = 120.0
# `scanned_at` is stamped when a scan ENDS, 2.5-9 s after it began reading files, so a file edited during the scan
# has an mtime before `scanned_at` yet is missing from the evidence. A file counts as newer than the evidence when
# it was modified later than `scanned_at` minus this margin.
STALENESS_MARGIN_S = 15.0
_MARKER_NAME = "refresh.marker"
_DONE_NAME = "refresh.done"  # written by the refresh process itself: its start and the evidence mtime it produced
_LIST_CAP = 5
_SIGNATURE_FINDINGS_CAP = 5
_EVIDENCE_REL = Path(".aletheore") / "air.json"


@dataclass(frozen=True)
class Finding:
    check: str  # "dependencies" | "affected" | "signatures" | "risks"
    severity: str  # "warn" | "info"
    message: str
    evidence: str


def _cap(items: list[str], limit: int = _LIST_CAP) -> str:
    shown = ", ".join(items[:limit])
    return f"{shown} and {len(items) - limit} more" if len(items) > limit else shown


# --------------------------------------------------------------------------------------------------
# Background refresh
# --------------------------------------------------------------------------------------------------


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _scan_time(scanned_at: object) -> float | None:
    try:
        return datetime.fromisoformat(str(scanned_at)).timestamp()
    except ValueError:
        return None


def _safe_relative(rel: str) -> str | None:
    """`rel` as a clean repo-relative posix path, or None when it is absolute or climbs out with `..`."""
    normalised = rel.replace("\\", "/")
    path = PurePosixPath(normalised)
    if path.is_absolute() or ".." in path.parts or (len(normalised) > 1 and normalised[1] == ":"):
        return None
    return normalised


def _refresh_started_at(marker: Path) -> float | None:
    """The start time the refresh marker records, or None when it is missing, empty or not a finite number."""
    try:
        value = float(marker.read_text(encoding="utf-8").strip())
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return value if math.isfinite(value) else None


def _evidence_mtime_ns(repo: Path) -> int | None:
    try:
        return (Path(repo) / _EVIDENCE_REL).stat().st_mtime_ns
    except OSError:
        return None


def evidence_mtime_ns(repo: Path) -> int | None:
    """The nanosecond mtime of `.aletheore/air.json`; None when it cannot be read. The refresh takes it right after
    its own evidence write, before the (possibly long) index build, so another writer's later file is not recorded
    as Shield's."""
    return _evidence_mtime_ns(repo)


def record_refresh_finished(repo: Path, started: float, mtime_ns: int | None) -> None:
    """Called by Shield's refresh process after its rebuild returned: record its start time and `mtime_ns`, the
    mtime of the evidence it wrote, taken right after that write (atomically replaced). Records nothing when the
    start is unusable or no evidence write was seen."""
    if not isinstance(mtime_ns, int) or isinstance(mtime_ns, bool) or not math.isfinite(started):
        return
    done = state.shield_dir(repo) / _DONE_NAME
    temporary = done.with_name(f"{done.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    try:
        temporary.write_text(json.dumps({"started": started, "evidence_mtime_ns": mtime_ns}), encoding="utf-8")
        os.replace(temporary, done)
    except OSError as error:
        debug_note("could not record the finished refresh", error)
        try:
            temporary.unlink()
        except OSError as cleanup:
            debug_note("could not remove a temporary file", cleanup)


def _finished_refresh_start(repo: Path) -> float | None:
    """When the last Shield refresh started, only if that refresh finished and wrote the evidence on disk now.

    The completion record its own process writes must name the current `air.json` mtime (to the nanosecond). Any
    other writer (the MCP watcher, `aletheore watch`, a manual scan) changes that mtime, and its scan may have read
    files before the refresh started, so then there is no trusted start and the caller falls back to the margin."""
    try:
        data = json.loads((state.shield_dir(repo) / _DONE_NAME).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        started, recorded = data.get("started"), data.get("evidence_mtime_ns")
        if isinstance(started, bool) or not isinstance(started, (int, float)):
            return None
        started = float(started)  # OverflowError for an absurdly large integer
        if not math.isfinite(started) or isinstance(recorded, bool) or not isinstance(recorded, int):
            return None
        if started > time.time() or recorded != _evidence_mtime_ns(repo):
            return None
        return started
    except FileNotFoundError:
        return None
    except Exception as error:  # noqa: BLE001 - corrupt or hostile content (RecursionError, OverflowError, ...)
        debug_note("ignoring an unreadable refresh completion record", error)
        return None


def _refresh_finished(repo: Path, started: float) -> bool:
    """Whether the refresh that recorded `started` in the marker has written its completion record."""
    finished = _finished_refresh_start(repo)
    return finished is not None and finished == started


def _covered_until(repo: Path, scan_time: float) -> float:
    """Files modified at or before this time are in the evidence: the start of a refresh that has finished since, or
    `scanned_at` minus the margin when there is no such refresh (or it is older than that)."""
    started = _finished_refresh_start(repo)
    fallback = scan_time - STALENESS_MARGIN_S
    return fallback if started is None else max(started, fallback)


def _write_marker(marker: Path, started: float) -> bool:
    """Create the marker holding `started`, only if it does not exist yet; True when this caller created it.

    The content goes into a private temporary file first and is hard-linked into place, so the marker never exists
    without its start time; where hard links are unavailable it falls back to an exclusive create and write."""
    temporary = marker.with_name(f"{marker.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    try:
        temporary.write_text(repr(started), encoding="utf-8")
        try:
            os.link(temporary, marker)
            return True
        except FileExistsError:
            return False
        except OSError:
            pass
        try:
            descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(repr(started))
        return True
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            debug_note("could not remove a temporary file", error)


def _claim_marker_unlocked(marker: Path, newest_edit: float | None = None) -> bool:
    """Atomically take the refresh marker and record the start time in it; False when a refresh may not start.

    A previous refresh blocks a new one while it is still running (it has not written its completion record; see
    `_finished_refresh_start`) and is younger than REFRESH_RUNNING_TIMEOUT_S, so one that crashed stops blocking
    after that. One that has finished blocks a new one for REFRESH_MIN_INTERVAL_S, unless a changed file was
    modified after it started (`newest_edit`). A marker without a readable start time falls back to its mtime.

    Called under `<marker>.lock` (see `_claim_marker`) and judges the marker as it is inside the lock, so of many
    callers that all saw the same dead marker only the first replaces it; the others then see its fresh start and
    decline."""
    from aletheore.shield.fsutil import atomic_write_text

    now = time.time()
    if _write_marker(marker, now):
        return True
    started = _refresh_started_at(marker)
    if started is None:
        started = _mtime(marker)
    if started is None:
        if not os.path.lexists(marker):
            return _write_marker(marker, now)  # gone meanwhile: create it, as above
        # There, but neither its start time nor its mtime can be read: replace it (this caller holds the lock), so a
        # corrupt marker never blocks refreshes for good.
        atomic_write_text(marker, repr(now))
        return True
    age = now - started
    # marker.parent is <repo>/.aletheore/shield
    running = not _refresh_finished(marker.parent.parent.parent, started)
    if running:
        if 0 <= age < REFRESH_RUNNING_TIMEOUT_S:
            return False
    elif not (newest_edit is not None and newest_edit > started):
        if 0 <= age < REFRESH_MIN_INTERVAL_S:
            return False
    atomic_write_text(marker, repr(now))  # replaced in one step: never absent, never without its start time
    return True


def _claim_marker(marker: Path, newest_edit: float | None = None) -> bool:
    """`_claim_marker_unlocked` under a bounded OS lock; False when the lock is busy (another caller is deciding)."""
    try:
        with file_lock(marker.with_suffix(".lock")) as acquired:
            return acquired and _claim_marker_unlocked(marker, newest_edit)
    except OSError as error:
        debug_note("could not lock the refresh marker", error)
        return False


def start_background_refresh(
    repo: Path, changed: list[str] | None = None, scanned_at: object = None, *, scanned_modules: set[str] | None = None
) -> bool:
    """Start a detached re-scan of `repo`; True only when a process was started.

    Does nothing when the repo has no evidence yet (the first scan is the user's to run; this never creates
    `.aletheore/`), when the evidence file is already newer than every changed file given, or when a refresh was
    started within the last 60 seconds (a marker file in Shield's state directory, which also records when the
    refresh started), so a burst of edits cannot spawn a burst of scans. A refresh still running blocks a new one for
    up to two minutes; a finished one lets a new one start at once when a changed file was modified after it began.

    A changed file that no longer exists has no modification time. When it is one of `scanned_modules` (the evidence
    still lists it), the evidence predates its deletion, so it always counts as a reason to refresh (still at most
    once per marker window), even when every changed file was deleted; one the scan never listed is no reason. A
    rescan drops a deleted file from the evidence, so it stops being a reason once a refresh has run.
    """
    repo = Path(repo)
    evidence_file = repo / _EVIDENCE_REL
    evidence_time = _mtime(evidence_file)
    if evidence_time is None:
        return False
    newest_edit = None
    if changed:
        safe = [r for r in (_safe_relative(c) for c in changed) if r is not None]
        times = {rel: _mtime(repo / rel) for rel in safe}
        edit_times = [t for t in times.values() if t is not None]
        deleted_in_scan = [rel for rel, t in times.items() if t is None and rel in (scanned_modules or ())]
        reference = _scan_time(scanned_at)
        if reference is None:
            reference = evidence_time  # written at the end of the scan, so never earlier than scanned_at
        if not deleted_in_scan:  # a deleted file the scan still lists is a reason on its own, whatever else changed
            if not edit_times:
                return False  # every changed file is gone and the scan never listed any of them: nothing is stale
            if _covered_until(repo, reference) >= max(edit_times):
                return False
        if edit_times:
            newest_edit = max(edit_times)
    marker = state.shield_dir(repo) / _MARKER_NAME
    try:
        state.shield_dir(repo, create=True)
        if not _claim_marker(marker, newest_edit):
            return False
    except OSError:
        return False  # without the marker the storm guard cannot work, so start nothing
    started = _refresh_started_at(marker)
    if started is None:
        return False  # the marker this call just wrote is gone or unreadable: the refresh could not report back
    options: dict = {"start_new_session": True}
    if os.name == "nt":
        options = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    try:
        subprocess.Popen(  # noqa: S603  # nosec B603 - fixed argv, our own module, no shell
            [sys.executable, "-m", "aletheore.shield.refresh", str(repo), repr(started)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **options,
        )
    except OSError:
        return False
    return True


# --------------------------------------------------------------------------------------------------
# Base evidence: the scan as it was when the session started
# --------------------------------------------------------------------------------------------------


def save_base_evidence(repo: Path, session_id: str) -> Path | None:
    """Copy `.aletheore/air.json` to the session's base-evidence file, once; later calls keep the first copy.
    None when the session id is unusable, there is no evidence to copy, or the copy failed."""
    destination = state.session_base_evidence_path(repo, session_id)
    source = Path(repo) / _EVIDENCE_REL
    if destination is None or not source.is_file():
        return None
    if destination.exists():
        return destination
    temporary = destination.with_name(destination.name + f".{os.getpid()}.tmp")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    except OSError as error:
        debug_note("could not save the session's base evidence", error)
        try:
            temporary.unlink()
        except OSError as cleanup:
            debug_note("could not remove a temporary file", cleanup)
        return None
    return destination


def load_base_evidence(repo: Path, session_id: str) -> dict | None:
    """The session's saved base evidence, or None when it is missing, unreadable or not valid evidence."""
    path = state.session_base_evidence_path(repo, session_id)
    if path is None or not path.is_file():
        return None
    try:
        from aletheore import evidence as evidence_io

        return evidence_io.load_evidence_file(path)
    except Exception as error:  # noqa: BLE001  # corrupt, truncated or incompatible: all mean "no base"
        debug_note("ignoring unreadable base evidence", error)
        return None


def delete_base_evidence(repo: Path, session_id: str) -> None:
    """Remove the session's base-evidence copy (at session end). Missing files and unusable ids are no-ops."""
    path = state.session_base_evidence_path(repo, session_id)
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as error:
        debug_note("could not delete the base evidence", error)


BASE_EVIDENCE_MAX_AGE_S = 7 * 24 * 3600.0
_BASE_EVIDENCE_FILE_SUFFIX = ".base-air.json"


def prune_base_evidence(repo: Path, *, max_age_s: float = BASE_EVIDENCE_MAX_AGE_S) -> int:
    """Delete base-evidence copies older than `max_age_s` left by sessions that never reached session end.

    Only regular files directly in Shield's sessions folder whose name is exactly the one
    `state.session_base_evidence_path` gives a valid session id are touched; returns how many were removed."""
    sessions = state.shield_dir(repo) / "sessions"
    try:
        entries = list(os.scandir(sessions))
    except OSError:
        return 0
    cutoff = time.time() - max_age_s
    removed = 0
    for entry in entries:
        name = entry.name
        if not name.endswith(_BASE_EVIDENCE_FILE_SUFFIX):
            continue
        token = name[: -len(_BASE_EVIDENCE_FILE_SUFFIX)]
        expected = state.session_base_evidence_path(repo, token)
        if expected is None or expected.name != name:
            continue
        try:
            if not entry.is_file(follow_symlinks=False) or entry.stat(follow_symlinks=False).st_mtime >= cutoff:
                continue
            os.unlink(entry.path)
            removed += 1
        except OSError:
            continue
    return removed


# --------------------------------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------------------------------


def _relevant_changed(repo: Path, changed: list[str], known: set[str]) -> list[str]:
    """Inputs to source and secret checks, including known files deleted since the scan."""
    from aletheore.scanner.detect import IGNORED_DIRS
    from aletheore.secrets import BINARY_EXTENSIONS, MAX_SCANNED_FILE_BYTES
    from aletheore.repo_config import is_ignored, load_repo_config

    patterns = load_repo_config(repo)["ignored_paths"]
    relevant = []
    for raw in changed:
        rel = _safe_relative(raw)
        if rel is None:
            continue
        path = PurePosixPath(rel)
        if set(path.parts) & set(IGNORED_DIRS) or is_ignored(rel, patterns):
            continue
        if rel in known:
            relevant.append(rel)
            continue
        try:
            info = (Path(repo) / rel).lstat()
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode) and path.suffix not in BINARY_EXTENSIONS and info.st_size <= MAX_SCANNED_FILE_BYTES:
            relevant.append(rel)
    return relevant


def _files_newer_than(repo: Path, files: list[str], scanned_at: object) -> list[str] | None:
    """Files modified after the scan; None when the scan time is unreadable."""
    scan_time = _scan_time(scanned_at)
    if scan_time is None:
        return None
    covered = _covered_until(repo, scan_time)
    newer = []
    for rel in files:
        modified = _mtime(Path(repo) / rel)
        if modified is None or modified > covered:
            newer.append(rel)
    return newer


def _has_pytest_config(repo: Path) -> bool:
    def text(name: str) -> str:
        try:
            return (Path(repo) / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    return (
        "[tool.pytest" in text("pyproject.toml")
        or (Path(repo) / "pytest.ini").is_file()
        or "[pytest]" in text("tox.ini")
        or "[tool:pytest]" in text("setup.cfg")
    )


def _affected_finding(repo: Path, head: dict, targets: list[str]) -> Finding | None:
    from aletheore import dependents
    from aletheore.scope_filter import is_test_path
    from aletheore.tests_for import candidate_tests

    reached, direct = dependents.dependents_of(head, set(targets))
    reached = [p for p in reached if not is_test_path(p)]
    direct_count = len([p for p in direct if not is_test_path(p)])
    high: set[str] = set()
    low: set[str] = set()
    for target in targets:
        h, lo = candidate_tests(head, target)
        high.update(h)
        low.update(lo)
    high_list = sorted(high)
    low_list = sorted(low - high)
    if not reached and not high_list and not low_list:
        return None

    subject = targets[0] if len(targets) == 1 else f"{len(targets)} changed files"
    verb = "is" if len(targets) == 1 else "are"
    sentences = []
    if reached:
        sentences.append(
            f"{subject} {verb} imported by {len(reached)} file(s) ({direct_count} directly): {_cap(reached)}."
        )
    if high_list:
        text = f"Test files that import it or are named for it: {_cap(high_list)}."
        python_tests = [p for p in high_list if p.endswith(".py")]
        if python_tests and _has_pytest_config(repo):
            argv = ["pytest", *python_tests[:_LIST_CAP]]
            command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
            if len(python_tests) > _LIST_CAP:
                command += f" (and {len(python_tests) - _LIST_CAP} more test files)"
            text += f" Command: {command}."
        sentences.append(text)
    if low_list:
        sentences.append(f"{len(low_list)} more test file(s) reach it only through other imports.")
    return Finding("affected", "info", " ".join(sentences), "import graph in .aletheore/air.json")


def _signature_findings(base: dict, head: dict, changed: set[str]) -> list[Finding]:
    from aletheore.signature_diff import find_changed_signatures

    by_path = {m["path"]: m for m in head["repository"]["modules"] if m.get("path")}
    moved = [c for c in find_changed_signatures(base, head) if c["file"] in changed]
    findings = []
    for change in moved[:_SIGNATURE_FINDINGS_CAP]:
        imported_by = by_path.get(change["file"], {}).get("imported_by") or []
        importers = sorted(p for p in imported_by if p != change["file"])
        if importers:
            who = f"file-level: each of {len(importers)} file(s) imports the module {change['file']}: {_cap(importers)}"
        else:
            who = "no file in the scan imports the module"
        findings.append(
            Finding(
                "signatures",
                "warn",
                f"{change['function']} in {change['file']} changed parameters from {change['old_params']} to "
                f"{change['new_params']}; {who}.",
                "function parameters in session-start vs current .aletheore/air.json",
            )
        )
    if len(moved) > _SIGNATURE_FINDINGS_CAP:
        rest = moved[_SIGNATURE_FINDINGS_CAP:]
        findings.append(
            Finding(
                "signatures",
                "warn",
                f"{len(rest)} more changed function signature(s): "
                + _cap([f"{c['function']} in {c['file']}" for c in rest]) + ".",
                "function parameters in session-start vs current .aletheore/air.json",
            )
        )
    return findings


def _cycle_text(cycle: list[str]) -> str:
    if not cycle:
        return "?"
    closed = cycle if cycle[0] == cycle[-1] else [*cycle, cycle[0]]
    return " -> ".join(closed)


RENAME_SIMILARITY = 0.5  # git's own default for rename detection
_RENAME_MAX_BYTES = 1024 * 1024
_RENAME_MAX_LINES = 5000  # beyond this only difflib's cheap upper bound is used


def _similar(before: bytes, after: bytes) -> bool:
    import difflib

    old_lines, new_lines = before.splitlines(), after.splitlines()
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    if matcher.real_quick_ratio() < RENAME_SIMILARITY or matcher.quick_ratio() < RENAME_SIMILARITY:
        return False
    if max(len(old_lines), len(new_lines)) > _RENAME_MAX_LINES:
        return True
    return matcher.ratio() >= RENAME_SIMILARITY


def _rename_judge(repo: Path, budget: Budget):
    """`renamed(gone, came)` for the cycle comparison: the same file name in another folder, or the deleted file's
    committed text (HEAD) and the new file's text at least RENAME_SIMILARITY alike. Anything that cannot be read in
    time is not a rename, so a loop closed by an unrelated new file is still reported."""
    from aletheore import evidence_diff
    from aletheore.shield.gitutil import cat_file_batch

    verdicts: dict[tuple[str, str], bool] = {}

    def renamed(gone: str, came: str) -> bool:
        if evidence_diff.same_file_name(gone, came):
            return True
        if (gone, came) not in verdicts:
            verdict = False
            if not budget.spent(MIN_STAGE_S):
                name = f"HEAD:{gone}"
                answer = cat_file_batch(repo, [name], timeout=budget.timeout(2.0)) or {}
                before = answer.get(name)
                try:
                    target = repo / came
                    after = target.read_bytes() if target.stat().st_size <= _RENAME_MAX_BYTES else None
                except OSError:
                    after = None
                if before is not None and after is not None and len(before) <= _RENAME_MAX_BYTES:
                    verdict = _similar(before, after)
            verdicts[(gone, came)] = verdict
        return verdicts[(gone, came)]

    return renamed


def _risk_findings(base: dict, head: dict, renamed=None) -> list[Finding]:
    """New secrets and import cycles. Vulnerability and layer-violation findings can only appear when the head
    evidence came from a full `aletheore scan`: Shield's first scan has vulnerabilities off, and the background
    refresh carries both forward from the last full scan. `renamed` judges renamed cycle members (see
    `_rename_judge`)."""
    from aletheore import evidence_diff

    risks = evidence_diff.new_risks(base, head, renamed)
    source = "session-start vs current .aletheore/air.json"
    findings = []
    if risks["secrets"]:
        names = [f"{s.get('path')}:{s.get('line')} ({s.get('pattern')})" for s in risks["secrets"]]
        findings.append(Finding("risks", "warn", f"{len(names)} new secret finding(s): {_cap(names)}.", source))
    if risks["vulnerabilities"]:
        names = [f"{v.get('package')} {v.get('advisory_id')}" for v in risks["vulnerabilities"]]
        findings.append(
            Finding("risks", "warn", f"{len(names)} new dependency vulnerability finding(s): {_cap(names)}.", source)
        )
    if risks["layer_violations"]:
        names = [f"{v.get('from')} -> {v.get('to')}" for v in risks["layer_violations"]]
        findings.append(Finding("risks", "warn", f"{len(names)} new layer violation(s): {_cap(names)}.", source))
    if risks["cycles"]:
        names = [_cycle_text(c.get("example_cycle") or c.get("members") or []) for c in risks["cycles"]]
        findings.append(Finding("risks", "warn", f"{len(names)} new import cycle(s): {_cap(names, 3)}.", source))
    return findings


MIN_STAGE_S = 0.05  # with less time than this left, a check is not started
EVIDENCE_NOT_RUN = "time budget reached; evidence checks were not run (dependents, signature and new-risk checks)"


def _out_of_time(stages: str) -> str:
    return f"time budget reached; {stages} skipped"


def evidence_checks(
    repo: Path, changed: list[str], base_evidence: dict | None, *, budget: Budget = UNLIMITED
) -> tuple[list[Finding], list[str]]:
    """(findings, not_checked reasons) for `changed` (repo-relative posix paths) from the evidence on disk.

    Never re-scans inline. When the evidence predates a changed file the stale-sensitive checks are skipped, a
    detached refresh is requested, and the reason says so. `budget` is the guard run's: a check that would start with
    no time left is skipped and named ("time budget reached; <checks> skipped") instead of overrunning the hook.
    """
    repo = Path(repo)
    if budget.spent(MIN_STAGE_S):
        return [], [EVIDENCE_NOT_RUN]
    try:
        if not (repo / _EVIDENCE_REL).exists():
            raise FileNotFoundError(str(repo / _EVIDENCE_REL))  # answered without importing the evidence reader
        from aletheore import evidence as evidence_io

        head = evidence_io.load_evidence(repo)
    except FileNotFoundError:
        return [], [
            "no scan evidence (.aletheore/air.json); run aletheore scan to enable dependents, signature and "
            "risk checks"
        ]
    except Exception as exc:  # noqa: BLE001
        return [], [f"scan evidence could not be read ({type(exc).__name__}); run aletheore scan"]

    try:
        known = {m["path"] for m in head["repository"]["modules"] if m.get("path")}
        secret_paths = {f.get("path") for f in head.get("security", {}).get("secrets", {}).get("findings", [])}
    except (AttributeError, KeyError, TypeError):
        return [], ["scan evidence has an unexpected shape; run aletheore scan"]

    newer = _files_newer_than(repo, _relevant_changed(repo, changed, known | secret_paths), head.get("scanned_at"))
    if newer is None:
        return [], ["scan evidence has no readable scan time, so its age cannot be checked; run aletheore scan"]
    if newer:
        started = start_background_refresh(repo, newer, head.get("scanned_at"), scanned_modules=known)
        followup = "a refresh was started" if started else "run aletheore scan"
        return [], [
            f"evidence is older than {len(newer)} changed file(s); dependents, signature and new-risk checks "
            f"skipped ({followup})"
        ]

    findings: list[Finding] = []
    not_checked: list[str] = []
    targets = sorted({c for c in changed if c in known})
    try:
        if targets:
            if budget.spent(MIN_STAGE_S):
                return findings, [_out_of_time("dependents, signature and new-risk checks")]
            affected = _affected_finding(repo, head, targets)
            if affected:
                findings.append(affected)
        if base_evidence is None:
            not_checked.append(
                "no base evidence was saved for this session, so signature and new-risk checks were skipped"
            )
        elif budget.spent(MIN_STAGE_S):
            not_checked.append(_out_of_time("signature and new-risk checks"))
        else:
            findings.extend(_signature_findings(base_evidence, head, set(changed)))
            if budget.spent(MIN_STAGE_S):
                not_checked.append(_out_of_time("new-risk check"))
            else:
                findings.extend(_risk_findings(base_evidence, head, _rename_judge(repo, budget)))
    except (KeyError, TypeError, ValueError) as exc:
        not_checked.append(f"evidence checks stopped on unexpected evidence shape ({type(exc).__name__})")
    return findings, not_checked
