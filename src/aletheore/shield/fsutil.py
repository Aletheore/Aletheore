"""Small file primitives Shield's hooks share: atomic replace, tidying old temporary files, and the debug note.

Locks are not here: every read-modify-write takes `locking.file_lock`, a bounded OS lock.

Everything here is stdlib only and imports nothing heavy, so the per-edit hook can use it without cost.

- `atomic_write_text` writes a temporary file in the target's folder and moves it into place with `os.replace`, so a
  reader sees the old file or the new one, never a torn one. On POSIX a private `mode` (no group or other bits, such as
  0600) is applied exactly, to a new file and to an existing one alike, so the umask never matters for it and a
  looser file an older version wrote is tightened. Any other `mode` (0644 by default): the replaced file keeps its
  permission bits and a new file gets `mode` less the umask read once at import (setting the umask to read it is
  not thread-safe), so a umask the host process sets after import does not apply to those. On Windows `mode` is not applied: the file
  is a `tempfile.mkstemp` file moved into place, so it has the folder's inherited ACL and the read-only bit clear,
  readable by whoever can read that folder. On Windows `os.replace` fails while another process has the target
  open; it is retried for a moment before giving up. A symbolic link at the target is replaced by the new file, never
  written through, so a link pointing outside the repository cannot redirect the write. Temporary files a killed
  writer left next to the target more than an hour ago are removed on the next write.
- `debug_note` names a swallowed error (its class only) on stderr when ALETHEORE_HOOK_DEBUG is set, and does nothing
  otherwise.
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
    """One `aletheore shield: <what>[: <error class>]` line on stderr, only under ALETHEORE_HOOK_DEBUG. `what` is a
    fixed message and only the error's class is written: an error's text can carry a path, a token or file content."""
    if not os.environ.get("ALETHEORE_HOOK_DEBUG"):
        return
    detail = f": {type(error).__name__}" if error is not None else ""
    try:
        sys.stderr.write(f"aletheore shield: {what}{detail}\n")
    except (OSError, ValueError, AttributeError):
        return  # stderr is closed, broken or missing: there is nowhere left to report anything, and the caller goes on


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


def _read_umask() -> int:
    """The process umask, read once when this module is imported. On Linux it is read from /proc without changing
    it; elsewhere the only way is to set it and set it back, which is done here, at import, and never again while
    other threads (run_git_pair's helper) may be creating files."""
    if os.name == "nt":
        return 0
    try:
        with open("/proc/self/status", encoding="ascii", errors="replace") as status:
            for line in status:
                if line.startswith("Umask:"):
                    return int(line.split()[1], 8)
    except (OSError, ValueError, IndexError):
        pass  # no /proc (macOS, BSD) or an unexpected format: fall back to reading it by setting it
    current = os.umask(0o022)
    os.umask(current)
    return current


_UMASK = _read_umask()


def atomic_write_text(path: Path, text: str, *, mode: int = 0o644, cleanup: bool = True) -> None:
    """Replace `path` with `text` (UTF-8, "\\n" line ends) in one step. Raises OSError; never leaves its temporary
    file behind. On POSIX a private `mode` is applied exactly; otherwise an existing file keeps its permission bits and
    a new one gets `mode` less the umask; on Windows `mode` is not applied (see the module docstring). `cleanup` removes
    this file's temporaries left by killed writers over an hour ago (one folder listing)."""
    path = Path(path)
    if cleanup:
        remove_older_than(path.parent, (f".{path.name}.",), (".tmp",), STALE_TEMPORARY_S)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as out:
            out.write(text)
        if os.name != "nt":
            if not mode & 0o077:
                target_mode = mode  # private: exactly this, whatever the umask, and an older looser file is tightened
            else:
                existing = _existing_mode(path)
                target_mode = existing if existing is not None else mode & ~_UMASK
            os.chmod(temporary, target_mode)  # mkstemp made it 0600 and only this process knows its name
        _replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError as error:
            debug_note("could not remove a temporary file", error)
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
            debug_note("could not remove an old file", error)
    return removed
