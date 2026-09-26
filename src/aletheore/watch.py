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

# Held for the whole of a rebuild, and by the MCP server's aletheore_scan and
# aletheore_index tools for the whole of theirs, so a background rebuild and an
# agent-triggered scan never write .aletheore/ at the same time. Re-entrant so
# a holder that calls back into another holder cannot deadlock itself.
EVIDENCE_WRITE_LOCK = threading.RLock()

# The MCP server's background watcher is slower to react than the foreground
# command on purpose. A rebuild costs seconds of CPU (about 9.5 s measured on a
# 424-source-file repository), and an agent editing files all afternoon would
# otherwise keep one running almost constantly. Edits that land while a rebuild
# is running are not lost: they accumulate in the handler and become the next one.
MCP_DEBOUNCE_SECONDS = 5.0

# Above this many parseable source files the background watcher declines to start
# rather than quietly burn a core on every edit burst. Measured in the same units
# the watcher itself tracks (files the scanner can parse), so the number in the
# message means what it says. A foreground `aletheore watch` has no such limit:
# whoever typed it asked for it.
MCP_MAX_WATCHED_FILES = 5000

WATCH_ENV_VAR = "ALETHEORE_MCP_WATCH"
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def watching_disabled_by_env(environ: dict[str, str] | None = None) -> bool:
    """Whether ALETHEORE_MCP_WATCH turns the background watcher off.

    Only an explicit falsy value disables it (0, false, no, off, any case).
    Unset, empty, or anything else leaves the default in force, so a typo
    cannot silently switch behaviour in either direction.
    """
    value = (environ if environ is not None else os.environ).get(WATCH_ENV_VAR)
    return value is not None and value.strip().lower() in _FALSE_VALUES

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


def _current_mtimes(repo_path: Path, limit: int | None = None) -> dict[Path, float] | None:
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

    limit: give up and return None as soon as more than this many relevant
    files have been seen, instead of finishing the walk. The background
    watcher uses it to refuse a huge repository without first paying to
    enumerate all of it.
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
            if limit is not None and len(mtimes) > limit:
                return None
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

    def __init__(self, repo_path: Path, known_mtimes: dict[Path, float] | None = None) -> None:
        self.repo_path = repo_path
        self._lock = threading.Lock()
        self._last_event_at: float | None = None
        self._changed: set[Path] = set()
        # Built once, synchronously, before the observer starts - see
        # _current_mtimes for why this is a one-time cost rather than the
        # per-cycle poll this module exists to avoid. A caller that already
        # walked the tree (start_background_watch, to size-check it) hands
        # its result in rather than paying for the walk twice.
        self._known_mtimes: dict[Path, float] = (
            known_mtimes if known_mtimes is not None else _current_mtimes(repo_path)
        )

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


def _carry_forward_skipped_analysis(evidence: dict, repo_path: Path) -> None:
    """Replace scan_repository's skipped-analysis placeholders (clusters,
    cross_cluster_edges, layer_violations, git.hotspots, and - as of this
    fix - dependency_vulnerabilities/dependency_licenses/git-history secret
    findings) with the last real full scan's values, in place.

    Flash review on #517 caught the architecture/hotspots half of this gap
    (this function was named _carry_forward_architecture for exactly that
    scope). The backward PR-gap audit that followed found the other half
    the same day: vulnerabilities and licenses are ALSO skipped by every
    `rebuild()` call (see below) but were never carried forward, so a
    dashboard or MCP server reading evidence mid-watch-session saw "0
    vulnerabilities" / "no license data" for the whole session even when
    the last real scan found real findings - verified directly: a repo
    with 32 real vulnerability findings from a full scan showed all 32
    silently vanish (checked: False, findings: []) after one incremental
    rebuild, before this fix. Same failure shape as the architecture gap,
    same fix shape: only ever improves on the placeholder - no prior
    evidence, or a read failure, leaves the placeholder standing exactly
    as before this function existed.
    """
    from aletheore.evidence import load_evidence

    try:
        previous = load_evidence(repo_path)
    except Exception:  # noqa: BLE001
        return

    previous_architecture = previous.get("architecture")
    if previous_architecture:
        evidence.setdefault("architecture", {}).update(
            clusters=previous_architecture.get("clusters", []),
            cross_cluster_edges=previous_architecture.get("cross_cluster_edges", []),
            layer_violations=previous_architecture.get("layer_violations")
            or evidence.get("architecture", {}).get("layer_violations"),
        )

    previous_hotspots = previous.get("git", {}).get("hotspots")
    if previous_hotspots is not None:
        evidence.setdefault("git", {})["hotspots"] = previous_hotspots

    previous_security = previous.get("security") or {}
    previous_vulnerabilities = previous_security.get("dependency_vulnerabilities")
    if previous_vulnerabilities and previous_vulnerabilities.get("checked"):
        evidence.setdefault("security", {})["dependency_vulnerabilities"] = previous_vulnerabilities
    previous_licenses = previous_security.get("dependency_licenses")
    if previous_licenses and previous_licenses.get("checked"):
        evidence.setdefault("security", {})["dependency_licenses"] = previous_licenses
    previous_static_analysis = previous_security.get("static_analysis")
    if previous_static_analysis and previous_static_analysis.get("checked"):
        evidence.setdefault("security", {})["static_analysis"] = previous_static_analysis

    # scan_git_history=False resets history_findings/history_scanned_commits
    # to empty on every rebuild (evidence.py: history_data defaults to
    # {"history_scanned_commits": 0, "history_findings": []} when the check
    # is skipped) - same failure shape as vulnerabilities/licenses above,
    # just nested under security.secrets instead of its own top-level key.
    # Only the history half is carried forward, not the whole secrets dict:
    # this rebuild's own secrets_data["findings"] (the working-tree scan)
    # is real and fresh every time - scan_git_history never gates it - so
    # overwriting the whole dict would discard THIS run's real results to
    # reuse an OLDER run's, backwards from what carry-forward is for.
    #
    # Flash Review on this PR caught a real gap: gating on non-empty
    # history_findings treats "scanned and found nothing" the same as
    # "never scanned" - a real prior scan that found zero secrets in
    # history (a normal, common outcome) would leave the placeholder's
    # history_scanned_commits: 0 standing, falsely implying history was
    # never scanned. Gate on history_scanned_commits > 0 instead - that's
    # only ever positive after a real scan actually walked commits.
    previous_secrets = previous_security.get("secrets", {})
    previous_history_scanned_commits = previous_secrets.get("history_scanned_commits", 0)
    if previous_history_scanned_commits > 0:
        current_secrets = evidence.setdefault("security", {}).setdefault("secrets", {})
        current_secrets["history_findings"] = previous_secrets.get("history_findings", [])
        current_secrets["history_scanned_commits"] = previous_history_scanned_commits


def rebuild(repo_path: Path, report: Callable[[str], None]) -> None:
    """One scan-and-reindex cycle.

    Vulnerability, license, git-history, architecture-analysis, hotspot, and
    static-analysis checks are skipped. Vulnerability/license/history are
    network-bound and history-bound; architecture analysis (clustering +
    layer violations) and hotspots are driven by the import graph and
    commit history respectively, neither of which moves when a function
    body is edited. Static analysis is the one exception to "doesn't move
    on a body edit" - Semgrep/Bearer/gosec/Bandit/Trivy/PMD very much care about
    changed code - but each is a real subprocess spawn (seconds at best,
    SonarQube minutes), and running five-plus external tools on every
    keystroke-triggered save would make the loop unusable regardless of
    relevance. All take seconds to minutes - clustering alone measured at
    1.9s on a 42-module repo, the dominant cost of an incremental rebuild by
    far (confirmed by direct profiling) - and running any of them on every
    save would make the loop unusable while adding nothing. A full
    `aletheore scan` still does all of them. The skipped fields
    (architecture, hotspots, vulnerabilities, licenses, git-history secrets,
    static analysis) aren't left blank in the meantime -
    _carry_forward_skipped_analysis reuses the last real full scan's
    values for each, so a dashboard or MCP server reading evidence written
    mid-session sees the last known-good state rather than "nothing"
    until the next full scan recomputes it for real.

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
        analyze_architecture=False,
        check_hotspots=False,
        check_static_analysis=False,
    )
    _carry_forward_skipped_analysis(evidence, repo_path)
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
    announce: str | None = None,
    initial_mtimes: dict[Path, float] | None = None,
) -> None:
    """Rebuild evidence whenever watched source files settle after changing.

    Returns when `stop` is set, so a caller (and a test) can end it without
    depending on a signal.

    announce replaces the opening line, whose "Ctrl-C to stop" is only true
    for the foreground command. initial_mtimes is a baseline the caller already
    walked (see _DebouncedHandler).
    """
    from watchdog.observers import Observer

    stop = stop or threading.Event()
    # Printed before building the mtime baseline (_DebouncedHandler.__init__
    # walks the tree once - see _current_mtimes) so a large repo shows
    # something immediately instead of an apparent hang.
    report(announce if announce is not None else f"watching {repo_path} - Ctrl-C to stop")
    handler = _DebouncedHandler(repo_path, known_mtimes=initial_mtimes)
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
                    with EVIDENCE_WRITE_LOCK:
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


def _try_lock_repo(repo_path: Path):
    """Take the advisory "one watcher per repository" lock, or return None.

    Several agent sessions can have an MCP server open on the same repository
    at once, and each would otherwise run its own watcher and rebuild the same
    evidence in parallel. The lock is an OS-level advisory lock on
    .aletheore/watch.lock, held by an open file for the life of the process,
    so it is released by the kernel when the holder exits or crashes: there is
    no pid file to go stale and nothing to clean up.
    """
    lock_path = repo_path / ".aletheore" / "watch.lock"
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a+")  # noqa: SIM115 - held open on purpose
    except OSError:
        return None
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


class BackgroundWatcher:
    """A `watch()` loop on a daemon thread, with a way to stop it.

    Everything that costs time (walking the tree to size it, building the
    mtime baseline) happens on that thread, never in the caller: the MCP
    server starts one before it is ready to answer its client, and a huge
    repository must not turn that into a startup timeout.
    """

    def __init__(
        self,
        repo_path: Path,
        report: Callable[[str], None],
        debounce_seconds: float,
        max_files: int,
        lock_handle,
    ) -> None:
        self.repo_path = repo_path
        self._report = report
        self._debounce_seconds = debounce_seconds
        self._max_files = max_files
        self._stop = threading.Event()
        self._lock_handle = lock_handle
        self._thread: threading.Thread | None = None
        # True once the repository proved too large to watch. A caller that
        # restarts watchers after each scan checks it so it does not re-walk
        # and re-announce the same refusal every time.
        self.declined = False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        try:
            mtimes = _current_mtimes(self.repo_path, limit=self._max_files)
            if mtimes is None:
                self.declined = True
                self._report(
                    f"file watching not started: more than {self._max_files} source files is over the limit "
                    "for automatic re-scans (run `aletheore watch` yourself, or re-scan when you need fresh evidence)"
                )
                return
            watch(
                self.repo_path,
                self._report,
                debounce_seconds=self._debounce_seconds,
                stop=self._stop,
                announce=(
                    f"watching {self.repo_path} ({len(mtimes)} source files): evidence re-scans "
                    f"{self._debounce_seconds:g}s after edits settle. Turn off with --no-watch or {WATCH_ENV_VAR}=0"
                ),
                initial_mtimes=mtimes,
            )
        except Exception as exc:  # noqa: BLE001
            # The likely causes are environmental (an exhausted inotify watch
            # limit, watchdog unavailable). Losing live updates must never take
            # the MCP server down with it, so say so and end the thread.
            self._report(f"file watching stopped ({type(exc).__name__}: {exc}); evidence will no longer refresh on edits")
        finally:
            self._release()

    def _release(self) -> None:
        handle, self._lock_handle = self._lock_handle, None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass

    def stop(self, timeout: float = 6.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._release()


def start_background_watch(
    repo_path: Path,
    report: Callable[[str], None],
    *,
    debounce_seconds: float = MCP_DEBOUNCE_SECONDS,
    max_files: int = MCP_MAX_WATCHED_FILES,
) -> BackgroundWatcher | None:
    """Start keeping this repository's evidence current in the background.

    Returns immediately with a watcher whose thread does the rest, or None
    when it cannot start at all (no evidence yet, or another process already
    watching); each of those is reported rather than silent, because the caller
    told the user watching was on by default and a quiet no-op would make that
    false. A repository over `max_files` is only discovered on the thread, which
    reports it and sets `declined`.
    """
    evidence_path = repo_path / ".aletheore" / "air.json"
    if not evidence_path.exists():
        # Nothing to keep current yet. rebuild() carries forward the last full
        # scan's slow-check results, so it needs one to exist.
        report("file watching not started: no evidence yet (run a scan first)")
        return None

    lock_handle = _try_lock_repo(repo_path)
    if lock_handle is None:
        report("file watching not started here: another aletheore process is already watching this repository")
        return None

    watcher = BackgroundWatcher(repo_path, report, debounce_seconds, max_files, lock_handle)
    watcher._thread = threading.Thread(target=watcher._run, name="aletheore-mcp-watch", daemon=True)
    watcher._thread.start()
    return watcher
