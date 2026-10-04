import os
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
        owned = not hasattr(os, "getuid") or info.st_uid == os.getuid()
        if path.is_symlink() or not path.is_dir() or not owned:
            raise OSError("untrusted fallback directory")
        return path
    except OSError:
        return Path(tempfile.mkdtemp(prefix="aletheore-home-"))
