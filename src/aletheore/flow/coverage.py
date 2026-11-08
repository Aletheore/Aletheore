"""Which files the flow engine gave up on (analysis budget exceeded), so a skipped file is never silent."""

from __future__ import annotations

from pathlib import Path

INCOMPLETE: set[str] = set()


def reset() -> None:
    INCOMPLETE.clear()


def note(path: Path | str) -> None:
    INCOMPLETE.add(str(path))


def relative(repo: Path) -> list[str]:
    root = str(repo.resolve())
    out = []
    for p in sorted(INCOMPLETE):
        out.append(p[len(root) + 1:] if p.startswith(root + "/") else p)
    return out
