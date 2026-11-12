"""One worker per person per room per machine.

Two workers for the same person would both answer the same debate turns, and a broken one (for example a
stale process that survived a closed SSH session) would poison the good one's answers. The lock is an
operating-system file lock, so it is released automatically when the holding process dies for any reason.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


class WorkerAlreadyRunning(RuntimeError):
    pass


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", name).strip(".") or "x"


def try_lock(fh) -> bool:
    """Take an exclusive, non-blocking OS lock on an open file. The OS frees it when the process dies."""
    try:
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def unlock(fh) -> None:
    if sys.platform == "win32":
        import msvcrt

        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class WorkerLock:
    def __init__(self, state_dir: Path, room_id: str, participant: str) -> None:
        self.path = Path(state_dir) / "locks" / f"worker-{_safe(room_id)}-{_safe(participant)}.lock"
        self._fh = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            holder = ""
            try:
                holder = self.path.read_text().strip()
            except OSError:
                pass
            fh.close()
            where = f" (process {holder})" if holder.isdigit() else ""
            raise WorkerAlreadyRunning(
                f"a worker for this person in this room is already running{where}; stop it first, or it will answer the same requests"
            ) from None
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()).encode())
        fh.flush()
        self._fh = fh

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None
