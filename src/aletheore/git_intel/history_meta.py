"""Whole-history git facts that every scan needs, kept up to date incrementally.

`git rev-list --count HEAD` (total commits) and `git rev-list --max-parents=0
HEAD` (root commits, used for the repo key and the repo's age) each walk the
entire history: ~15s apiece on the Linux kernel's 1.46M commits, on every scan
even when nothing changed, and the root walk ran twice per scan.

Both have exact incremental forms when the previously scanned HEAD is an
ancestor of the current one:

    total(HEAD) = total(old) + count(old..HEAD)
    roots(HEAD) = roots(old) | roots(old..HEAD)

so a re-scan only walks the commits that landed since. The facts are memoized
in-process per (repo, HEAD) and persisted to .aletheore/git-meta.json for the
next scan. That file is skipped under ALETHEORE_DISABLE_LOCAL_SCAN_CACHE, the
same rule as scan-cache.json (a hosted checkout is someone else's repo, and
every hosted scan is a fresh clone anyway), and it is ignored whenever HEAD is
not a descendant of the cached HEAD or the repo's shallow boundary changed.
"""

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

META_FILENAME = "git-meta.json"
_META_VERSION = 1
_DISABLE_LOCAL_CACHE_ENV = "ALETHEORE_DISABLE_LOCAL_SCAN_CACHE"


@dataclass(frozen=True)
class HistoryFacts:
    head: str
    total_commits: int
    root_shas: tuple[str, ...]


_memo: dict[tuple[str, str, str], HistoryFacts] = {}


def _git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo_path, capture_output=True, text=True, errors="ignore"
    )


def _shallow_signature(repo_path: Path) -> str:
    """Changes whenever the shallow boundary does (a fetch --deepen or
    --unshallow changes the counts without moving HEAD)."""
    result = _git(repo_path, "rev-parse", "--git-path", "shallow")
    shallow_file = Path(repo_path) / result.stdout.strip() if result.returncode == 0 else None
    if shallow_file is None or not shallow_file.exists():
        return "full"
    return hashlib.blake2b(shallow_file.read_bytes(), digest_size=16).hexdigest()


def _meta_path(repo_path: Path) -> Path:
    return Path(repo_path) / ".aletheore" / META_FILENAME


def _local_cache_enabled() -> bool:
    return not os.environ.get(_DISABLE_LOCAL_CACHE_ENV)


def _load(repo_path: Path) -> dict:
    if not _local_cache_enabled():
        return {}
    try:
        data = json.loads(_meta_path(repo_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) and data.get("version") == _META_VERSION else {}


def _save(repo_path: Path, data: dict) -> None:
    if not _local_cache_enabled():
        return
    path = _meta_path(repo_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({**data, "version": _META_VERSION}), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _roots(repo_path: Path, rev_range: str) -> list[str] | None:
    result = _git(repo_path, "rev-list", "--max-parents=0", rev_range)
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.split() if line]


def _count(repo_path: Path, rev_range: str) -> int | None:
    result = _git(repo_path, "rev-list", "--count", rev_range)
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def history_facts(repo_path: Path) -> HistoryFacts | None:
    """Total commit count and root commits reachable from HEAD, or None when
    there is no HEAD (an empty repo) or git fails - callers keep their own
    error handling for that case."""
    head_result = _git(repo_path, "rev-parse", "HEAD")
    head = head_result.stdout.strip()
    if head_result.returncode != 0 or not head:
        return None
    shallow = _shallow_signature(repo_path)
    memo_key = (str(Path(repo_path).resolve()), head, shallow)
    if memo_key in _memo:
        return _memo[memo_key]

    cached = _load(repo_path).get("history")
    facts = None
    # A truncated, hand-edited or foreign file is treated as no cache: recompute.
    valid = (
        isinstance(cached, dict)
        and isinstance(cached.get("head"), str)
        and type(cached.get("total_commits")) is int
        and isinstance(cached.get("root_shas"), list)
        and all(isinstance(sha, str) for sha in cached["root_shas"])
    )
    if valid and cached.get("shallow") == shallow:
        old = cached["head"]
        if old == head:
            facts = HistoryFacts(head, cached["total_commits"], tuple(cached["root_shas"]))
        elif _git(repo_path, "merge-base", "--is-ancestor", old, head).returncode == 0:
            added = _count(repo_path, f"{old}..{head}")
            new_roots = _roots(repo_path, f"{old}..{head}")
            if added is not None and new_roots is not None:
                facts = HistoryFacts(
                    head,
                    cached["total_commits"] + added,
                    tuple(sorted(set(cached["root_shas"]) | set(new_roots))),
                )
    if facts is None:
        total = _count(repo_path, "HEAD")
        roots = _roots(repo_path, "HEAD")
        if total is None or roots is None:
            return None
        facts = HistoryFacts(head, total, tuple(sorted(roots)))

    _memo[memo_key] = facts
    meta = _load(repo_path)
    meta["history"] = {
        "head": facts.head,
        "shallow": shallow,
        "total_commits": facts.total_commits,
        "root_shas": list(facts.root_shas),
    }
    _save(repo_path, meta)
    return facts


def cached_ahead_behind(repo_path: Path, default_ref: str, compute) -> dict[str, tuple[int, int]] | None:
    """Every branch's (ahead, behind) vs default_ref, reused from the last scan
    when no ref tip and not the default ref moved. `compute` is the real
    one-subprocess for-each-ref computation (15s on the kernel's mirror)."""
    tips = _git(repo_path, "for-each-ref", "--format=%(refname:short)\t%(objectname)", "refs/heads", "refs/remotes")
    default_sha = _git(repo_path, "rev-parse", default_ref)
    if tips.returncode != 0 or default_sha.returncode != 0:
        return compute()
    signature = {
        "default_ref": default_ref,
        "default_sha": default_sha.stdout.strip(),
        "shallow": _shallow_signature(repo_path),
        "tips": dict(line.split("\t", 1) for line in tips.stdout.splitlines() if "\t" in line),
    }
    meta = _load(repo_path)
    cached = meta.get("ahead_behind")
    if isinstance(cached, dict) and cached.get("signature") == signature:
        try:
            return {name: (int(pair[0]), int(pair[1])) for name, pair in cached["counts"].items()}
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            pass  # malformed: recompute below
    counts = compute()
    if counts is not None:
        meta["ahead_behind"] = {"signature": signature, "counts": {k: list(v) for k, v in counts.items()}}
        _save(repo_path, meta)
    return counts
