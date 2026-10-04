import functools
import os
import stat
import tempfile
from pathlib import Path


def user_home() -> Path:
    """Path.home(), but never raising.

    Path.home() raises RuntimeError in containers running as an arbitrary UID
    with no HOME and no passwd entry; several modules computed paths from it at
    import time, so the whole CLI (even --version) died. Falls back to a
    private per-user directory under the system temp dir.
    """
    try:
        return Path.home()
    except (RuntimeError, KeyError):
        return _private_fallback_home()


def _private_fallback_home() -> Path:
    uid = os.getuid() if hasattr(os, "getuid") else "user"
    path = Path(tempfile.gettempdir()) / f"aletheore-home-{uid}"
    try:
        path.mkdir(mode=0o700, exist_ok=True)
        info = path.lstat()
        # A predictable name in a shared temp dir can be pre-created by another
        # user or planted as a symlink; only trust a real directory we own.
        # mkdir(exist_ok=True) does not change an already-existing dir's
        # mode, so a pre-created dir we own but with looser permissions
        # (e.g. left over from before this permission check existed) would
        # otherwise pass every other check here and be trusted as private.
        owned = not hasattr(os, "getuid") or info.st_uid == os.getuid()
        private_mode = stat.S_IMODE(info.st_mode) == 0o700
        if path.is_symlink() or not path.is_dir() or not owned or not private_mode:
            raise OSError("untrusted fallback directory")
        return path
    except OSError:
        return _mkdtemp_fallback_home()


@functools.lru_cache(maxsize=1)
def _mkdtemp_fallback_home() -> Path:
    """The last-resort fallback, computed at most once per process.

    Every module-level DEFAULT_*_PATH constant (credentials.py,
    licenses.py, vulnerabilities.py) calls user_home() independently at
    import time; without caching, each would get its own freshly
    mkdtemp'd directory even within a single process, scattering
    credentials and caches that are supposed to share one home. Falls
    back to the bare (shared, non-private) temp dir rather than raising
    if mkdtemp itself fails (e.g. TMPDIR is full or unwritable) - this
    function exists specifically so a filesystem problem degrades
    gracefully instead of crashing the whole CLI at import time.
    """
    try:
        return Path(tempfile.mkdtemp(prefix="aletheore-home-"))
    except OSError:
        return Path(tempfile.gettempdir())
