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
"""

import json
import os
import re
import secrets
import shutil
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


def _snapshot(top: Path, scratch: Path) -> _Snapshot | None:
    """Hash the working tree into a temporary index in scratch and return its tree id."""
    seeded_index = scratch / "seeded-index"
    seeded = False
    real = _real_index(top)
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
