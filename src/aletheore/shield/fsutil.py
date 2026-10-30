"""Small file primitives Shield's hooks share: atomic replace, a cheap exclusive lock, and the debug note.

Everything here is stdlib only and imports nothing heavy, so the per-edit hook can use it without cost.

- `atomic_write_text` writes a private temporary file in the target's folder and moves it into place with
  `os.replace`, so a reader sees the old file or the new one, never a torn one. On Windows `os.replace` fails while
  another process has the target open; it is retried for a moment before giving up.
- `exclusive` is a lock file created with O_CREAT | O_EXCL: whoever creates it holds the lock until it is removed.
  A holder that died leaves the file behind; one older than `stale_s` is removed by the next caller, which then does
  NOT take the lock itself (it returns not-held), so two callers breaking the same stale lock can never both hold it.
- `debug_note` explains a swallowed error on stderr when ALETHEORE_HOOK_DEBUG is set, and does nothing otherwise.
  Stdout carries the hook protocol's JSON and is never written here.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

LOCK_WAIT_S = 0.25
LOCK_STALE_S = 10.0
_REPLACE_TRIES = 5
_POLL_S = 0.005


def debug_note(what: str, error: BaseException | None = None) -> None:
    """One `aletheore shield: <what>[: <error type>: <error>]` line on stderr, only under ALETHEORE_HOOK_DEBUG."""
    if not os.environ.get("ALETHEORE_HOOK_DEBUG"):
        return
    try:
        detail = f": {type(error).__name__}: {error}" if error is not None else ""
        sys.stderr.write(f"aletheore shield: {what}{detail}\n")
    except Exception:  # noqa: BLE001 - a broken stderr must not break the caller either  # nosec B110
        pass


def _replace(source: str, target: Path) -> None:
    for attempt in range(_REPLACE_TRIES):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            # Windows: the target is open in another process (a concurrent reader or writer). It is a matter of
            # milliseconds, so wait a little and try again; elsewhere this error is real and is raised at once.
            if os.name != "nt" or attempt == _REPLACE_TRIES - 1:
                raise
            time.sleep(0.01 * (attempt + 1))


def atomic_write_text(path: Path, text: str) -> None:
    """Replace `path` with `text` (UTF-8, "\\n" line ends) in one step. Raises OSError; never leaves its temporary
    file behind."""
    path = Path(path)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as out:
            out.write(text)
        _replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError as error:
            debug_note(f"could not remove the temporary file {temporary}", error)
        raise


def _try_create(lock: Path) -> bool:
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    os.close(descriptor)
    return True


@contextmanager
def exclusive(lock: Path, *, wait_s: float = LOCK_WAIT_S, stale_s: float = LOCK_STALE_S) -> Iterator[bool]:
    """Hold `lock` (a file that must not otherwise exist) for the body; yields whether it is held.

    Waits at most `wait_s` for another holder. A lock file older than `stale_s` was left by a holder that died: it is
    removed, and this call yields False (the next caller takes it). Never raises OSError for the lock itself: a
    folder that cannot hold the lock file yields False."""
    held = False
    deadline = time.monotonic() + wait_s
    try:
        while True:
            try:
                held = _try_create(lock)
            except OSError as error:
                debug_note(f"could not create the lock {lock}", error)
                break
            if held:
                break
            try:
                age = time.time() - lock.stat().st_mtime
            except OSError:
                age = 0.0  # gone already: the holder just released it, try again
            if age > stale_s:
                try:
                    lock.unlink()
                except OSError as error:
                    debug_note(f"could not remove the stale lock {lock}", error)
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(_POLL_S)
        yield held
    finally:
        if held:
            try:
                lock.unlink()
            except OSError as error:
                debug_note(f"could not release the lock {lock}", error)


def remove_older_than(folder: Path, prefix: str, suffixes: tuple[str, ...], max_age_s: float) -> int:
    """Delete regular files directly in `folder` named `<prefix>...<one of suffixes>` and older than `max_age_s`;
    returns how many. Symbolic links and folders are left alone; errors skip the file."""
    cutoff = time.time() - max_age_s
    removed = 0
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return 0
    for entry in entries:
        if not entry.name.startswith(prefix) or not entry.name.endswith(suffixes):
            continue
        try:
            if not entry.is_file(follow_symlinks=False) or entry.stat(follow_symlinks=False).st_mtime >= cutoff:
                continue
            os.unlink(entry.path)
            removed += 1
        except OSError as error:
            debug_note(f"could not remove the old file {entry.path}", error)
    return removed
