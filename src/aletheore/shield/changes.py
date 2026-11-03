"""What a change touches, listed ONCE per guard run and shared by the dependency and evidence checks.

Three git calls, whatever the repository's size (the last two run at the same time): `rev-parse` (the base commit and the git dir), `ls-files -t
--cached --others --exclude-standard` (every tracked and every untracked, not ignored file, in one walk of the working
tree) and `diff --name-status --no-renames <base>` (what differs from the base, deletions included). Before this the
guard listed untracked files three times per run, and that walk is the slowest git call on a large tree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from aletheore.shield.gitutil import UNLIMITED, Budget, run_git, run_git_pair

LIST_TIMEOUT_S = 30.0
MIN_SPAWN_S = 0.01


@dataclass(frozen=True)
class ChangeSet:
    has_base: bool
    base_commit: str  # "" when there is no base commit
    git_dir: Path | None
    tracked: tuple[str, ...]  # paths in the index
    untracked: tuple[str, ...]  # untracked files git does not ignore
    changed: tuple[str, ...]  # differ from the base (deletions included) plus untracked; every file with no base
    deleted: frozenset[str] = field(default_factory=frozenset)  # changed paths missing from the working tree
    complete: bool = True  # False when a listing failed or ran out of time; the lists may then be partial
    spawn_failed: bool = False  # git could not be resolved or started at all

    @property
    def working(self) -> list[str]:
        """Every file in the working tree that git does not ignore (tracked, minus deleted, plus untracked)."""
        return [p for p in self.tracked if p not in self.deleted] + [p for p in self.untracked]

    @property
    def present_changed(self) -> list[str]:
        """Changed files that still exist (added or modified, tracked or not)."""
        return [p for p in self.changed if p not in self.deleted]


def _paths(out: bytes) -> list[str]:
    return [p.decode("utf-8", "replace") for p in out.split(b"\0") if p]


def compute(repo: Path, base: str = "HEAD", *, budget: Budget = UNLIMITED) -> ChangeSet:
    """List the change of the working tree against `base`. Never raises; `complete` is False when git did not
    answer (in time), and the guard then says so instead of reporting from partial lists. With less than
    MIN_SPAWN_S left no git process is started at all."""
    repo = Path(repo)
    if budget.left() < MIN_SPAWN_S:  # git could not finish anything useful in the time left: do not start it
        return ChangeSet(False, "", None, (), (), (), complete=False)
    probe = run_git(
        repo, "rev-parse", "--absolute-git-dir", "--verify", "-q", f"{base}^{{commit}}",
        timeout=budget.timeout(10.0),
    )
    lines = probe.stdout.decode("utf-8", "surrogateescape").splitlines()
    git_dir = Path(lines[0]) if lines and lines[0] else None
    has_base = probe.returncode == 0 and len(lines) >= 2 and bool(lines[1].strip())
    base_commit = lines[1].strip() if has_base else ""
    complete = git_dir is not None

    list_args = ("ls-files", "-z", "-t", "--cached", "--others", "--exclude-standard")
    diff_args = ("diff", "--name-status", "-z", "--no-renames", base_commit, "--")
    if has_base:  # independent calls: the diff runs while the working tree is walked
        listing, diff = run_git_pair(
            repo, list_args, diff_args, timeout=budget.timeout(LIST_TIMEOUT_S), runner=run_git
        )
    else:
        listing, diff = run_git(repo, *list_args, timeout=budget.timeout(LIST_TIMEOUT_S)), None
    tracked: dict[str, None] = {}
    untracked: dict[str, None] = {}
    if listing.returncode == 0:
        for entry in _paths(listing.stdout):
            tag, _, path = entry.partition(" ")
            if path:
                (untracked if tag == "?" else tracked).setdefault(path)
    else:
        complete = False
    for path in untracked:
        tracked.pop(path, None)

    deleted: set[str] = set()
    if diff is not None:
        changed: dict[str, None] = {}
        if diff.returncode == 0:
            fields = _paths(diff.stdout)
            for status, path in zip(fields[::2], fields[1::2]):
                changed.setdefault(path)
                if status.startswith("D"):
                    deleted.add(path)
        else:
            complete = False
        for path in untracked:
            changed.setdefault(path)
        changed_list = tuple(changed)
    else:
        changed_list = tuple(dict.fromkeys([*tracked, *untracked]))
    return ChangeSet(
        has_base=has_base,
        base_commit=base_commit,
        git_dir=git_dir,
        tracked=tuple(tracked),
        untracked=tuple(untracked),
        changed=changed_list,
        deleted=frozenset(deleted),
        complete=complete,
        spawn_failed=probe.spawn_failed,
    )
