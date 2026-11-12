"""Git worktrees for workers, area enforcement and merging into a per-room integration branch.

Workers never share a working copy. Merges happen in a throwaway worktree on
the integration branch `council/<room_id>/main`, so the user's checked-out
branch and working tree are never touched.
"""

from __future__ import annotations

import fnmatch
import os
import time
import re
import shutil
import subprocess  # nosec B404 - no shell: argument lists only
import tempfile
from dataclasses import dataclass
from pathlib import Path

_GIT_IDENT = ["-c", "user.name=Council", "-c", "user.email=council@localhost"]
_GIT_ENV_ALLOW = (
    "PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "COMSPEC", "PATHEXT", "TMPDIR", "TEMP", "TMP",
    "LANG", "LC_ALL", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME",
)
_empty_hooks_dir: str | None = None


def _no_hooks_dir() -> str:
    """An empty Council-owned directory used as core.hooksPath, so repo hooks (which a worker agent
    could have edited) never run during Council's own git commands."""
    global _empty_hooks_dir
    if _empty_hooks_dir is None or not os.path.isdir(_empty_hooks_dir):
        _empty_hooks_dir = tempfile.mkdtemp(prefix="council-no-hooks-")
    return _empty_hooks_dir


class GitError(RuntimeError):
    pass


def _git_path() -> str:
    path = shutil.which("git")
    if not path:
        raise GitError("git is not installed or not on PATH")
    return os.path.abspath(path)


_LOCK_RETRIES = 5
_LOCK_RETRY_DELAY_S = 0.2


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    env = {k: os.environ[k] for k in _GIT_ENV_ALLOW if k in os.environ}
    env["GIT_TERMINAL_PROMPT"] = "0"
    argv = [_git_path(), *_GIT_IDENT, "-c", f"core.hooksPath={_no_hooks_dir()}", *args]
    for attempt in range(_LOCK_RETRIES):
        result = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)  # nosec B603 - argument list, no shell, no user-controlled program
        # several rooms and workers can use one repository at once; git takes short-lived .lock files, so wait and retry
        if result.returncode != 0 and ".lock" in (result.stderr or "") and attempt < _LOCK_RETRIES - 1:
            time.sleep(_LOCK_RETRY_DELAY_S * (2 ** attempt))
            continue
        break
    if check and result.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", name)


def integration_branch(room_id: str) -> str:
    return f"council/{_safe(room_id)}/main"


def worker_branch(room_id: str, agent_id: str) -> str:
    return f"council/{_safe(room_id)}/{_safe(agent_id)}"


def _branch_exists(repo: Path, branch: str) -> bool:
    return _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0


def create_worktree(repo: Path, room_id: str, agent_id: str, dest_root: Path) -> tuple[Path, str]:
    main = integration_branch(room_id)
    if not _branch_exists(repo, main):
        _git(repo, "branch", main, "HEAD")
    branch = worker_branch(room_id, agent_id)
    dest = Path(dest_root) / _safe(room_id) / _safe(agent_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "-b", branch, str(dest), main)
    return dest, branch


def commit_all(worktree: Path, message: str) -> bool:
    """Commit everything in the worktree. Returns False when there was nothing to commit."""
    _git(worktree, "add", "-A")
    if not _git(worktree, "status", "--porcelain").stdout.strip():
        return False
    _git(worktree, "commit", "-q", "-m", message)
    return True


def changed_files(worktree: Path, base_branch: str) -> list[str]:
    out = _git(worktree, "diff", "--name-only", "--no-renames", f"{base_branch}...HEAD").stdout
    return sorted(line.replace("\\", "/") for line in out.splitlines() if line.strip())


def branch_changed_files(repo: Path, room_id: str, branch: str) -> list[str]:
    """Files a worker branch changes relative to the room's integration branch (renames reported as delete + add)."""
    out = _git(repo, "diff", "--name-only", "--no-renames", f"{integration_branch(room_id)}...{branch}").stdout
    return sorted(line.replace("\\", "/") for line in out.splitlines() if line.strip())


def files_outside_area(changed: list[str], globs: list[str]) -> list[str]:
    return [f for f in changed if not any(fnmatch.fnmatchcase(f, g) for g in globs)]


@dataclass
class MergeResult:
    ok: bool
    conflicts: list[str]
    message: str


def merge_branch(repo: Path, room_id: str, branch: str) -> MergeResult:
    main = integration_branch(room_id)
    tmp = Path(tempfile.mkdtemp(prefix="council-merge-"))
    wt = tmp / "wt"
    try:
        _git(repo, "worktree", "add", str(wt), main)
        result = _git(wt, "merge", "--no-ff", "-m", f"Council: merge {branch}", branch, check=False)
        if result.returncode == 0:
            return MergeResult(True, [], f"merged {branch} into {main}")
        conflicts = sorted(
            f.replace("\\", "/") for f in _git(wt, "diff", "--name-only", "--diff-filter=U", check=False).stdout.split()
        )
        _git(wt, "merge", "--abort", check=False)
        return MergeResult(False, conflicts, (result.stdout + result.stderr).strip())
    finally:
        _git(repo, "worktree", "remove", "--force", str(wt), check=False)
        shutil.rmtree(tmp, ignore_errors=True)
        _git(repo, "worktree", "prune", check=False)
