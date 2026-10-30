"""Git helpers for Shield: one subprocess wrapper and a plain-words repository state check."""

import os
import subprocess  # nosec B404 - runs git only, with fixed argument lists and never a shell
from dataclasses import dataclass
from pathlib import Path

from aletheore.git_exec import git_executable

# Exit codes of the results run_git makes up itself (git never ran to the end). They only keep `returncode != 0`
# checks right; whether a call timed out or could not start is said by GitResult's flags, since a real git (or a
# git alias, or a wrapper script) can exit with these codes too.
_TIMED_OUT = 124
_SPAWN_FAILED = 127
_INHERITED_GIT_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")


@dataclass(frozen=True)
class RepoState:
    ok: bool
    reason: str | None
    git_dir: Path | None
    has_head: bool
    # Filled when ok, from the same probe, so a checkpoint needs no second one: the top of the working tree, the
    # common git dir (differs from git_dir in a linked worktree), the index file path and the HEAD commit ("" when
    # the branch has no commit yet).
    top: Path | None = None
    common_dir: Path | None = None
    index: Path | None = None
    head: str = ""


class GitResult(subprocess.CompletedProcess):
    """A finished git call. `timed_out`: stopped at its timeout; `spawn_failed`: git could not be started (not
    found, not executable, no such folder). Either way the output is empty and returncode is non-zero."""

    def __init__(self, args, returncode, stdout=b"", stderr=b"", *, timed_out=False, spawn_failed=False):
        super().__init__(args, returncode, stdout, stderr)
        self.timed_out = timed_out
        self.spawn_failed = spawn_failed


def run_git(
    repo: Path,
    *args: str,
    env: dict[str, str] | None = None,
    input: bytes | None = None,
    timeout: float = 10.0,
) -> GitResult:
    """Run git inside repo. Never raises: a non-zero exit is in returncode, a timeout or a git that cannot start
    is said by the result's `timed_out` / `spawn_failed` (with empty output)."""
    try:
        command = [git_executable(), *args]
    except FileNotFoundError:  # not on PATH
        return GitResult(["git", *args], _SPAWN_FAILED, spawn_failed=True)
    full_env = {k: v for k, v in os.environ.items() if k not in _INHERITED_GIT_VARS}
    full_env.update(env or {})
    try:
        done = subprocess.run(  # noqa: S603  # nosec B603 - absolute git from git_executable, argv list, no shell
            command, cwd=str(repo), env=full_env, input=input, capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return GitResult(command, _TIMED_OUT, timed_out=True)
    except OSError:
        return GitResult(command, _SPAWN_FAILED, spawn_failed=True)
    return GitResult(done.args, done.returncode, done.stdout, done.stderr)


class Budget:
    """A time budget that ends at `deadline` on `clock` (time.monotonic by default); no deadline means unlimited.

    Every stage of the guard asks it before starting (`spent`), and every git call it makes gets `timeout(default)`:
    the smaller of its usual timeout and the time left, so no call can run past the deadline by much."""

    def __init__(self, deadline: float | None = None, clock=None) -> None:
        import time

        self.deadline = deadline
        self.clock = clock or time.monotonic

    @classmethod
    def lasting(cls, seconds: float | None, clock=None) -> "Budget":
        budget = cls(None, clock)
        if seconds is not None:
            budget.deadline = budget.clock() + seconds
        return budget

    def left(self) -> float:
        return float("inf") if self.deadline is None else self.deadline - self.clock()

    def spent(self, minimum: float = 0.05) -> bool:
        """True when less than `minimum` seconds are left: a stage is not worth starting."""
        return self.left() < minimum

    def timeout(self, default: float) -> float:
        """A git timeout ending by the deadline, never below 50 ms."""
        return max(0.05, min(default, self.left()))


UNLIMITED = Budget()


def cat_file_batch(
    repo: Path, objects: list[str], *, timeout: float = 10.0
) -> dict[str, bytes] | None:
    """Contents of many objects (`<rev>:<path>` or object ids) from ONE `git cat-file --batch`.

    Returns {object name: bytes} for every object that exists; a missing one is simply absent. None when git failed
    or timed out, so the caller can tell "not there" from "not answered". Names holding a newline cannot be asked
    for on the batch protocol and are left out."""
    wanted = [name for name in dict.fromkeys(objects) if "\n" not in name and "\r" not in name]
    if not wanted:
        return {}
    payload = b"".join(name.encode("utf-8", "surrogateescape") + b"\n" for name in wanted)
    result = run_git(repo, "cat-file", "--batch", input=payload, timeout=timeout)
    if result.returncode != 0:
        return None
    data = result.stdout
    found: dict[str, bytes] = {}
    position = 0
    for name in wanted:  # answers come back in the order asked
        end = data.find(b"\n", position)
        if end < 0:
            return None
        header = data[position:end].split(b" ")
        position = end + 1
        if len(header) != 3 or not header[2].isdigit():
            continue  # "<name> missing" or "<name> ambiguous"
        size = int(header[2])
        if position + size > len(data):
            return None
        found[name] = data[position : position + size]
        position += size + 1  # content is followed by a newline
    return found


def run_git_pair(
    repo: Path, first: tuple[str, ...], second: tuple[str, ...], *, env: dict[str, str] | None = None,
    timeout: float = 10.0, runner=None,
) -> "tuple[subprocess.CompletedProcess[bytes], subprocess.CompletedProcess[bytes]]":
    """Two independent read-only git calls at once (the second on a helper thread); results in argument order.
    `runner` is the run_git to use (a caller's module-level name, so a test's patch of it applies)."""
    import threading

    run = runner or run_git
    box: list = []

    def second_call() -> None:
        try:
            box.append(run(repo, *second, env=env, timeout=timeout))
        except Exception as error:  # noqa: BLE001 - reported as a failed call below
            from aletheore.shield.fsutil import debug_note

            debug_note("a git call on the helper thread failed", error)

    helper = threading.Thread(target=second_call, daemon=True)
    helper.start()
    one = run(repo, *first, env=env, timeout=timeout)
    helper.join(timeout + 5.0)
    two = box[0] if box else GitResult(["git", *second], _TIMED_OUT, timed_out=True)
    return one, two


GIT_NOT_FOUND = "git was not found or could not be started"


def _unsupported(reason: str, git_dir: Path | None = None) -> RepoState:
    return RepoState(ok=False, reason=reason, git_dir=git_dir, has_head=False)


def repo_state(repo: Path) -> RepoState:
    """Say whether Shield can work in repo, and if not, why in plain words. Three git calls."""
    probe = run_git(
        repo, "rev-parse", "--absolute-git-dir", "--is-bare-repository", "--show-toplevel", "--git-common-dir",
        "--git-path", "index",
    )
    if getattr(probe, "spawn_failed", False):
        return _unsupported(GIT_NOT_FOUND)
    if getattr(probe, "timed_out", False):
        return _unsupported("git did not answer in time")
    lines = probe.stdout.decode("utf-8", "surrogateescape").splitlines()
    # rev-parse prints each answer in turn and stops at the first it cannot give: a bare repository has no top.
    if len(lines) >= 2 and lines[1].strip() == "true":
        return _unsupported("bare repositories have no working tree to guard", Path(lines[0]))
    if len(lines) < 2 or not lines[0]:
        return _unsupported("this folder is not a git repository")
    git_dir = Path(lines[0])
    top = common = index = None
    # Inside the git dir itself there is no top (rev-parse stops after the first two answers); that was always
    # accepted here, so it still is, just without the layout: take_checkpoint then probes on its own (and declines).
    if probe.returncode == 0 and len(lines) == 5:
        top, common, index = Path(lines[2]), Path(lines[3]), Path(lines[4])
        if not common.is_absolute():
            common = Path(repo) / common
        if not index.is_absolute():
            index = Path(repo) / index
    for marker, what in (
        ("rebase-merge", "a rebase"),
        ("rebase-apply", "a rebase"),
        ("MERGE_HEAD", "a merge"),
    ):
        if (git_dir / marker).exists():
            return _unsupported(f"{what} is in progress; finish or abort it first", git_dir)
    if run_git(repo, "symbolic-ref", "-q", "HEAD").returncode != 0:
        return _unsupported("HEAD is detached; check out a branch first", git_dir)
    head = run_git(repo, "rev-parse", "--verify", "-q", "HEAD^{commit}")
    head_sha = head.stdout.decode("ascii", "replace").strip() if head.returncode == 0 else ""
    return RepoState(
        ok=True, reason=None, git_dir=git_dir, has_head=bool(head_sha), top=top, common_dir=common, index=index,
        head=head_sha,
    )
