"""Small cross-process locks for Shield's read/modify/write state."""

import errno
import os
import time
from contextlib import contextmanager
from pathlib import Path

_RETRY_OPEN = os.name == "nt"


@contextmanager
def file_lock(path: Path, timeout: float = 0.25):
    """Yield whether a bounded OS lock was acquired; process exit releases it automatically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    end = time.monotonic() + timeout
    while True:
        try:
            descriptor = os.open(path, flags, 0o600)
            break
        except PermissionError:
            # Windows: several openers creating the same lock file at once can get a sharing violation for a few
            # milliseconds. That is contention, not a real refusal, so keep trying within the time budget (the same
            # budget the lock itself waits); elsewhere, or once the budget is spent, the error is real and is raised.
            if not _RETRY_OPEN or time.monotonic() >= end:
                raise
            time.sleep(0.005)
    with os.fdopen(descriptor, "r+b") as handle:
        if os.name == "nt":
            import msvcrt

            # The lock byte goes in through the raw descriptor, not the buffered handle: a refused buffered write stays
            # in the buffer, so every later seek or flush on the handle would raise the same error again.
            descriptor = handle.fileno()
            while True:
                try:
                    if os.fstat(descriptor).st_size == 0:
                        os.write(descriptor, b"\0")
                        os.lseek(descriptor, 0, os.SEEK_SET)
                    break
                except PermissionError:
                    # Windows refuses a write into a byte another opener already locked. That is benign once the byte
                    # exists. While the file is still empty it is either openers creating the lock file together (gone
                    # in milliseconds, so keep trying within the time budget) or a real refusal (read-only file,
                    # restrictive ACL), which still raises once the budget is spent.
                    if os.fstat(descriptor).st_size > 0:
                        break
                    if time.monotonic() >= end:
                        raise
                    time.sleep(0.005)

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
