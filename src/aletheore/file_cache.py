"""Per-file results cache shared by the scan stages that work file by file.

A re-scan of an unchanged repo used to redo every per-file stage from scratch
(on the Linux kernel: ~50s of secrets, ~30s of error handling, ~25s of line
counting). This stores each stage's result for each file keyed by the file's
content hash, so the next scan only does the files that changed.

Safety rules, the same ones scan-cache.json follows:

- Content hash, not mtime: a branch switch that touches mtimes without
  changing bytes is a hit, an edit that keeps the mtime is a miss.
- Every entry is stamped with the scanner's version *and* a hash of the source
  module that produced it, so changing the scanner (even in an editable
  install where __version__ doesn't move) invalidates its old results.
- Off under ALETHEORE_DISABLE_LOCAL_SCAN_CACHE (the hosted worker): a hosted
  checkout is someone else's repo, and they could commit a cache file that
  hides their own findings. ALETHEORE_FILE_CACHE_PATH can point a trusted
  caller (one that owns the file, outside the checkout) at its own database,
  one per repo: entries are keyed by repo-relative path, and each scan drops
  the rows for paths it didn't see, so two repos sharing a database would
  keep evicting each other (never wrong results, since hits need a matching
  content hash, just no reuse).
"""

import hashlib
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from aletheore import __version__

_DISABLE_LOCAL_CACHE_ENV = "ALETHEORE_DISABLE_LOCAL_SCAN_CACHE"
_CACHE_PATH_ENV = "ALETHEORE_FILE_CACHE_PATH"
_FILENAME = "file-cache.db"
_HASH_CHUNK_BYTES = 1024 * 1024

_code_versions: dict[tuple[str, ...], str] = {}


def _grammar_versions() -> str:
    """Installed tree-sitter runtime and grammar versions: a grammar upgrade
    can change parse results without any Aletheore code changing."""
    from importlib import metadata

    names = sorted(
        dist.metadata["Name"].lower()
        for dist in metadata.distributions()
        if (dist.metadata["Name"] or "").lower().startswith("tree-sitter")
    )
    return ",".join(f"{name}={metadata.version(name)}" for name in names)


def code_version(*module_files: str, parses: bool = False) -> str:
    """__version__, a hash of every module the result depends on, and (for
    stages that parse with tree-sitter) the installed grammar versions."""
    key = (*module_files, str(parses))
    if key not in _code_versions:
        hasher = hashlib.blake2b(digest_size=8)
        for module_file in module_files:
            try:
                hasher.update(Path(module_file).read_bytes())
            except OSError:
                hasher.update(b"unknown")
        if parses:
            hasher.update(_grammar_versions().encode())
        _code_versions[key] = f"{__version__}:{hasher.hexdigest()}"
    return _code_versions[key]


def _hash_one(path: Path) -> str | None:
    try:
        hasher = hashlib.blake2b(digest_size=16)
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(_HASH_CHUNK_BYTES), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except OSError:
        return None


# path -> ((size, mtime_ns), content hash), so the several stages of one scan
# hash each file once. Size and mtime only gate this in-process reuse; what gets
# compared against stored entries is always the content hash. Keyed by path, so
# a long-lived process (MCP server, hosted worker) holds one entry per file, not
# one per version of it.
_hash_memo: dict[str, tuple[tuple[int, int], str | None]] = {}


def content_hashes(paths: list[Path]) -> dict[Path, str | None]:
    todo: list[tuple[Path, tuple]] = []
    out: dict[Path, str | None] = {}
    for path in paths:
        try:
            st = path.stat()
        except OSError:
            out[path] = None
            continue
        stamp = (st.st_size, st.st_mtime_ns)
        memo = _hash_memo.get(str(path))
        if memo is not None and memo[0] == stamp:
            out[path] = memo[1]
        else:
            todo.append((path, stamp))
    if todo:
        # hashlib releases the GIL on large buffers, so threads overlap the reads.
        with ThreadPoolExecutor(max_workers=min(16, (os.cpu_count() or 1) * 2)) as executor:
            hashes = list(executor.map(lambda item: _hash_one(item[0]), todo))
        for (path, stamp), digest in zip(todo, hashes):
            _hash_memo[str(path)] = (stamp, digest)
            out[path] = digest
    return out


class FileCache:
    def __init__(self, db_path: Path):
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS entries ("
            " kind TEXT NOT NULL, path TEXT NOT NULL, hash TEXT NOT NULL,"
            " version TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY (kind, path))"
        )

    def get_many(self, kind: str, version: str, hashes: dict[str, str]) -> dict[str, object]:
        hits: dict[str, object] = {}
        rows = self._conn.execute("SELECT path, hash, version, payload FROM entries WHERE kind = ?", (kind,))
        for path, digest, entry_version, payload in rows:
            if entry_version == version and hashes.get(path) == digest:
                hits[path] = json.loads(payload)
        return hits

    def put_many(self, kind: str, version: str, entries: dict[str, tuple[str, object]], keep: set[str]) -> None:
        """Store fresh results and drop rows for files that no longer exist."""
        with self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO entries (kind, path, hash, version, payload) VALUES (?, ?, ?, ?, ?)",
                [(kind, path, digest, version, json.dumps(payload)) for path, (digest, payload) in entries.items()],
            )
            stale = [
                (kind, path)
                for (path,) in self._conn.execute("SELECT path FROM entries WHERE kind = ?", (kind,))
                if path not in keep
            ]
            self._conn.executemany("DELETE FROM entries WHERE kind = ? AND path = ?", stale)

    def close(self) -> None:
        self._conn.close()


def open_cache(repo_path: Path) -> FileCache | None:
    explicit = os.environ.get(_CACHE_PATH_ENV)
    if explicit:
        db_path = Path(explicit)
    elif os.environ.get(_DISABLE_LOCAL_CACHE_ENV):
        return None
    else:
        # Only inside an .aletheore/ a scan already created (and gitignored):
        # a standalone helper call must not drop a new file into someone's
        # working tree.
        if not (Path(repo_path) / ".aletheore").is_dir():
            return None
        db_path = Path(repo_path) / ".aletheore" / _FILENAME
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return FileCache(db_path)
    except (OSError, sqlite3.Error):
        return None


def cached_per_file(
    repo_path: Path,
    kind: str,
    version: str,
    jobs: list[tuple[Path, str]],
    compute,
) -> list:
    """Results for every (path, rel_path) job in input order: cached ones for
    files whose content hasn't changed, `compute(missing_jobs)` (which must
    return results in the same order) for the rest."""
    cache = open_cache(repo_path)
    if cache is None:
        return compute(jobs)
    try:
        hashes = content_hashes([path for path, _rel in jobs])
        rel_hashes = {rel: hashes[path] for path, rel in jobs if hashes[path] is not None}
        try:
            hits = cache.get_many(kind, version, rel_hashes)
        except (sqlite3.Error, json.JSONDecodeError):
            hits = {}
        missing = [job for job in jobs if job[1] not in hits]
        fresh = compute(missing) if missing else []
        by_rel = dict(hits)
        # Re-hash what was just computed and only store results whose file
        # didn't change mid-scan, so a result is never filed under bytes it
        # wasn't computed from.
        after = {path: _hash_one(path) for path, _rel in missing}
        new_entries: dict[str, tuple[str, object]] = {}
        for (path, rel), result in zip(missing, fresh):
            by_rel[rel] = result
            if rel in rel_hashes and after[path] == rel_hashes[rel]:
                new_entries[rel] = (rel_hashes[rel], result)
        try:
            cache.put_many(kind, version, new_entries, keep={rel for _p, rel in jobs})
        except sqlite3.Error:
            pass
        return [by_rel[rel] for _path, rel in jobs]
    finally:
        cache.close()
