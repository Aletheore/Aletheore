"""A one-pass check of which weakness classes can occur in a repository at all.

A flow in one file can end at a sink in another, so a class can be skipped everywhere only when none of its
sink words appears in any file. Scanning the bytes once is far cheaper than running every function against
every class, and most repositories use only a few of them.
"""

from __future__ import annotations

from pathlib import Path

_MAX_BYTES = 64_000_000


def classes_present(files: list[Path], sink_words: dict[int, tuple[bytes, ...]]) -> set[int]:
    present: set[int] = set()
    remaining = set(sink_words)
    for p in files:
        if not remaining:
            break
        try:
            data = p.read_bytes()[:_MAX_BYTES]
        except OSError:
            continue
        for c in list(remaining):
            if any(w in data for w in sink_words[c]):
                present.add(c)
                remaining.discard(c)
    return present
