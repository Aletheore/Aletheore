"""Checkpoints: snapshots of the working tree kept as commits under refs/aletheore/checkpoints/.

A checkpoint is built with plumbing only (update-index, write-tree, commit-tree, update-ref) against a
temporary index file outside the repository, so the user's index, branches, stash, HEAD, reflogs and
working tree are never touched. It holds tracked and untracked files, minus anything git ignores,
deny-listed secret files, Shield's own .aletheore/ folder, and files over the size caps.

Each checkpoint commit's parent is the HEAD commit at capture time (none in a repository without
commits), never the previous checkpoint, so pruning a ref really lets git collect its objects.
"""

import json
import os
import secrets
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aletheore.shield.denylist import is_denied_path
from aletheore.shield.gitutil import repo_state, run_git

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024

REF_PREFIX = "refs/aletheore/checkpoints/"
_SUBJECT = "aletheore shield checkpoint"
_FORMAT_VERSION = 1
_GITLINK_MODE = "160000"
_PRIVATE_DIR = ".aletheore/"
_WRITE_TIMEOUT_S = 60.0
_IDENT_ENV = {
    "GIT_AUTHOR_NAME": "Aletheore Shield",
    "GIT_AUTHOR_EMAIL": "shield@aletheore.invalid",
    "GIT_COMMITTER_NAME": "Aletheore Shield",
    "GIT_COMMITTER_EMAIL": "shield@aletheore.invalid",
}
# Config that must not leak from the user's setup into our temporary index or our private refs:
# safecrlf would abort hashing, splitIndex would drop sharedindex files into .git, reflogs and
# reference-transaction hooks are for the user's refs, not ours.
_INDEX_CONFIG = (
    "-c",
    "core.safecrlf=false",
    "-c",
    "core.splitIndex=false",
    "-c",
    "core.untrackedCache=false",
)
_REF_CONFIG = ("-c", "core.logAllRefUpdates=false")


class CheckpointError(RuntimeError):
    """Git could not produce the snapshot a caller asked for."""


@dataclass(frozen=True)
class Checkpoint:
    ref: str
    commit: str
    tree: str
    created_at: datetime
    session: str
    files: int
    skipped: tuple[str, ...]


@dataclass(frozen=True)
class _Layout:
    top: Path
    worktree: str


@dataclass(frozen=True)
class _Snapshot:
    tree: str
    files: int
    skipped: tuple[str, ...]


@dataclass(frozen=True)
class _Stored:
    checkpoint: Checkpoint
    worktree: str


# ---------------------------------------------------------------------------- small helpers


def _utc(moment: datetime | None) -> datetime:
    if moment is None:
        return datetime.now(timezone.utc)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _decode_path(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogateescape")


def _encode_path(path: str) -> bytes:
    return path.encode("utf-8", "surrogateescape")


def _split_z(output: bytes) -> list[bytes]:
    return [item for item in output.split(b"\0") if item]


def _layout(repo: Path) -> _Layout | None:
    """Top of the working tree, and which worktree this is ("" for the main one)."""
    probe = run_git(
        repo, "rev-parse", "--show-toplevel", "--absolute-git-dir", "--git-common-dir"
    )
    lines = probe.stdout.decode("utf-8", "surrogateescape").splitlines()
    if probe.returncode != 0 or len(lines) != 3:
        return None
    top, git_dir, common = (Path(line) for line in lines)
    if not common.is_absolute():
        common = Path(repo) / common
    same = os.path.normcase(os.path.realpath(git_dir)) == os.path.normcase(
        os.path.realpath(common)
    )
    return _Layout(top=top, worktree="" if same else git_dir.name)


@contextmanager
def _scratch_dir() -> Iterator[Path]:
    """A private directory outside the repository for the temporary index (and an empty hooks path)."""
    with tempfile.TemporaryDirectory(
        prefix="aletheore-shield-", ignore_cleanup_errors=True
    ) as name:
        yield Path(name)


# ---------------------------------------------------------------------------- building a tree


def _list_candidates(top: Path) -> tuple[list[str], dict[str, str], list[str]] | None:
    """Tracked paths, tracked gitlinks (path -> commit) and untracked non-ignored paths, each sorted."""
    tracked_out = run_git(top, "ls-files", "-s", "-z")
    untracked_out = run_git(top, "ls-files", "-o", "--exclude-standard", "-z")
    if tracked_out.returncode != 0 or untracked_out.returncode != 0:
        return None
    tracked: set[str] = set()
    gitlinks: dict[str, str] = {}
    for record in _split_z(tracked_out.stdout):
        meta, _, raw_path = record.partition(b"\t")
        fields = meta.split(b" ")
        if not raw_path or len(fields) != 3:
            continue
        path = _decode_path(raw_path)
        if fields[0].decode("ascii", "replace") == _GITLINK_MODE:
            gitlinks[path] = fields[1].decode("ascii")
        else:
            tracked.add(path)
    untracked = {_decode_path(raw) for raw in _split_z(untracked_out.stdout)} - tracked
    return sorted(tracked), gitlinks, sorted(untracked)


def _select(top: Path, ordered: list[str]) -> tuple[list[str], list[str]]:
    """Choose which paths go into the snapshot. Missing paths are left out silently (absent = deleted)."""
    chosen: list[str] = []
    skipped: list[str] = []
    total = 0
    for position, path in enumerate(ordered):
        if path.startswith(_PRIVATE_DIR):
            continue
        if path.endswith("/") or is_denied_path(path):
            # A trailing slash is an untracked nested repository; git cannot hold its files here.
            skipped.append(path)
            continue
        try:
            info = os.lstat(top / path)
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError:
            skipped.append(path)
            continue
        if stat.S_ISDIR(info.st_mode):
            continue
        is_link = stat.S_ISLNK(info.st_mode)
        if not is_link and not stat.S_ISREG(info.st_mode):
            skipped.append(path)
            continue
        size = info.st_size
        if not is_link and (
            size > MAX_FILE_BYTES or not os.access(top / path, os.R_OK)
        ):
            skipped.append(path)
            continue
        if total + size > MAX_TOTAL_BYTES:
            skipped.extend(
                p for p in ordered[position:] if not p.startswith(_PRIVATE_DIR)
            )
            break
        total += size
        chosen.append(path)
    return chosen, skipped


def _snapshot(top: Path, scratch: Path) -> _Snapshot | None:
    """Hash the working tree into a temporary index in scratch and return its tree id."""
    listed = _list_candidates(top)
    if listed is None:
        return None
    tracked, gitlinks, untracked = listed
    # Tracked files first: when the total cap is hit, the user's source wins over untracked output.
    chosen, skipped = _select(top, tracked + untracked)
    links = {
        path: sha for path, sha in gitlinks.items() if not path.startswith(_PRIVATE_DIR)
    }
    env = {"GIT_INDEX_FILE": str(scratch / "index")}

    if chosen:
        added = run_git(
            top,
            *_INDEX_CONFIG,
            "update-index",
            "--add",
            "--remove",
            "-z",
            "--stdin",
            env=env,
            input=b"".join(_encode_path(p) + b"\0" for p in chosen),
            timeout=_WRITE_TIMEOUT_S,
        )
        if added.returncode != 0:
            return None
    if links:
        info = b"".join(
            f"{_GITLINK_MODE} {sha}\t".encode("ascii") + _encode_path(path) + b"\0"
            for path, sha in sorted(links.items())
        )
        if (
            run_git(
                top,
                *_INDEX_CONFIG,
                "update-index",
                "-z",
                "--index-info",
                env=env,
                input=info,
            ).returncode
            != 0
        ):
            return None
    written = run_git(
        top, *_INDEX_CONFIG, "write-tree", env=env, timeout=_WRITE_TIMEOUT_S
    )
    tree = written.stdout.decode("ascii", "replace").strip()
    if written.returncode != 0 or not tree:
        return None
    return _Snapshot(tree=tree, files=len(chosen) + len(links), skipped=tuple(skipped))


# ---------------------------------------------------------------------------- reading checkpoints


def _parse_commit(ref: str, commit: str, body: bytes) -> _Stored | None:
    header, sep, message = body.partition(b"\n\n")
    if not sep:
        return None
    tree = ""
    for line in header.split(b"\n"):
        if line.startswith(b"tree "):
            tree = line[5:].decode("ascii", "replace").strip()
            break
    text = message.decode("utf-8", "replace")
    subject, _, rest = text.partition("\n\n")
    if not tree or not subject.startswith(_SUBJECT):
        return None
    try:
        meta = json.loads(rest)
        created_at = _utc(datetime.fromisoformat(meta["created_at"]))
        session = meta["session"]
        files = meta["files"]
        skipped = meta["skipped"]
        worktree = meta["worktree"]
    except (ValueError, KeyError, TypeError):
        return None
    if not (
        isinstance(session, str)
        and isinstance(files, int)
        and isinstance(worktree, str)
        and isinstance(skipped, list)
        and all(isinstance(s, str) for s in skipped)
    ):
        return None
    checkpoint = Checkpoint(
        ref=ref,
        commit=commit,
        tree=tree,
        created_at=created_at,
        session=session,
        files=files,
        skipped=tuple(skipped),
    )
    return _Stored(checkpoint=checkpoint, worktree=worktree)


def _read_all(top: Path) -> list[_Stored]:
    """Every checkpoint Shield wrote in this repository (all worktrees), newest first. Foreign refs are skipped."""
    refs_out = run_git(
        top,
        "for-each-ref",
        "--sort=-refname",
        "--format=%(objectname) %(objecttype) %(refname)",
        REF_PREFIX,
    )
    if refs_out.returncode != 0:
        return []
    refs: list[tuple[str, str]] = []
    for line in refs_out.stdout.decode("utf-8", "replace").splitlines():
        parts = line.split(" ", 2)
        if len(parts) == 3 and parts[1] == "commit":
            refs.append((parts[2], parts[0]))
    if not refs:
        return []
    batch = run_git(
        top,
        "cat-file",
        "--batch",
        input=b"".join(f"{sha}\n".encode("ascii") for _, sha in refs),
    )
    if batch.returncode != 0:
        return []
    bodies: dict[str, bytes] = {}
    data = batch.stdout
    pos = 0
    while pos < len(data):
        end = data.find(b"\n", pos)
        if end < 0:
            break
        head = data[pos:end].split(b" ")
        pos = end + 1
        if len(head) != 3:
            continue
        size = int(head[2])
        bodies[head[0].decode("ascii")] = data[pos : pos + size]
        pos += size + 1
    stored = []
    for ref, sha in refs:
        body = bodies.get(sha)
        parsed = _parse_commit(ref, sha, body) if body is not None else None
        if parsed is not None:
            stored.append(parsed)
    return stored


def _for_worktree(layout: _Layout) -> list[Checkpoint]:
    return [
        s.checkpoint for s in _read_all(layout.top) if s.worktree == layout.worktree
    ]


def _latest_checkpoint(layout: _Layout) -> Checkpoint | None:
    found = _for_worktree(layout)
    return found[0] if found else None


def list_checkpoints(repo: Path, limit: int = 50) -> list[Checkpoint]:
    """This worktree's checkpoints, newest first."""
    layout = _layout(Path(repo))
    if layout is None:
        return []
    return _for_worktree(layout)[: max(limit, 0)]


# ---------------------------------------------------------------------------- writing checkpoints


def _message(
    session_id: str, created_at: datetime, snapshot: _Snapshot, worktree: str
) -> bytes:
    meta = {
        "v": _FORMAT_VERSION,
        "session": session_id,
        "created_at": created_at.isoformat(),
        "files": snapshot.files,
        "skipped": list(snapshot.skipped),
        "worktree": worktree,
        "nonce": secrets.token_hex(8),
    }
    subject = f"{_SUBJECT}: {snapshot.files} files, {len(snapshot.skipped)} skipped"
    return (
        subject + "\n\n" + json.dumps(meta, ensure_ascii=True, sort_keys=True) + "\n"
    ).encode("ascii")


def _store(
    layout: _Layout,
    scratch: Path,
    session_id: str,
    created_at: datetime,
    snapshot: _Snapshot,
) -> Checkpoint | None:
    head = run_git(layout.top, "rev-parse", "-q", "--verify", "HEAD^{commit}")
    parent = (
        head.stdout.decode("ascii", "replace").strip() if head.returncode == 0 else ""
    )
    stamp = f"@{int(created_at.timestamp())} +0000"
    env = {**_IDENT_ENV, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    prefix = REF_PREFIX + created_at.strftime("%Y%m%dT%H%M%S%fZ") + "-"
    # A fresh nonce per attempt gives a fresh commit id, so a ref name collision can only be retried, never shared.
    for _attempt in range(3):
        made = run_git(
            layout.top,
            "commit-tree",
            "--no-gpg-sign",
            snapshot.tree,
            *(("-p", parent) if parent else ()),
            env=env,
            input=_message(session_id, created_at, snapshot, layout.worktree),
        )
        commit = made.stdout.decode("ascii", "replace").strip()
        if made.returncode != 0 or not commit:
            return None
        ref = prefix + commit[:8]
        # The empty old value makes this create-only: an existing ref is never overwritten.
        created = run_git(
            layout.top,
            *_REF_CONFIG,
            "-c",
            f"core.hooksPath={scratch}",
            "update-ref",
            "--no-deref",
            ref,
            commit,
            "",
        )
        if created.returncode == 0:
            return Checkpoint(
                ref=ref,
                commit=commit,
                tree=snapshot.tree,
                created_at=created_at,
                session=session_id,
                files=snapshot.files,
                skipped=snapshot.skipped,
            )
    return None


def take_checkpoint(
    repo: Path,
    session_id: str,
    *,
    force: bool = False,
    now: datetime | None = None,
    min_interval_s: float = 5.0,
) -> Checkpoint | None:
    """Snapshot the working tree. None when the repo state is unsupported, when the tree equals the latest
    checkpoint's tree, when within min_interval_s of the latest one (unless force), or when git fails."""
    repo = Path(repo)
    if not repo_state(repo).ok:
        return None
    layout = _layout(repo)
    if layout is None:
        return None
    created_at = _utc(now)
    latest = _latest_checkpoint(layout)
    if latest is not None and not force:
        elapsed = (created_at - latest.created_at).total_seconds()
        if abs(elapsed) < min_interval_s:
            return None
    with _scratch_dir() as scratch:
        snapshot = _snapshot(layout.top, scratch)
        if snapshot is None:
            return None
        if latest is not None and snapshot.tree == latest.tree:
            return None
        return _store(layout, scratch, session_id, created_at, snapshot)


def prune_checkpoints(
    repo: Path, *, keep: int = 50, keep_days: int = 7, now: datetime | None = None
) -> int:
    """Delete old checkpoint refs; returns how many were deleted. Within each worktree a ref survives when it is
    among the newest `keep` or younger than `keep_days`. Refs Shield did not write are left alone."""
    layout = _layout(Path(repo))
    if layout is None:
        return 0
    cutoff = _utc(now) - timedelta(days=keep_days)
    seen: dict[str, int] = {}
    deleted = 0
    for stored in _read_all(layout.top):
        rank = seen.get(stored.worktree, 0)
        seen[stored.worktree] = rank + 1
        checkpoint = stored.checkpoint
        if rank < keep or checkpoint.created_at > cutoff:
            continue
        # Passing the old value means a ref that changed under us is not deleted.
        result = run_git(
            layout.top,
            *_REF_CONFIG,
            "update-ref",
            "--no-deref",
            "-d",
            checkpoint.ref,
            checkpoint.commit,
        )
        if result.returncode == 0:
            deleted += 1
    return deleted


def changed_since(repo: Path, checkpoint: Checkpoint) -> list[str]:
    """Repo-relative paths whose content differs between the checkpoint and the working tree now, compared
    with the same capture rules (so ignored, deny-listed and oversized files are not reported).
    Raises CheckpointError when git cannot answer, rather than claiming nothing changed."""
    layout = _layout(Path(repo))
    if layout is None:
        raise CheckpointError("this folder is not a git working tree")
    with _scratch_dir() as scratch:
        snapshot = _snapshot(layout.top, scratch)
    if snapshot is None:
        raise CheckpointError("git could not snapshot the working tree")
    diff = run_git(
        layout.top,
        "diff-tree",
        "-r",
        "-z",
        "--name-only",
        "--no-renames",
        checkpoint.tree,
        snapshot.tree,
    )
    if diff.returncode != 0:
        raise CheckpointError(
            "git could not compare the checkpoint with the working tree"
        )
    return [_decode_path(raw) for raw in _split_z(diff.stdout)]
