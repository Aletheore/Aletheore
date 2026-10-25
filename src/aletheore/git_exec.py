"""One place that resolves the git executable for commands run inside a scanned repository."""

import os
import shutil


def git_executable() -> str:
    """Absolute path of git, for any invocation whose cwd is a repository being analyzed.

    A bare "git" is re-resolved against PATH after the child switches to that cwd, so a relative
    PATH entry would let a scanned (untrusted) repository shadow it with its own executable. The
    scanner wrappers and the adapters already resolve to an absolute path for this reason; this
    keeps the git calls on the same footing. Raises FileNotFoundError when git is not on PATH,
    the same error the bare name would have produced, and never falls back to the bare name.
    """
    found = shutil.which("git")
    if not found:
        raise FileNotFoundError("git was not found on PATH")
    return os.path.abspath(found)
