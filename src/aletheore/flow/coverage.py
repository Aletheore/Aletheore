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


def safe_this(interp, cls, f):
    """The constructor-populated `this` for an entry point. An unexpected error while building it (a construct the
    interpreter mishandles in a constructor) costs only the field values: the file is listed as incomplete and the
    entry point runs with a bare object, so one class can never take the whole flow stage down."""
    try:
        return interp.make_this(cls, f)
    except Exception:
        from aletheore.flow.values import Obj
        note(getattr(f, "path", f))
        return Obj(cls=cls, type_name=getattr(cls, "name", None))
