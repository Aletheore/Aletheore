import os
import tempfile
from pathlib import Path


def user_home() -> Path:
    """Path.home(), but never raising.

    Path.home() raises RuntimeError in containers running as an arbitrary UID
    with no HOME and no passwd entry; several modules computed paths from it at
    import time, so the whole CLI (even --version) died. Falls back to a
    per-user directory under the system temp dir.
    """
    try:
        return Path.home()
    except (RuntimeError, KeyError):
        uid = os.getuid() if hasattr(os, "getuid") else "user"
        return Path(tempfile.gettempdir()) / f"aletheore-home-{uid}"
