"""Checkpoints: snapshots of the working tree kept as commits under refs/aletheore/checkpoints/.

A checkpoint is built with plumbing only (update-index, write-tree, commit-tree, update-ref) against a
temporary index file outside the repository, so the user's index, branches, stash, HEAD, reflogs and
working tree are never touched. It holds tracked and untracked files, minus anything git ignores,
deny-listed secret files, Shield's own .aletheore/ folder, and files over the size caps.

Speed: the temporary index starts as a byte copy of the user's index (read only, mtime preserved), so
git reuses its stat data and only re-hashes files that changed. If that seeded index cannot produce a
tree (for example it holds conflict entries), the snapshot is rebuilt once from an empty index.

Each checkpoint commit's parent is the HEAD commit at capture time (none in a repository without
commits), never the previous checkpoint, so pruning a ref really lets git collect its objects.

Refs are named refs/aletheore/checkpoints/<worktree key>/<UTC timestamp>-<8 hex of commit>, so one
worktree's newest checkpoint is found without reading any other worktree's commits.

Restore (undo) first saves a safety checkpoint of the current files, then writes back only the files
whose content differs from the chosen checkpoint. It never deletes a file, never runs reset, clean or
checkout, and never touches the index, branches or stash. It refuses any path it could not safely write:
names that would leave the repository or enter .git, paths the capture rules exclude, paths under a
symbolic link or inside a nested repository, and files whose current content the safety checkpoint
could not hold (too large, unreadable, ignored), so undo can never destroy what it cannot give back.
Restore is two-phase (stage every file to a temporary file, then verify each target is unchanged since the
plan and move it into place), and restored bytes follow the repository's line-ending and filter
configuration exactly as `git checkout` would write them.
"""

import json
import os
import re
import secrets
import shutil
import stat
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aletheore.shield.denylist import is_denied_path
from aletheore.shield.gitutil import RepoState, repo_state, run_git

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_SKIPPED_STORED = 200

REF_PREFIX = "refs/aletheore/checkpoints/"
_SUBJECT = "aletheore shield checkpoint"
_FORMAT_VERSION = 1
_GITLINK_MODE = "160000"
_PRIVATE_DIR = ".aletheore/"
_WRITE_TIMEOUT_S = 60.0
_SAFE_WORKTREE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_IDENT_ENV = {
    "GIT_AUTHOR_NAME": "Aletheore Shield",
    "GIT_AUTHOR_EMAIL": "shield@aletheore.invalid",
    "GIT_COMMITTER_NAME": "Aletheore Shield",
    "GIT_COMMITTER_EMAIL": "shield@aletheore.invalid",
}
# Config that must not leak from the user's setup into our temporary index or our private refs:
# safecrlf would abort hashing, splitIndex would drop sharedindex files into .git, fsmonitor and the
# untracked cache are for the user's index, reflogs and reference-transaction hooks are for the user's refs.
_INDEX_CONFIG = (
    "-c",
    "core.safecrlf=false",
    "-c",
    "core.splitIndex=false",
    "-c",
    "core.untrackedCache=false",
    "-c",
    "core.fsmonitor=false",
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
    # The first MAX_SKIPPED_STORED skipped names; skipped_count is the full number.
    skipped: tuple[str, ...]
    skipped_count: int = 0
    skipped_truncated: bool = False


@dataclass(frozen=True)
class _Layout:
    top: Path
    worktree: str
    index: Path | None = None  # the user's index file for this worktree, when it exists


@dataclass(frozen=True)
class _Snapshot:
    tree: str
    files: int
    skipped: tuple[str, ...]


@dataclass(frozen=True)
class _Plan:
    add: list[str]  # paths hashed from disk into the index
    drop: list[str]  # tracked paths that must not be in the snapshot
    links: dict[str, str]  # gitlinks to keep, path -> commit
    skipped: list[str]


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


def _paths_input(paths: list[str]) -> bytes:
    return b"".join(_encode_path(p) + b"\0" for p in paths)


def _layout(repo: Path) -> _Layout | None:
    """Top of the working tree, and which worktree this is ("" for the main one)."""
    # One git call answers all four (the index path too, which _snapshot would otherwise ask for separately).
    probe = run_git(
        repo,
        "rev-parse",
        "--show-toplevel",
        "--absolute-git-dir",
        "--git-common-dir",
        "--git-path",
        "index",
    )
    lines = probe.stdout.decode("utf-8", "surrogateescape").splitlines()
    if probe.returncode != 0 or len(lines) != 4:
        return None
    top, git_dir, common, index = (Path(line) for line in lines)
    if not common.is_absolute():
        common = Path(repo) / common
    if not index.is_absolute():
        index = Path(repo) / index
    same = os.path.normcase(os.path.realpath(git_dir)) == os.path.normcase(
        os.path.realpath(common)
    )
    return _Layout(
        top=top, worktree="" if same else git_dir.name, index=index if index.is_file() else None
    )


def _worktree_prefix(worktree: str) -> str:
    """The ref folder for one worktree. Never collides: "main", "wt-<name>" or "wtx-<hex of name>"."""
    if not worktree:
        key = "main"
    elif _SAFE_WORKTREE_NAME.fullmatch(worktree):
        key = f"wt-{worktree}"
    else:
        key = "wtx-" + worktree.encode("utf-8", "surrogateescape").hex()
    return f"{REF_PREFIX}{key}/"


@contextmanager
def _scratch_dir() -> Iterator[Path]:
    """A private directory outside the repository for the temporary index (and an empty hooks path)."""
    with tempfile.TemporaryDirectory(
        prefix="aletheore-shield-", ignore_cleanup_errors=True
    ) as name:
        yield Path(name)


# ---------------------------------------------------------------------------- building a tree


def _list_candidates(
    top: Path, env: dict[str, str] | None
) -> tuple[list[str], dict[str, str], list[str], set[str]] | None:
    """Tracked paths, tracked gitlinks (path -> commit), untracked non-ignored paths (each sorted), and
    tracked paths flagged assume-unchanged or skip-worktree, whose index entries git will not refresh."""
    tracked_out = run_git(top, "ls-files", "-s", "-v", "-z", env=env)
    untracked_out = run_git(top, "ls-files", "-o", "--exclude-standard", "-z", env=env)
    if tracked_out.returncode != 0 or untracked_out.returncode != 0:
        return None
    tracked: set[str] = set()
    gitlinks: dict[str, str] = {}
    flagged: set[str] = set()
    for record in _split_z(tracked_out.stdout):
        meta, _, raw_path = record.partition(b"\t")
        fields = meta.split(b" ")
        if not raw_path or len(fields) != 4:
            continue
        tag, mode, sha = (f.decode("ascii", "replace") for f in fields[:3])
        path = _decode_path(raw_path)
        if mode == _GITLINK_MODE:
            gitlinks[path] = sha
            continue
        tracked.add(path)
        if tag.islower() or tag == "S":
            flagged.add(path)
    untracked = {_decode_path(raw) for raw in _split_z(untracked_out.stdout)} - tracked
    return sorted(tracked), gitlinks, sorted(untracked), flagged


def _under_symlink(top: Path, path: str, cache: dict[str, bool]) -> bool:
    """True when a parent folder of path is a symbolic link; git refuses paths "beyond a symbolic link"."""
    prefix = ""
    for part in path.split("/")[:-1]:
        prefix = f"{prefix}/{part}" if prefix else part
        hit = cache.get(prefix)
        if hit is None:
            try:
                hit = stat.S_ISLNK(os.lstat(os.path.join(top, prefix)).st_mode)
            except OSError:
                hit = False
            cache[prefix] = hit
        if hit:
            return True
    return False


def _readable(path: str, info: os.stat_result) -> bool:
    """Cheap readability test: the owner bit for our own files, a real access() call otherwise."""
    getuid = getattr(os, "getuid", None)
    if getuid is not None and info.st_uid == getuid():
        return bool(info.st_mode & stat.S_IRUSR)
    return os.access(path, os.R_OK)


def _select(top: Path, ordered: list[str]) -> tuple[list[str], list[str]]:
    """Choose which paths go into the snapshot. Missing paths are left out silently (absent = deleted)."""
    chosen: list[str] = []
    skipped: list[str] = []
    links_seen: dict[str, bool] = {}
    base = os.fspath(top) + os.sep
    total = 0
    for position, path in enumerate(ordered):
        if path.startswith(_PRIVATE_DIR):
            continue
        if (
            path.endswith("/")
            or is_denied_path(path)
            or _under_symlink(top, path, links_seen)
        ):
            # A trailing slash is an untracked nested repository; git cannot hold its files here.
            skipped.append(path)
            continue
        try:
            info = os.lstat(base + path)
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
        if not is_link and (size > MAX_FILE_BYTES or not _readable(base + path, info)):
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


def _plan(top: Path, env: dict[str, str] | None) -> _Plan | None:
    listed = _list_candidates(top, env)
    if listed is None:
        return None
    tracked, gitlinks, untracked, flagged = listed
    # Tracked files first: when the total cap is hit, the user's source wins over untracked output.
    chosen, skipped = _select(top, tracked + untracked)
    keep = set(chosen)
    links = {p: sha for p, sha in gitlinks.items() if not p.startswith(_PRIVATE_DIR)}
    drop = [p for p in tracked if p not in keep]
    drop.extend(p for p in gitlinks if p not in links)
    # A seeded index would keep stale content for flagged entries; drop them so --add re-hashes from disk.
    drop.extend(p for p in chosen if p in flagged)
    return _Plan(add=chosen, drop=sorted(drop), links=links, skipped=skipped)


def _real_index(top: Path) -> Path | None:
    """The user's index file for this worktree (linked worktrees have their own)."""
    found = run_git(top, "rev-parse", "--git-path", "index")
    if found.returncode != 0:
        return None
    path = Path(found.stdout.decode("utf-8", "surrogateescape").rstrip("\n"))
    if not path.is_absolute():
        path = top / path
    return path if path.is_file() else None


def _write_tree(top: Path, index: Path, plan: _Plan, seeded: bool) -> str | None:
    """Fill the index at `index` per plan and return the tree id. A seeded index already holds a copy of
    the user's entries, so tracked paths that must stay out are removed and gitlinks are already there."""
    env = {"GIT_INDEX_FILE": str(index)}
    if seeded:
        if plan.drop:
            dropped = run_git(
                top,
                *_INDEX_CONFIG,
                "update-index",
                "--force-remove",
                "-z",
                "--stdin",
                env=env,
                input=_paths_input(plan.drop),
                timeout=_WRITE_TIMEOUT_S,
            )
            if dropped.returncode != 0:
                return None
    if plan.add:
        added = run_git(
            top,
            *_INDEX_CONFIG,
            "update-index",
            "--add",
            "--remove",
            "-z",
            "--stdin",
            env=env,
            input=_paths_input(plan.add),
            timeout=_WRITE_TIMEOUT_S,
        )
        if added.returncode != 0:
            return None
    if not seeded and plan.links:
        info = b"".join(
            f"{_GITLINK_MODE} {sha}\t".encode("ascii") + _encode_path(path) + b"\0"
            for path, sha in sorted(plan.links.items())
        )
        linked = run_git(
            top,
            *_INDEX_CONFIG,
            "update-index",
            "-z",
            "--index-info",
            env=env,
            input=info,
        )
        if linked.returncode != 0:
            return None
    written = run_git(
        top, *_INDEX_CONFIG, "write-tree", env=env, timeout=_WRITE_TIMEOUT_S
    )
    tree = written.stdout.decode("ascii", "replace").strip()
    return tree if written.returncode == 0 and tree else None


def _snapshot(
    top: Path, scratch: Path, real_index: Path | None | bool = False
) -> _Snapshot | None:
    """Hash the working tree into a temporary index in scratch and return its tree id. `real_index` is the
    user's index when the caller already knows it (None: there is none); by default it is looked up."""
    seeded_index = scratch / "seeded-index"
    seeded = False
    real = _real_index(top) if real_index is False else real_index
    if real is not None:
        try:
            # Read-only copy; copy2 keeps the mtime, which git needs to judge the cached stat data.
            shutil.copy2(real, seeded_index)
            seeded = True
        except OSError:
            seeded = False
    # List from the copy, not the live index, so an entry added meanwhile cannot slip past the rules.
    list_env = {"GIT_INDEX_FILE": str(seeded_index)} if seeded else None
    plan = _plan(top, list_env)
    if plan is None and seeded:
        # git could not read the copy (for example a split index): list from the live index instead.
        seeded = False
        plan = _plan(top, None)
    if plan is None:
        return None
    tree = _write_tree(top, seeded_index, plan, True) if seeded else None
    if tree is None:
        # Seeded index unusable (for example conflict entries): rebuild once from an empty index.
        tree = _write_tree(top, scratch / "fresh-index", plan, False)
    if tree is None:
        return None
    return _Snapshot(
        tree=tree, files=len(plan.add) + len(plan.links), skipped=tuple(plan.skipped)
    )


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
        skipped_count = meta.get("skipped_count", len(skipped))
        truncated = meta.get("truncated", False)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    if not (
        isinstance(session, str)
        and isinstance(files, int)
        and isinstance(worktree, str)
        and isinstance(skipped, list)
        and all(isinstance(s, str) for s in skipped)
        and isinstance(skipped_count, int)
        and not isinstance(skipped_count, bool)
        and isinstance(truncated, bool)
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
        skipped_count=skipped_count,
        skipped_truncated=truncated,
    )
    return _Stored(checkpoint=checkpoint, worktree=worktree)


def _list_refs(top: Path, prefix: str) -> list[tuple[str, str]]:
    """(ref, commit) under prefix, newest name first. Only names are read, no commit bodies."""
    refs_out = run_git(
        top,
        "for-each-ref",
        "--sort=-refname",
        "--format=%(objectname) %(objecttype) %(refname)",
        prefix,
    )
    if refs_out.returncode != 0:
        return []
    refs: list[tuple[str, str]] = []
    for line in refs_out.stdout.decode("utf-8", "replace").splitlines():
        parts = line.split(" ", 2)
        if len(parts) == 3 and parts[1] == "commit":
            refs.append((parts[2], parts[0]))
    return refs


def _load(top: Path, refs: list[tuple[str, str]]) -> list[_Stored]:
    """Parse the commits behind refs in one cat-file call; refs Shield did not write are dropped."""
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


def _for_worktree(layout: _Layout, limit: int) -> list[Checkpoint]:
    """Up to limit of this worktree's checkpoints, newest first, parsing only as many bodies as needed."""
    refs = _list_refs(layout.top, _worktree_prefix(layout.worktree))
    found: list[Checkpoint] = []
    start = 0
    while len(found) < limit and start < len(refs):
        chunk = refs[start : start + limit]
        start += limit
        found.extend(
            s.checkpoint
            for s in _load(layout.top, chunk)
            if s.worktree == layout.worktree
        )
    return found[:limit]


def _latest_checkpoint(layout: _Layout) -> Checkpoint | None:
    found = _for_worktree(layout, 1)
    return found[0] if found else None


def list_checkpoints(repo: Path, limit: int = 50) -> list[Checkpoint]:
    """This worktree's checkpoints, newest first."""
    layout = _layout(Path(repo))
    if layout is None or limit <= 0:
        return []
    return _for_worktree(layout, limit)


# ---------------------------------------------------------------------------- writing checkpoints


def _message(
    session_id: str, created_at: datetime, snapshot: _Snapshot, worktree: str
) -> bytes:
    count = len(snapshot.skipped)
    meta = {
        "v": _FORMAT_VERSION,
        "session": session_id,
        "created_at": created_at.isoformat(),
        "files": snapshot.files,
        "skipped": list(snapshot.skipped[:MAX_SKIPPED_STORED]),
        "skipped_count": count,
        "truncated": count > MAX_SKIPPED_STORED,
        "worktree": worktree,
        "nonce": secrets.token_hex(8),
    }
    subject = f"{_SUBJECT}: {snapshot.files} files, {count} skipped"
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
    prefix = (
        _worktree_prefix(layout.worktree)
        + created_at.strftime("%Y%m%dT%H%M%S%fZ")
        + "-"
    )
    count = len(snapshot.skipped)
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
                skipped=snapshot.skipped[:MAX_SKIPPED_STORED],
                skipped_count=count,
                skipped_truncated=count > MAX_SKIPPED_STORED,
            )
    return None


def take_checkpoint(
    repo: Path,
    session_id: str,
    *,
    force: bool = False,
    now: datetime | None = None,
    min_interval_s: float = 5.0,
    checked: RepoState | None = None,
) -> Checkpoint | None:
    """Snapshot the working tree. None when the repo state is unsupported, or when git fails, or when the
    latest checkpoint of this worktree belongs to the same session and either is within min_interval_s
    (unless force) or holds the same tree.

    The rate limit and the unchanged-tree skip compare with the latest checkpoint only when that checkpoint is
    the same session's, so every session gets a checkpoint of its own starting point even when another session
    saved an identical tree a moment ago. `force` bypasses only the rate limit. `checked` is a repo_state result
    the caller already has for this repo (saves the probe)."""
    repo = Path(repo)
    if not (checked if checked is not None else repo_state(repo)).ok:
        return None
    layout = _layout(repo)
    if layout is None:
        return None
    created_at = _utc(now)
    latest = _latest_checkpoint(layout)
    if latest is not None and latest.session != session_id:
        latest = None
    if latest is not None and not force:
        elapsed = (created_at - latest.created_at).total_seconds()
        if abs(elapsed) < min_interval_s:
            return None
    with _scratch_dir() as scratch:
        snapshot = _snapshot(layout.top, scratch, layout.index)
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
    for stored in _load(layout.top, _list_refs(layout.top, REF_PREFIX)):
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


def delete_all_checkpoints(repo: Path) -> int:
    """Delete every ref under REF_PREFIX, for all worktrees; returns how many were deleted.

    Only the refs go. The commits they pointed at stay in the object store until git's own housekeeping drops
    them; this never runs gc, prune or anything else that rewrites the object store."""
    layout = _layout(Path(repo))
    if layout is None:
        return 0
    listed = run_git(layout.top, "for-each-ref", "--format=%(objectname) %(refname)", REF_PREFIX)
    if listed.returncode != 0:
        return 0
    deleted = 0
    for line in listed.stdout.decode("utf-8", "surrogateescape").splitlines():
        sha, _, ref = line.partition(" ")
        if not ref.startswith(REF_PREFIX):
            continue
        # Passing the old value means a ref that changed under us is not deleted.
        result = run_git(layout.top, *_REF_CONFIG, "update-ref", "--no-deref", "-d", ref, sha)
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


# ---------------------------------------------------------------------------- restoring checkpoints

_LINK_MODE = "120000"
_EXEC_MODE = "100755"
_RESTORABLE_MODES = ("100644", _EXEC_MODE, _LINK_MODE)
_GIT_DIR_NAMES = (".git", "git~1")  # git~1 is the Windows short name of .git
# The blob of a missing path. diff-tree prints all zeros for it (40 or 64, by hash), normalized to this.
_ABSENT = ""


@dataclass(frozen=True)
class RestorePlan:
    # Files whose content differs from the checkpoint or that are missing now; restore writes these.
    to_write: tuple[str, ...]
    # Files that exist now but not in the checkpoint. Never deleted, so undo cannot destroy new work.
    left_in_place: tuple[str, ...]
    # Files that differ but that restore refuses to touch: unsafe names, paths the capture rules exclude,
    # paths under a symbolic link or inside a nested repository, and files whose current content the
    # safety checkpoint cannot hold (too large, unreadable, ignored). Never written, never deleted.
    blocked: tuple[str, ...] = ()


@dataclass(frozen=True)
class RestoreResult:
    restored: tuple[str, ...]
    safety: Checkpoint | None


@dataclass(frozen=True)
class _Entry:
    path: str
    mode: str  # the checkpoint's mode for path
    blob: str  # the checkpoint's blob for path
    seen_mode: str  # the mode the plan saw on disk ("000000" when absent)
    seen_blob: str  # the blob the plan saw on disk (_ABSENT when absent)


@dataclass(frozen=True)
class _Restore:
    plan: RestorePlan
    entries: tuple[_Entry, ...]  # one per to_write path
    current_tree: str


@dataclass(frozen=True)
class _Staged:
    entry: _Entry
    target: str
    temp: str


def _same_name(part: str, name: str) -> bool:
    """Compare a path part as Windows and case-insensitive file systems do: trailing dots and spaces dropped."""
    return part.rstrip(". ").lower() == name


def _safe_relative(path: str) -> bool:
    """True for a plain repo-relative path: no absolute path, no empty, "." or ".." part, never inside .git."""
    if not path or path.startswith("/") or "\0" in path:
        return False
    for part in path.split("/"):
        if part in ("", ".", ".."):
            return False
        # Windows ignores trailing dots and spaces, so ".git." and ".git " name the .git folder there.
        if any(_same_name(part, name) for name in _GIT_DIR_NAMES):
            return False
        if os.name == "nt" and (":" in part or "\\" in part):
            return False
    return True


def _is_private(path: str) -> bool:
    """True for Shield's own .aletheore/ folder, under any spelling a file system treats as the same."""
    first, sep, _ = path.partition("/")
    return bool(sep) and _same_name(first, _PRIVATE_DIR.rstrip("/"))


def _parent_problem(
    top: Path, path: str, created: list[str] | None = None
) -> str | None:
    """Why path's parent folders cannot be written safely, or None. Every existing parent must be a real
    folder (not a symbolic link, not a file) outside any nested repository. When created is a list, missing
    parents are made one level at a time (so no link is ever followed) and appended to it."""
    prefix = os.fspath(top)
    for part in path.split("/")[:-1]:
        prefix = os.path.join(prefix, part)
        try:
            info = os.lstat(prefix)
        except FileNotFoundError:
            if created is None:
                return None  # everything below is missing too, so nothing there can be a link
            try:
                os.mkdir(prefix)
                created.append(prefix)
            except FileExistsError:
                pass
            except OSError as exc:
                return f"could not create the folder {prefix}: {exc}"
            try:
                info = os.lstat(prefix)
            except OSError as exc:
                return f"could not read the folder {prefix}: {exc}"
        except OSError as exc:
            return f"could not read {prefix}: {exc}"
        if stat.S_ISLNK(info.st_mode):
            return f"{prefix} is a symbolic link"
        if not stat.S_ISDIR(info.st_mode):
            return f"{prefix} is not a folder"
        if os.path.lexists(os.path.join(prefix, ".git")):
            return f"{prefix} is a nested repository"
    return None


def _parse_raw_diff(output: bytes) -> list[tuple[str, str, str, str, str, str]]:
    """(status, old mode, old blob, new mode, new blob, path) per record of `diff-tree -r -z` raw output."""
    records = []
    items = output.split(b"\0")
    pos = 0
    while pos + 1 < len(items):
        meta, raw_path = items[pos], items[pos + 1]
        pos += 2
        fields = meta.decode("ascii", "replace").lstrip(":").split(" ")
        if len(fields) != 5 or not raw_path:
            continue
        old_mode, new_mode, old_blob, new_blob, status = fields
        records.append(
            (status[:1], old_mode, old_blob, new_mode, new_blob, _decode_path(raw_path))
        )
    return records


def _blob_sizes(top: Path, blobs: list[str]) -> dict[str, int]:
    if not blobs:
        return {}
    found = run_git(
        top,
        "cat-file",
        "--batch-check",
        input=b"".join(f"{sha}\n".encode("ascii") for sha in blobs),
        timeout=_WRITE_TIMEOUT_S,
    )
    if found.returncode != 0:
        raise CheckpointError("git could not read the checkpoint's files")
    sizes: dict[str, int] = {}
    for line in found.stdout.decode("ascii", "replace").splitlines():
        parts = line.split(" ")
        if len(parts) == 3 and parts[1] == "blob" and parts[2].isdigit():
            sizes[parts[0]] = int(parts[2])
    return sizes


def _restore_plan(top: Path, checkpoint: Checkpoint, scratch: Path) -> _Restore:
    """Diff the checkpoint against a fresh snapshot of the working tree, taken with the same capture rules."""
    snapshot = _snapshot(top, scratch)
    if snapshot is None:
        raise CheckpointError("git could not snapshot the working tree")
    diff = run_git(
        top,
        "diff-tree",
        "-r",
        "-z",
        "--no-renames",
        checkpoint.tree,
        snapshot.tree,
        timeout=_WRITE_TIMEOUT_S,
    )
    if diff.returncode != 0:
        raise CheckpointError(
            "git could not compare the checkpoint with the working tree"
        )
    records = _parse_raw_diff(diff.stdout)
    sizes = _blob_sizes(
        top,
        [
            blob
            for status, mode, blob, _, _, _ in records
            if status != "A" and mode in _RESTORABLE_MODES
        ],
    )
    # The full list: these are files on disk now whose content the safety checkpoint cannot hold.
    not_captured_now = set(snapshot.skipped)
    skipped_then = set(checkpoint.skipped)
    entries: list[_Entry] = []
    left: list[str] = []
    blocked: list[str] = []
    for status, mode, blob, seen_mode, seen_blob, path in records:
        if status == "A":
            left.append(path)
            continue
        refuse = (
            not _safe_relative(path)
            or _is_private(path)
            or is_denied_path(path)
            or path in skipped_then
            or path in not_captured_now
            or mode not in _RESTORABLE_MODES
            or (mode == _LINK_MODE and os.name == "nt")
            or blob not in sizes
            or (mode != _LINK_MODE and sizes[blob] > MAX_FILE_BYTES)
            or _parent_problem(top, path) is not None
        )
        if not refuse:
            try:
                info = os.lstat(os.path.join(top, *path.split("/")))
            except (FileNotFoundError, NotADirectoryError):
                info = None
            except OSError:
                refuse = True
                info = None
            if info is not None:
                # A folder would have to be deleted; a file absent from the fresh snapshot (ignored, say)
                # was not captured, so overwriting it would lose content the safety checkpoint lacks.
                refuse = stat.S_ISDIR(info.st_mode) or status == "D"
        if refuse:
            blocked.append(path)
        else:
            entries.append(
                _Entry(
                    path=path,
                    mode=mode,
                    blob=blob,
                    seen_mode=seen_mode,
                    seen_blob=seen_blob if seen_blob.strip("0") else _ABSENT,
                )
            )
    plan = RestorePlan(
        to_write=tuple(e.path for e in entries),
        left_in_place=tuple(left),
        blocked=tuple(blocked),
    )
    return _Restore(plan=plan, entries=tuple(entries), current_tree=snapshot.tree)


def plan_restore(repo: Path, checkpoint: Checkpoint) -> RestorePlan:
    """What restoring checkpoint would do to the working tree now. Writes nothing.
    Raises CheckpointError when git cannot answer."""
    layout = _layout(Path(repo))
    if layout is None:
        raise CheckpointError("this folder is not a git working tree")
    with _scratch_dir() as scratch:
        return _restore_plan(layout.top, checkpoint, scratch).plan


def _blob_bytes(top: Path, entry: _Entry) -> bytes:
    """The bytes to put on disk, as `git checkout` would write them: files get git's checkout conversion for
    their path (core.autocrlf, eol and text attributes, smudge filters); links get their target verbatim."""
    if entry.mode == _LINK_MODE:
        args: tuple[str, ...] = ("cat-file", "blob", entry.blob)
    else:
        args = ("cat-file", "--filters", f"--path={entry.path}", entry.blob)
    read = run_git(top, *args, timeout=_WRITE_TIMEOUT_S)
    if read.returncode != 0:
        detail = read.stderr.decode("utf-8", "replace").strip()
        raise CheckpointError(
            f"git could not read {entry.path} from the checkpoint"
            + (f": {detail}" if detail else "")
        )
    return read.stdout


def _permissions(entry: _Entry, previous: os.stat_result | None) -> int | None:
    """Permission bits for a rewritten file: the old file's, with the executable bits set per the checkpoint."""
    if previous is None or not stat.S_ISREG(previous.st_mode) or os.name == "nt":
        return None
    bits = stat.S_IMODE(previous.st_mode)
    if entry.mode == _EXEC_MODE:
        return bits | ((bits & 0o444) >> 2)
    return bits & ~0o111


def _remove_quietly(path: str) -> None:
    try:
        if os.path.lexists(path):
            os.remove(path)
    except OSError:
        pass


def _remove_empty_folders(created: list[str]) -> None:
    for folder in reversed(created):
        try:
            os.rmdir(folder)  # fails, harmlessly, once anything is inside
        except OSError:
            pass


def _stage_one(top: Path, entry: _Entry, created: list[str], data: bytes) -> _Staged:
    """Write entry's bytes to a temporary file next to its target. The target itself is not touched."""
    problem = _parent_problem(top, entry.path, created)
    if problem is not None:
        raise CheckpointError(f"refused to write {entry.path}: {problem}")
    target = os.path.join(top, *entry.path.split("/"))
    parent = os.path.dirname(target)
    real_top = os.path.realpath(top)
    if os.path.commonpath([real_top, os.path.realpath(parent)]) != real_top:
        raise CheckpointError(
            f"refused to write {entry.path}: it is outside the repository"
        )
    try:
        previous: os.stat_result | None = os.lstat(target)
    except FileNotFoundError:
        previous = None
    except OSError as exc:
        raise CheckpointError(f"could not read {entry.path}: {exc}") from exc
    if previous is not None and stat.S_ISDIR(previous.st_mode):
        raise CheckpointError(f"refused to write {entry.path}: a folder is in the way")
    temp = os.path.join(
        parent, f".{os.path.basename(target)}.aletheore-restore-{secrets.token_hex(4)}"
    )
    try:
        if entry.mode == _LINK_MODE:
            os.symlink(os.fsdecode(data), temp)
        else:
            # The OS applies the umask to these, as it does for files git checks out.
            fresh = 0o777 if entry.mode == _EXEC_MODE else 0o666
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
            handle = os.open(temp, flags, fresh)
            with os.fdopen(handle, "wb") as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            bits = _permissions(entry, previous)
            if bits is not None:
                os.chmod(temp, bits)
    except OSError as exc:
        _remove_quietly(temp)
        raise CheckpointError(f"could not write {entry.path}: {exc}") from exc
    return _Staged(entry=entry, target=target, temp=temp)


def _stage(top: Path, entries: tuple[_Entry, ...], created: list[str]) -> list[_Staged]:
    """Phase 1: read every file from the checkpoint and write it to a temporary file. On any failure every
    temporary file and every folder made here is removed and CheckpointError is raised: nothing was restored."""
    staged: list[_Staged] = []
    total = 0
    try:
        for entry in entries:
            data = _blob_bytes(top, entry)
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise CheckpointError(
                    "the files to restore exceed the checkpoint size cap"
                )
            staged.append(_stage_one(top, entry, created, data))
    except BaseException as exc:
        for item in staged:
            _remove_quietly(item.temp)
        _remove_empty_folders(created)
        if isinstance(exc, CheckpointError):
            raise CheckpointError(f"{exc}. Nothing was restored.") from exc
        raise
    return staged


def _on_disk_blob(top: Path, entry: _Entry) -> str:
    """The blob id of what is at entry's path now, hashed the way capture hashes it; _ABSENT when missing."""
    target = os.path.join(top, *entry.path.split("/"))
    try:
        info = os.lstat(target)
    except (FileNotFoundError, NotADirectoryError):
        return _ABSENT
    if stat.S_ISLNK(info.st_mode):
        if entry.seen_mode != _LINK_MODE:
            return "link"
        found = run_git(
            top,
            "hash-object",
            "--no-filters",
            "--stdin",
            input=os.fsencode(os.readlink(target)),
        )
    elif stat.S_ISREG(info.st_mode):
        if entry.seen_mode == _LINK_MODE:
            return "file"
        found = run_git(
            top,
            *_INDEX_CONFIG,
            "hash-object",
            f"--path={entry.path}",
            "--",
            target,
            timeout=_WRITE_TIMEOUT_S,
        )
    else:
        return "other"
    if found.returncode != 0:
        raise CheckpointError(f"git could not hash {entry.path}")
    return found.stdout.decode("ascii", "replace").strip()


def _clear_read_only(target: str) -> None:
    """Windows refuses to replace a read-only file; the safety checkpoint holds it, so lift the flag."""
    if sys.platform != "win32":
        return
    try:
        mode = os.lstat(target).st_mode
    except OSError:
        return
    if stat.S_ISREG(mode) and not mode & stat.S_IWRITE:
        os.chmod(target, mode | stat.S_IWRITE)


def _commit(
    top: Path, staged: list[_Staged], created: list[str], safety: Checkpoint
) -> list[str]:
    """Phase 2: check each target still holds what the plan saw, then move its temporary file into place."""
    restored: list[str] = []
    for position, item in enumerate(staged):
        try:
            try:
                now = _on_disk_blob(top, item.entry)
            except OSError as exc:
                raise CheckpointError(
                    f"could not read {item.entry.path}: {exc}"
                ) from exc
            if now != item.entry.seen_blob:
                raise CheckpointError(
                    f"{item.entry.path} changed after the safety checkpoint was saved, so it was not"
                    " overwritten"
                )
            try:
                _clear_read_only(item.target)
                os.replace(item.temp, item.target)
            except OSError as exc:
                raise CheckpointError(
                    f"could not write {item.entry.path}: {exc}"
                ) from exc
        except CheckpointError as exc:
            for rest in staged[position:]:
                _remove_quietly(rest.temp)
            _remove_empty_folders(created)
            pending = [rest.entry.path for rest in staged[position:]]
            raise CheckpointError(
                f"{exc}. Restored: {', '.join(restored) or 'none'}. Not written: {', '.join(pending)}."
                f" The safety checkpoint {safety.ref} holds every file as it was before this restore."
            ) from exc
        restored.append(item.entry.path)
    return restored


def restore_checkpoint(
    repo: Path, checkpoint: Checkpoint, session_id: str
) -> RestoreResult:
    """Put the working tree's files back to how they were in checkpoint. Never prompts; callers confirm first.

    First saves a safety checkpoint of the current files (take_checkpoint with force), so the restore can
    itself be undone. That safety checkpoint is recorded under `session_id`; when the current files equal the
    latest checkpoint and it belongs to the same session, take_checkpoint saves nothing and that latest
    checkpoint is the safety one (a different session's identical checkpoint no longer suppresses it). When no safety checkpoint can be had, CheckpointError is raised
    and nothing is written, so RestoreResult.safety is only None if no write could have happened.

    Then writes only plan_restore's to_write files, in two phases. Phase 1 reads every file from the
    checkpoint and writes it to a temporary file beside its target; any failure there (a smudge filter that
    fails, a folder that cannot be written) removes the temporary files and raises with nothing restored.
    Phase 2 moves each temporary file into place, but first re-hashes the target and refuses to overwrite a
    file that changed since the plan (so a concurrent edit, which the safety checkpoint does not hold, is
    never lost). If phase 2 stops midway, CheckpointError names the files restored, the files not written and
    the safety checkpoint ref.

    Bytes follow the repository's git line-ending and filter configuration exactly as `git checkout` would:
    capture applied git's clean conversion and restore applies the checkout conversion. With no conversion
    configured, files come back byte for byte; with core.autocrlf=true an LF-only text file comes back with
    CRLF, and with core.autocrlf=input a CRLF file comes back with LF, as a checkout would write them.

    Files that are new since the checkpoint are left in place, blocked files are not touched, and reset,
    clean, checkout, the index, branches and stash are never used.
    """
    repo = Path(repo)
    state = repo_state(repo)
    if not state.ok:
        raise CheckpointError(state.reason or "Shield cannot work in this repository")
    layout = _layout(repo)
    if layout is None:
        raise CheckpointError("this folder is not a git working tree")
    safety = take_checkpoint(repo, session_id, force=True, checked=state)
    if safety is None:
        safety = _latest_checkpoint(layout)
    if safety is None:
        raise CheckpointError(
            "could not save a safety checkpoint of the current files; nothing was restored"
        )
    with _scratch_dir() as scratch:
        work = _restore_plan(layout.top, checkpoint, scratch)
    if work.current_tree != safety.tree:
        # Either files changed after the safety checkpoint, or take_checkpoint failed and the latest
        # checkpoint is stale. Either way the safety checkpoint does not hold what would be overwritten.
        raise CheckpointError(
            "the files changed while the safety checkpoint was being saved; nothing was restored, try again"
        )
    created: list[str] = []
    staged = _stage(layout.top, work.entries, created)
    restored = _commit(layout.top, staged, created, safety)
    return RestoreResult(restored=tuple(restored), safety=safety)
