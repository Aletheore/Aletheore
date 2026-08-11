"""`aletheore watch` - keep evidence and the search index current while you work.

A scan is only true at the moment it ran. Between scans, every consumer -
`query`, the MCP server an agent is driving, the dashboard - is answering
from a snapshot of a repository that has since moved. This closes that gap
without asking anyone to remember a command.

Why OS file events rather than polling: measured on this repository, walking
and stat-ing the tree costs 841 ms for 644 files. A two-second poll would
burn roughly 40% of a core, continuously, on a laptop that is also running a
compiler and an editor - and worse on a monorepo. watchdog uses FSEvents on
macOS and inotify on Linux, so idle cost is essentially zero and cost scales
with edits rather than repository size.
"""

import os
import threading
import time
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

# Every aletheore and watchdog import in this module is deliberately inside a
# function. cli.py imports DEBOUNCE_SECONDS at module scope to use as a typer
# default, and search_index pulls in lancedb, pyarrow and pandas - importing
# any of that here would drag the whole stack into `aletheore --help` and
# every other command. test_cli.py guards exactly this.

# How long the tree must be quiet before a rebuild starts. A single save in
# an editor is one event; "format on save" across a package, a branch
# checkout, or a rebase is hundreds within a second or two. Debouncing turns
# any of those into one rebuild instead of hundreds of overlapping ones.
DEBOUNCE_SECONDS = 2.0

@lru_cache(maxsize=1)
def _watched_suffixes() -> frozenset[str]:
    """Extensions the scanner can parse.

    Read from the scanner's own table rather than restated, so a language
    added there is watched without anyone remembering to update this. Cached
    and lazy: the import is not cheap and must not happen at module scope.
    """
    from aletheore.scanner.graph import LANGUAGE_BY_EXTENSION

    return frozenset(LANGUAGE_BY_EXTENSION)


@lru_cache(maxsize=1)
def _ignored_dirs() -> frozenset[str]:
    from aletheore.scanner.detect import IGNORED_DIRS

    return frozenset(IGNORED_DIRS)


def _is_relevant(repo_path: Path, changed: Path) -> bool:
    """Whether a filesystem event's path is even a candidate for a rebuild.

    `.aletheore/` is excluded first and deliberately: a scan writes
    air.json, air.toon, scan-cache.json, a history snapshot and the LanceDB
    index into it, and without this exclusion those writes would themselves
    be candidates. This is necessary but not sufficient to stop a rebuild
    retriggering itself, though - a path outside `.aletheore/` can still be
    a false positive: `rebuild()` reads every watched file's content, and on
    Linux that read alone generates a real filesystem event (see
    _DebouncedHandler). Path is all this function can judge; _real_changes
    is what catches the rest.
    """
    try:
        relative = changed.resolve().relative_to(repo_path.resolve())
    except ValueError:
        return False
    parts = relative.parts
    if ".aletheore" in parts or any(part in _ignored_dirs() for part in parts):
        return False
    return changed.suffix in _watched_suffixes()


def _current_mtimes(repo_path: Path) -> dict[Path, float]:
    """mtime for every currently-relevant file, walked once at startup.

    This is the baseline _DebouncedHandler compares new events against - see
    _real_changes for why that comparison exists at all. os.walk with
    dirnames pruned in place, matching the scanner's own convention, rather
    than Path.rglob("*") + a post-filter: this way node_modules or .git is
    never descended into at all, not walked and thrown away.

    Paid once, not per rebuild. This is the same walk+stat this module's own
    docstring already costs at 841 ms for 644 files - the number that ruled
    out polling in the first place. Doing it once at startup rather than
    every debounce cycle is the entire point; it does not reintroduce what
    was rejected above.
    """
    mtimes: dict[Path, float] = {}
    suffixes = _watched_suffixes()
    ignored = _ignored_dirs()
    for dirpath, dirnames, filenames in os.walk(repo_path, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in ignored]
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.suffix not in suffixes or path.is_symlink():
                continue
            try:
                resolved = path.resolve()
                mtimes[resolved] = resolved.stat().st_mtime
            except OSError:
                continue
    return mtimes


class _DebouncedHandler:
    """Records that something changed; the run loop decides when to act.

    Deliberately not a FileSystemEventHandler subclass, so this module needs
    no watchdog import at module scope and the collapsing logic is testable
    without an observer. watchdog calls `dispatch`, not `on_any_event`, so
    _observer_handler below adapts this to the real interface rather than
    duck-typing it - relying on the observer to call the method we happen to
    have defined is how this silently stopped receiving events once.

    A settled batch is filtered against on-disk mtimes before it is handed
    back - see _real_changes. `.aletheore/` writes are not the only way a
    rebuild retriggers itself: `rebuild()` reads every watched file's
    content (scan_repository, and build_chunks for an existing index), and
    on Linux that read alone is a real filesystem event - inotify's IN_OPEN,
    and often IN_ATTRIB from the atime update, both indistinguishable from a
    genuine write once watchdog turns them into a FileModifiedEvent /
    FileOpenedEvent. Path-based filtering alone cannot catch this, since the
    path is legitimately a watched source file; only content actually having
    changed can.
    """

    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path
        self._lock = threading.Lock()
        self._last_event_at: float | None = None
        self._changed: set[Path] = set()
        # Built once, synchronously, before the observer starts - see
        # _current_mtimes for why this is a one-time cost rather than the
        # per-cycle poll this module exists to avoid.
        self._known_mtimes: dict[Path, float] = _current_mtimes(repo_path)

    def on_any_event(self, event) -> None:  # noqa: ANN001 - watchdog event, kept untyped to avoid the import
        if event.is_directory:
            return
        # A move reports both ends; both matter, since one file left a path
        # and another arrived at one.
        candidates = [Path(str(event.src_path))]
        destination = getattr(event, "dest_path", None)
        if destination:
            candidates.append(Path(str(destination)))
        relevant = [path for path in candidates if _is_relevant(self.repo_path, path)]
        if not relevant:
            return
        with self._lock:
            self._changed.update(relevant)
            self._last_event_at = time.monotonic()

    def take_settled_batch(self, debounce_seconds: float) -> set[Path] | None:
        """The pending changes, once the tree has been quiet long enough.

        Empty and non-empty are different outcomes here: an event batch that
        turns out, on inspection, to be entirely read-triggered noise still
        settles and clears - it must not sit in `_changed` holding the
        debounce timer hostage - it just produces no rebuild.
        """
        with self._lock:
            if self._last_event_at is None:
                return None
            if time.monotonic() - self._last_event_at < debounce_seconds:
                return None
            batch, self._changed = self._changed, set()
            self._last_event_at = None
        return self._real_changes(batch) or None

    def _real_changes(self, batch: set[Path]) -> set[Path]:
        """batch, minus any path whose mtime did not actually move.

        Reading a file - an open(), or an atime-only metadata update -
        changes neither its mtime nor its content, only a real write does.
        A path missing from disk (deleted, or a transient temp file already
        gone by the time this runs) has nothing to compare and is trusted as
        a real change; its stale baseline is dropped either way so a file
        recreated later is judged fresh rather than against a deleted
        version's mtime.
        """
        real: set[Path] = set()
        for path in batch:
            resolved = path.resolve()
            try:
                mtime = resolved.stat().st_mtime
            except OSError:
                self._known_mtimes.pop(resolved, None)
                real.add(path)
                continue
            if self._known_mtimes.get(resolved) != mtime:
                real.add(path)
            self._known_mtimes[resolved] = mtime
        return real


def _observer_handler(handler: "_DebouncedHandler"):
    """Adapt _DebouncedHandler to watchdog's real handler interface.

    Built here rather than at module scope so the watchdog import stays
    inside watch(), and subclassed rather than duck-typed because the
    observer dispatches through FileSystemEventHandler.dispatch - a plain
    object with on_any_event is accepted by schedule() and then never
    called, which is a failure that looks exactly like "no file events".
    """
    from watchdog.events import FileSystemEventHandler

    class _Adapter(FileSystemEventHandler):
        def on_any_event(self, event) -> None:  # noqa: ANN001 - watchdog event type
            handler.on_any_event(event)

    return _Adapter()


def rebuild(repo_path: Path, report: Callable[[str], None]) -> None:
    """One scan-and-reindex cycle.

    Vulnerability, license and git-history checks are skipped. They are the
    network-bound and history-bound parts of a scan, they take seconds to
    minutes, and none of them change because a function body was edited -
    running them on every save would make the loop unusable while adding
    nothing. A full `aletheore scan` still does all of them.

    The index is only refreshed if one already exists. Building a first
    index means embedding every chunk, which needs a provider and real time;
    starting that unasked because someone edited a file would be a surprise.
    """
    from aletheore.evidence import scan_repository, write_evidence

    evidence = scan_repository(
        repo_path,
        check_vulnerabilities=False,
        check_licenses=False,
        scan_git_history=False,
    )
    write_evidence(evidence, repo_path)
    report("evidence updated")

    if not (repo_path / ".aletheore" / "index.lancedb").exists():
        return
    from aletheore.search_index import build_index

    try:
        count = build_index(repo_path, evidence)
    except Exception as exc:  # noqa: BLE001
        # An unreachable embedding provider must not end the watch. Evidence
        # is already current, which is most of the value, and the next edit
        # retries.
        report(f"index not updated ({type(exc).__name__}: {exc})")
        return
    report(f"index updated ({count} chunks)")


def watch(
    repo_path: Path,
    report: Callable[[str], None],
    debounce_seconds: float = DEBOUNCE_SECONDS,
    stop: threading.Event | None = None,
    poll_seconds: float = 0.25,
) -> None:
    """Rebuild evidence whenever watched source files settle after changing.

    Returns when `stop` is set, so a caller (and a test) can end it without
    depending on a signal.
    """
    from watchdog.observers import Observer

    stop = stop or threading.Event()
    # Printed before building the mtime baseline (_DebouncedHandler.__init__
    # walks the tree once - see _current_mtimes) so a large repo shows
    # something immediately instead of an apparent hang.
    report(f"watching {repo_path} - Ctrl-C to stop")
    handler = _DebouncedHandler(repo_path)
    observer = Observer()
    observer.schedule(_observer_handler(handler), str(repo_path), recursive=True)
    observer.start()

    try:
        while not stop.is_set():
            batch = handler.take_settled_batch(debounce_seconds)
            if batch:
                names = sorted(path.name for path in batch)
                shown = ", ".join(names[:3]) + (f" +{len(names) - 3} more" if len(names) > 3 else "")
                report(f"{len(names)} file(s) changed ({shown})")
                try:
                    rebuild(repo_path, report)
                except Exception as exc:  # noqa: BLE001
                    # A scan that fails on one bad edit - a half-written
                    # file, a syntax error mid-keystroke - should not end a
                    # session that the next save would fix.
                    report(f"rebuild failed ({type(exc).__name__}: {exc})")
            stop.wait(poll_seconds)
    finally:
        observer.stop()
        observer.join(timeout=5)
