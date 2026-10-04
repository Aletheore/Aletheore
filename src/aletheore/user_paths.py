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
    trusted = _trusted_private_dir(path)
    if trusted is not None:
        return trusted
    return _mkdtemp_fallback_home()


def _trusted_private_dir(path: Path) -> Path | None:
    """`path`, created (mode 0o700) if needed and verified actually private -
    or None if it can't be made or trusted. A predictable name in a shared
    temp dir can be pre-created by another user or planted as a symlink, so
    this checks ownership, type, and mode rather than trusting existence
    alone. mkdir(exist_ok=True) does not change an already-existing dir's
    mode, so a dir we own but with looser permissions (e.g. left over from
    before this check existed) would otherwise pass the other checks and
    be trusted anyway."""
    try:
        path.mkdir(mode=0o700, exist_ok=True)
        info = path.lstat()
        owned = not hasattr(os, "getuid") or info.st_uid == os.getuid()
        private_mode = stat.S_IMODE(info.st_mode) == 0o700
        if path.is_symlink() or not path.is_dir() or not owned or not private_mode:
            return None
        return path
    except OSError:
        return None


@functools.lru_cache(maxsize=1)
def _mkdtemp_fallback_home() -> Path:
    """The last-resort fallback, computed at most once per process.

    Every module-level DEFAULT_*_PATH constant (credentials.py,
    licenses.py, vulnerabilities.py) calls user_home() independently at
    import time; without caching, each would get its own freshly
    mkdtemp'd directory even within a single process, scattering
    credentials and caches that are supposed to share one home.
    """
    try:
        return Path(tempfile.mkdtemp(prefix="aletheore-home-"))
    except OSError:
        pass
    # mkdtemp itself failed (TMPDIR full or unwritable). The bare temp dir
    # is shared and world-writable; handing it straight to callers would
    # mean credentials.py and the license/vulnerability caches write
    # sensitive files somewhere every other user on the machine can read.
    # One more attempt at a private, deterministically-named directory -
    # narrower exposure than the fully public dir, even though (unlike
    # mkdtemp) its name can be predicted ahead of time - before finally
    # giving up and returning the public dir as the last resort.
    uid = os.getuid() if hasattr(os, "getuid") else "user"
    emergency = _trusted_private_dir(Path(tempfile.gettempdir()) / f"aletheore-home-emergency-{uid}")
    return emergency if emergency is not None else Path(tempfile.gettempdir())
