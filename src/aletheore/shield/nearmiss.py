"""Typosquat near-miss detection against a committed snapshot of popular packages.

Distance is optimal-string-alignment Damerau-Levenshtein: one insertion,
deletion, substitution or adjacent transposition counts as 1.
"""

import functools
import json
import re
from pathlib import Path

MIN_LENGTH = 5
_DATA = Path(__file__).resolve().parents[1] / "data" / "popular_packages.json"


@functools.lru_cache(maxsize=1)
def load_popular() -> dict:
    return json.loads(_DATA.read_text(encoding="utf-8"))


def _norm(ecosystem: str, name: str) -> str:
    if ecosystem == "PyPI":
        return re.sub(r"[-_.]+", "-", name).lower()
    return name.lower()


def _within_one(a: str, b: str) -> bool:
    """True when 0 < distance(a, b) <= 1 under Damerau-Levenshtein (OSA)."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diffs = [i for i in range(len(a)) if a[i] != b[i]]
        if len(diffs) == 1:
            return True  # substitution
        return len(diffs) == 2 and diffs[1] == diffs[0] + 1 and a[diffs[0]] == b[diffs[1]] and a[diffs[1]] == b[diffs[0]]
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    i = 0
    while i < len(short) and short[i] == long_[i]:
        i += 1
    return short[i:] == long_[i + 1 :]  # one insertion/deletion


@functools.lru_cache(maxsize=2)
def _index(ecosystem: str) -> tuple[frozenset[str], tuple[tuple[str, str], ...]]:
    key = "pypi" if ecosystem == "PyPI" else "npm"
    names = load_popular().get(key, [])
    normed = tuple((_norm(ecosystem, n), n) for n in names)
    return frozenset(n for n, _ in normed), normed


def near_miss(ecosystem: str, name: str) -> str | None:
    """The popular package `name` is one edit away from, or None."""
    target = _norm(ecosystem, name)
    if len(target) < MIN_LENGTH:
        return None
    popular, ordered = _index(ecosystem)
    if target in popular:
        return None
    for normed, original in ordered:  # popularity order: most popular wins
        if _within_one(target, normed):
            return original
    return None
