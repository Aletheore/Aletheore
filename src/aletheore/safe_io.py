"""Tolerant file reads for scan stages.

An unreadable file (permission denied, removed mid-scan, a path past the
Windows length limit) or an undecodable one must never crash a scan. Every
stage that reads scanned source goes through these helpers (or an equivalent
try/except) so the file is skipped, and the skip is recorded here so the
evidence can list it (`repository.unreadable_files`) instead of hiding it.
"""

from __future__ import annotations

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


def collect_unreadable(repo_path: Path) -> list[dict]:
    """Recorded skips as repo-relative {"path", "reason"} entries, sorted by path."""
    root = str(repo_path)
    entries = []
    with _lock:
        items = list(_records.items())
    for raw, reason in items:
        rel = raw
        for prefix in (root, str(Path(root).resolve())):
            if raw.startswith(prefix + "/") or raw.startswith(prefix + "\\"):
                rel = raw[len(prefix) + 1:].replace("\\", "/")
                break
        entries.append({"path": rel, "reason": reason})
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
    """File text, or None (and a recorded skip) when it cannot be read or decoded."""
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
