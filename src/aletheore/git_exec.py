"""One place that resolves the git executable for commands run inside a scanned repository."""

import os
import shutil


def _from_absolute_entries() -> str | None:
    """git from the absolute PATH entries only, in PATH order. Relative entries are skipped (they resolve against the
    current directory), and so is the current directory itself, which Windows would otherwise search first."""
    if os.name == "nt":
        extensions = [e for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if e]
        names = ["git" + e.lower() for e in extensions]
    else:
        names = ["git"]
    for entry in os.environ.get("PATH", os.defpath).split(os.pathsep):
        entry = entry.strip().strip('"')
        if not entry or not os.path.isabs(entry):
            continue
        for name in names:
            candidate = os.path.join(entry, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return os.path.abspath(candidate)
    return None


def git_executable(*, absolute_path_only: bool = False) -> str:
    """Absolute path of git, for any invocation whose cwd is a repository being analyzed.

    A bare "git" is re-resolved against PATH after the child switches to that cwd, so a relative
    PATH entry would let a scanned (untrusted) repository shadow it with its own executable. The
    scanner wrappers and the adapters already resolve to an absolute path for this reason; this
    keeps the git calls on the same footing. Raises FileNotFoundError when git is not on PATH,
    the same error the bare name would have produced, and never falls back to the bare name.

    `absolute_path_only` is for a process that itself starts inside an untrusted repository (Shield's hooks run
    with the repository as their current directory): only absolute PATH entries are searched, never a relative entry
    or the current directory, so the repository cannot plant the git that runs.
    """
    found = _from_absolute_entries() if absolute_path_only else shutil.which("git")
    if not found:
        raise FileNotFoundError("git was not found on PATH")
    return os.path.abspath(found)
