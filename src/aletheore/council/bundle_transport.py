"""Moving a worker's branch between machines as a git bundle.

A bundle holds only the new commits of one branch (relative to the room's shared
starting commit). Importing is deliberately narrow: the bytes must match the
announced SHA-256, git must verify the bundle against this repo, the bundle
must list exactly one head and it must be the expected branch, and the result
lands in refs/council/peers/<participant>/..., never in refs/heads/.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from aletheore.council.protocol import MAX_BUNDLE_BYTES
from aletheore.council.worktrees import GitError, _git, integration_branch

_BRANCH_RE = re.compile(r"^council/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+\Z")
_PARTICIPANT_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}\Z")
_SHA_RE = re.compile(r"^[0-9a-f]{40,64}\Z")


class TransportError(RuntimeError):
    pass


@dataclass
class BundleInfo:
    sha256: str
    size: int
    tip_sha: str
    base_sha: str


def validate_branch_name(name: str) -> str:
    if not isinstance(name, str) or not _BRANCH_RE.match(name) or any(seg.startswith("-") or ".." in seg for seg in name.split("/")):
        raise TransportError(f"invalid branch name: {name!r}")
    return name


def validate_participant(name: str) -> str:
    if not isinstance(name, str) or not _PARTICIPANT_RE.match(name) or name in (".", ".."):
        raise TransportError(f"invalid participant id: {name!r}")
    return name


def _validate_sha(sha: str) -> str:
    if not isinstance(sha, str) or not _SHA_RE.match(sha):
        raise TransportError("invalid commit sha")
    return sha


def has_commit(repo: Path, sha: str) -> bool:
    if not isinstance(sha, str) or not _SHA_RE.match(sha):
        return False
    return _git(repo, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode == 0


def ensure_integration_branch(repo: Path, room_id: str, base_sha: str) -> None:
    _validate_sha(base_sha)
    if not has_commit(repo, base_sha):
        raise TransportError(
            f"this repository does not have the room's starting commit {base_sha[:12]}; update your clone (fetch/pull) and try again"
        )
    branch = integration_branch(room_id)
    if _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode != 0:
        _git(repo, "branch", branch, base_sha)


def create_bundle(repo: Path, branch: str, base_sha: str, out_path: Path) -> BundleInfo:
    validate_branch_name(branch)
    _validate_sha(base_sha)
    try:
        tip = _git(repo, "rev-parse", f"refs/heads/{branch}").stdout.strip()
        ahead = int(_git(repo, "rev-list", "--count", f"{base_sha}..{branch}").stdout.strip() or "0")
    except GitError as exc:
        raise TransportError(f"cannot read branch {branch}: {exc}") from exc
    if ahead == 0:
        raise TransportError(f"branch {branch} has no new commits to send")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.unlink(missing_ok=True)
    try:
        _git(repo, "bundle", "create", str(out_path), f"refs/heads/{branch}", f"^{base_sha}")
    except GitError as exc:
        raise TransportError(f"could not create bundle: {exc}") from exc
    data = out_path.read_bytes()
    if len(data) > MAX_BUNDLE_BYTES:
        out_path.unlink(missing_ok=True)
        raise TransportError(f"bundle is {len(data)} bytes, over the {MAX_BUNDLE_BYTES} byte limit")
    return BundleInfo(hashlib.sha256(data).hexdigest(), len(data), tip, base_sha)


_FORBIDDEN_NAMES = {".gitattributes", ".lfsconfig", ".gitmodules"}


def _check_new_commits(repo: Path, ref: str) -> None:
    """Refuse symlinks, submodules and git config-like files in the commits a peer sent: they can make git or a
    reviewer's tools read or run things outside the working tree once the branch is merged."""
    out = _git(repo, "-c", "core.quotepath=false", "log", "--raw", "--no-renames", "-z", "--format=", ref, "--not", "--branches").stdout
    tokens = out.split("\0")
    i = 0
    while i < len(tokens):
        head = tokens[i]
        if not head.startswith(":"):
            i += 1
            continue
        fields = head[1:].split()
        path = tokens[i + 1] if i + 1 < len(tokens) else ""
        i += 2
        new_mode = fields[1] if len(fields) > 1 else ""
        if new_mode in ("120000", "160000"):
            raise TransportError(f"not allowed in a peer's branch: symlink or submodule at {path!r}")
        if path.rsplit("/", 1)[-1] in _FORBIDDEN_NAMES:
            raise TransportError(f"not allowed in a peer's branch: {path!r}")


def import_bundle(repo: Path, participant: str, branch: str, bundle_path: Path, expected_sha256: str) -> str:
    validate_participant(participant)
    validate_branch_name(branch)
    bundle_path = Path(bundle_path)
    data = bundle_path.read_bytes()
    if len(data) > MAX_BUNDLE_BYTES:
        raise TransportError("bundle is over the size limit")
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise TransportError("bundle does not match the announced sha-256")
    if _git(repo, "bundle", "verify", str(bundle_path), check=False).returncode != 0:
        raise TransportError("git cannot verify this bundle against this repository (missing starting commit or corrupt bundle)")
    heads = [ln.split(" ", 1) for ln in _git(repo, "bundle", "list-heads", str(bundle_path)).stdout.splitlines() if ln.strip()]
    if len(heads) != 1 or heads[0][1].strip() != f"refs/heads/{branch}":
        raise TransportError("bundle must contain exactly the expected branch and nothing else")
    ref = f"refs/council/peers/{participant}/{branch}"
    try:
        _git(repo, "-c", "fetch.fsckObjects=true", "fetch", "--no-tags", "--no-write-fetch-head", str(bundle_path), f"+refs/heads/{branch}:{ref}")
    except GitError as exc:
        raise TransportError(f"could not import bundle: {exc}") from exc
    try:
        _check_new_commits(repo, ref)
    except TransportError:
        _git(repo, "update-ref", "-d", ref, check=False)
        raise
    return ref
