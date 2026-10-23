"""Tolerant file reads for scan stages.

An unreadable file (permission denied, removed mid-scan, a path past the
Windows length limit) or an undecodable one must never crash a scan. Every
stage that reads scanned source goes through these helpers (or an equivalent
try/except) so the file is skipped, and the skip is recorded here so the
evidence can list it (`repository.unreadable_files`) instead of hiding it.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

_lock = threading.Lock()
_records: dict[str, str] = {}


def reset_unreadable() -> None:
    """Forget everything recorded so far (start of a scan)."""
    with _lock:
        _records.clear()


def record_unreadable(path: Path | str, reason: str) -> None:
    with _lock:
        _records.setdefault(str(path), reason)


def _relative_to_root(raw: str, roots: list[str]) -> str:
    """raw as a repo-relative POSIX path. Comparison goes through
    os.path.normcase: on Windows that folds case and separators, so a drive
    letter or directory in a different case still matches; on POSIX it is the
    identity, so matching is exact (case-sensitive) there. A path equal to the
    root, with or without a trailing separator, is "."; a path outside every
    root is returned with forward slashes, otherwise unchanged."""
    raw = raw.rstrip("/" + os.sep) or raw
    folded = os.path.normcase(raw)
    for root in roots:
        folded_root = os.path.normcase(root).rstrip("/" + os.sep)
        if folded == folded_root:
            return "."
        for sep in {"/", os.sep}:
            if folded.startswith(folded_root + sep):
                return raw[len(folded_root) + 1:].replace(os.sep, "/")
    return raw.replace(os.sep, "/")


def collect_unreadable(repo_path: Path) -> list[dict]:
    """Recorded skips as repo-relative {"path", "reason"} entries, sorted by path."""
    roots = [str(repo_path)]
    resolved = str(Path(repo_path).resolve())
    if resolved not in roots:
        roots.append(resolved)
    with _lock:
        items = list(_records.items())
    entries = [{"path": _relative_to_root(raw, roots), "reason": reason} for raw, reason in items]
    entries.sort(key=lambda entry: entry["path"])
    return entries


def read_bytes_or_none(path: Path) -> bytes | None:
    """File bytes, or None (and a recorded skip) when it cannot be read."""
    try:
        return path.read_bytes()
    except OSError as exc:
        record_unreadable(path, f"could not read file: {exc.strerror or exc.__class__.__name__}")
        return None


def read_text_or_none(path: Path, encoding: str = "utf-8", errors: str = "ignore") -> str | None:
    """File text, or None (and a recorded skip) when it cannot be read.

    With the default errors="ignore" undecodable bytes are dropped silently and
    never raise; pass errors="strict" to have an undecodable file skipped and
    recorded as "could not decode file" instead."""
    try:
        return path.read_text(encoding=encoding, errors=errors)
    except OSError as exc:
        record_unreadable(path, f"could not read file: {exc.strerror or exc.__class__.__name__}")
        return None
    except UnicodeDecodeError:
        record_unreadable(path, "could not decode file")
        return None


def read_text_lenient(path: Path, encoding: str = "utf-8", errors: str = "ignore") -> str:
    """Like read_text_or_none but returns "" for an unreadable file, for
    manifest/config parsers that already treat empty text as "nothing declared"."""
    text = read_text_or_none(path, encoding=encoding, errors=errors)
    return "" if text is None else text
