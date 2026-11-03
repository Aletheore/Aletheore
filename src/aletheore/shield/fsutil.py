"""Small file primitives Shield's hooks share: atomic replace, tidying old temporary files, and the debug note.

Locks are not here: every read-modify-write takes `locking.file_lock`, a bounded OS lock.

Everything here is stdlib only and imports nothing heavy, so the per-edit hook can use it without cost.

- `atomic_write_text` writes a temporary file in the target's folder and moves it into place with `os.replace`, so a
  reader sees the old file or the new one, never a torn one. The replaced file keeps its permission bits; a new file
  gets `mode` (0644 by default, less the umask). On Windows `os.replace` fails while another process has the target
  open; it is retried for a moment before giving up. A symbolic link at the target is replaced by the new file, never
  written through, so a link pointing outside the repository cannot redirect the write. Temporary files a killed
  writer left next to the target more than an hour ago are removed on the next write.
- `debug_note` explains a swallowed error on stderr when ALETHEORE_HOOK_DEBUG is set, and does nothing otherwise.
  Stdout carries the hook protocol's JSON and is never written here.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

STALE_TEMPORARY_S = 3600.0
_REPLACE_TRIES = 5
_RETRY_REPLACE = os.name == "nt"


def debug_note(what: str, error: BaseException | None = None) -> None:
    """One `aletheore shield: <what>[: <error type>: <error>]` line on stderr, only under ALETHEORE_HOOK_DEBUG."""
    if not os.environ.get("ALETHEORE_HOOK_DEBUG"):
        return
    try:
        detail = f": {type(error).__name__}: {error}" if error is not None else ""
        sys.stderr.write(f"aletheore shield: {what}{detail}\n")
    except Exception:  # noqa: BLE001 - a broken stderr must not break the caller either  # nosec B110
        pass


# ---- atomic replace -------------------------------------------------------------------------------------------------


def _replace(source: str, target: Path) -> None:
    for attempt in range(_REPLACE_TRIES):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            # Windows: the target is open in another process (a concurrent reader or writer). It is a matter of
            # milliseconds, so wait a little and try again; elsewhere this error is real and is raised at once.
            if not _RETRY_REPLACE or attempt == _REPLACE_TRIES - 1:
                raise
            time.sleep(0.01 * (attempt + 1))


def _existing_mode(path: Path) -> int | None:
    """Permission bits of the regular file at `path` (not of a link's target); None when there is none."""
    try:
        info = os.lstat(path)
    except OSError:
        return None
    import stat

    return stat.S_IMODE(info.st_mode) if stat.S_ISREG(info.st_mode) else None


def _umask() -> int:
    # Reading the umask means setting it; this is the usual way and is restored at once.
    current = os.umask(0)
    os.umask(current)
    return current


def atomic_write_text(path: Path, text: str, *, mode: int = 0o644, cleanup: bool = True) -> None:
    """Replace `path` with `text` (UTF-8, "\\n" line ends) in one step. Raises OSError; never leaves its temporary
    file behind. An existing file keeps its permission bits; a new one gets `mode` less the umask. `cleanup` removes
    this file's temporaries left by killed writers over an hour ago (one folder listing)."""
    path = Path(path)
    if cleanup:
        remove_older_than(path.parent, (f".{path.name}.",), (".tmp",), STALE_TEMPORARY_S)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as out:
            out.write(text)
        if os.name != "nt":
            existing = _existing_mode(path)
            os.chmod(temporary, existing if existing is not None else mode & ~_umask())
        _replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError as error:
            debug_note(f"could not remove the temporary file {temporary}", error)
        raise


# ---- tidying --------------------------------------------------------------------------------------------------------


def remove_older_than(folder: Path, prefixes: tuple[str, ...], suffixes: tuple[str, ...], max_age_s: float) -> int:
    """Delete regular files directly in `folder` named `<a prefix>...<a suffix>` and older than `max_age_s`;
    returns how many. Symbolic links and folders are left alone; errors skip the file."""
    cutoff = time.time() - max_age_s
    removed = 0
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return 0
    for entry in entries:
        if not entry.name.startswith(prefixes) or not entry.name.endswith(suffixes):
            continue
        try:
            if not entry.is_file(follow_symlinks=False) or entry.stat(follow_symlinks=False).st_mtime >= cutoff:
                continue
            os.unlink(entry.path)
            removed += 1
        except OSError as error:
            debug_note(f"could not remove the old file {entry.path}", error)
    return removed
