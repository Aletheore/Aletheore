"""Small cross-process locks for Shield's read/modify/write state."""

import errno
import os
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(path: Path, timeout: float = 0.25):
    """Yield whether a bounded OS lock was acquired; process exit releases it automatically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags, 0o600), "r+b") as handle:
        if os.name == "nt":
            import msvcrt

            try:
                if handle.seek(0, 2) == 0:
                    handle.write(b"\0")
                    handle.flush()
            except PermissionError:
                # Windows refuses a write into a byte another opener already locked. That is only benign when the byte
                # exists; if the file is still empty the error is a real one (read-only file, restrictive ACL), so raise.
                if handle.seek(0, 2) == 0:
                    raise

            def lock():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

            def unlock():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def lock():
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

            def unlock():
                fcntl.flock(handle, fcntl.LOCK_UN)

        end = time.monotonic() + timeout
        acquired = False
        while True:
            try:
                lock()
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if time.monotonic() >= end:
                    break
                time.sleep(0.005)
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    unlock()
                except OSError as error:
                    # The enclosing `with` closes the handle whatever happens here, and closing it releases the OS
                    # lock (flock and msvcrt locks belong to the open file); the body's own outcome is kept.
                    from aletheore.shield.fsutil import debug_note

                    debug_note("could not unlock; closing the lock file releases it", error)
