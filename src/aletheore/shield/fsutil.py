"""Small file primitives Shield's hooks share: atomic replace, a cheap exclusive lock, and the debug note.

Everything here is stdlib only and imports nothing heavy, so the per-edit hook can use it without cost.

- `atomic_write_text` writes a temporary file in the target's folder and moves it into place with `os.replace`, so a
  reader sees the old file or the new one, never a torn one. The replaced file keeps its permission bits; a new file
  gets `mode` (0644 by default, less the umask). On Windows `os.replace` fails while another process has the target
  open; it is retried for a moment before giving up. A symbolic link at the target is replaced by the new file, never
  written through, so a link pointing outside the repository cannot redirect the write. Temporary files a killed
  writer left next to the target more than an hour ago are removed on the next write.
- `exclusive` is a lock file created with O_CREAT | O_EXCL holding a token unique to this holder. Release removes
  the file only while it still holds that token. A holder that died leaves the file behind; one older than `stale_s`
  is broken by renaming it to a private name (only one caller can rename a given file), checking the renamed file is
  the stale one (a fresh lock renamed by mistake is linked back), and deleting it; the caller then competes for the
  lock as usual. On Windows, creating a file whose deletion is still pending raises PermissionError: that counts as
  "held by someone" and is retried within the wait.
- `debug_note` explains a swallowed error on stderr when ALETHEORE_HOOK_DEBUG is set, and does nothing otherwise.
  Stdout carries the hook protocol's JSON and is never written here.
"""

from __future__ import annotations

import os
import secrets
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

LOCK_WAIT_S = 0.25
LOCK_STALE_S = 10.0
STALE_TEMPORARY_S = 3600.0
_REPLACE_TRIES = 5
_RETRY_REPLACE = os.name == "nt"
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


# ---- exclusive lock -------------------------------------------------------------------------------------------------


def _try_create(lock: Path, token: str) -> bool:
    """Create `lock` holding `token`; False when it exists. PermissionError (Windows, delete pending) propagates."""
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as out:
        out.write(token)
    return True


def _age(path: Path) -> float:
    """Seconds since `path` was last modified; 0 when it is gone (its holder just released it)."""
    try:
        return time.time() - os.stat(path).st_mtime
    except OSError:
        return 0.0


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None


def _set_aside(lock: Path) -> Path | None:
    """Rename `lock` to a private name; None when another caller got there first (or it is gone)."""
    aside = lock.with_name(f"{lock.name}.{secrets.token_hex(6)}.broken")
    try:
        os.rename(lock, aside)
    except OSError:
        return None
    return aside


def _break_stale(lock: Path, stale_s: float) -> None:
    """Remove a lock whose holder died. Only the caller whose rename succeeds acts on the file it renamed; if that
    file turns out to be fresh (the stale one was replaced between the age check and the rename), it is put back."""
    aside = _set_aside(lock)
    if aside is None:
        return
    try:
        if time.time() - os.stat(aside).st_mtime <= stale_s:
            try:
                os.link(aside, lock)  # fails when a third caller already created a new lock: theirs stands
            except OSError as error:
                debug_note(f"could not put back the live lock {lock}", error)
    except OSError as error:
        debug_note(f"could not check the lock set aside at {aside}", error)
    try:
        os.unlink(aside)
    except OSError as error:
        debug_note(f"could not remove the broken lock {aside}", error)


def _release(lock: Path, token: str) -> None:
    """Remove `lock` only while it still holds `token`: set it aside, check, and link it back when it is not ours
    (our lock was broken as stale and another caller now holds a new one)."""
    aside = _set_aside(lock)
    if aside is None:
        debug_note(f"the lock {lock} was gone at release")
        return
    if _read(aside) != token:
        try:
            os.link(aside, lock)
        except OSError as error:
            debug_note(f"could not put back another holder's lock {lock}", error)
    try:
        os.unlink(aside)
    except OSError as error:
        debug_note(f"could not release the lock {lock}", error)


@contextmanager
def exclusive(lock: Path, *, wait_s: float = LOCK_WAIT_S, stale_s: float = LOCK_STALE_S) -> Iterator[bool]:
    """Hold `lock` (a file that must not otherwise exist) for the body; yields whether it is held.

    Waits at most `wait_s` for another holder; a lock older than `stale_s` is broken (see the module docstring) and
    then competed for. Never raises OSError for the lock itself: a folder that cannot hold the lock file yields
    False."""
    lock = Path(lock)
    token = f"{os.getpid()}-{secrets.token_hex(8)}"
    held = False
    deadline = time.monotonic() + wait_s
    try:
        while True:
            try:
                held = _try_create(lock, token)
            except PermissionError:
                held = False  # Windows: the previous lock file is still being deleted; it is contended, not broken
            except OSError as error:
                debug_note(f"could not create the lock {lock}", error)
                break
            if held:
                break
            if time.monotonic() >= deadline:
                break
            if _age(lock) > stale_s:
                _break_stale(lock, stale_s)
                continue  # compete for it at once (within the wait)
            time.sleep(_POLL_S)
        yield held
    finally:
        if held:
            _release(lock, token)


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
