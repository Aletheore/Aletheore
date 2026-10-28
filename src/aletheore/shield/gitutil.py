"""Git helpers for Shield: one subprocess wrapper and a plain-words repository state check."""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from aletheore.git_exec import git_executable

_TIMED_OUT = 124


@dataclass(frozen=True)
class RepoState:
    ok: bool
    reason: str | None
    git_dir: Path | None
    has_head: bool


def run_git(
    repo: Path,
    *args: str,
    env: dict[str, str] | None = None,
    input: bytes | None = None,
    timeout: float = 10.0,
) -> "subprocess.CompletedProcess[bytes]":
    """Run git inside repo. Never raises on a non-zero exit; a timeout yields returncode 124 and empty output."""
    command = [git_executable(), *args]
    try:
        return subprocess.run(
            command, cwd=str(repo), env=env, input=input, capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, _TIMED_OUT, b"", b"")


def _unsupported(reason: str, git_dir: Path | None = None) -> RepoState:
    return RepoState(ok=False, reason=reason, git_dir=git_dir, has_head=False)


def repo_state(repo: Path) -> RepoState:
    """Say whether Shield can work in repo, and if not, why in plain words."""
    try:
        probe = run_git(repo, "rev-parse", "--absolute-git-dir", "--is-bare-repository")
    except FileNotFoundError:
        return _unsupported("git was not found on PATH")
    lines = probe.stdout.decode("utf-8", "replace").splitlines()
    if probe.returncode != 0 or len(lines) < 2:
        return _unsupported("this folder is not a git repository")
    git_dir = Path(lines[0])
    if lines[1].strip() == "true":
        return _unsupported("bare repositories have no working tree to guard", git_dir)
    for marker, what in (
        ("rebase-merge", "a rebase"),
        ("rebase-apply", "a rebase"),
        ("MERGE_HEAD", "a merge"),
    ):
        if (git_dir / marker).exists():
            return _unsupported(f"{what} is in progress; finish or abort it first", git_dir)
    if run_git(repo, "symbolic-ref", "-q", "HEAD").returncode != 0:
        return _unsupported("HEAD is detached; check out a branch first", git_dir)
    has_head = run_git(repo, "rev-parse", "--verify", "-q", "HEAD").returncode == 0
    return RepoState(ok=True, reason=None, git_dir=git_dir, has_head=has_head)
