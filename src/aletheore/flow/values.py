"""Abstract values for the flow interpreter.

Classification is what matters to callers: a value is "safe" (a constant, or provably benign),
"tainted" (carries attacker-controlled data) or "unknown" (could be either). Only "safe" lets a
finding be dropped.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Every change to a mutable value (a container's contents, its `opaque` flag, a rebound field) bumps one global epoch.
# `classify` remembers its answer on a value together with the epoch it was computed at, so an answer is reused
# exactly as long as nothing anywhere has changed. The tracking is automatic (below), so an interpreter cannot
# forget to invalidate: it mutates a TList/TDict or sets an attribute, and the epoch moves.
_EPOCH = [0]
_CHECK = bool(os.environ.get("ALETHEORE_FLOW_CHECK_CLASSIFY"))  # tests/diagnostics: assert cache == recomputation


def _bump() -> None:
    _EPOCH[0] += 1


def _tracked(base, names):
    def make(name):
        orig = getattr(base, name)

        def method(self, *a, **k):
            _EPOCH[0] += 1
            return orig(self, *a, **k)

        method.__name__ = name
        return method

    return {n: make(n) for n in names if hasattr(base, n)}


TList = type("TList", (list,), _tracked(list, (
    "append", "extend", "insert", "pop", "remove", "clear", "sort", "reverse", "__setitem__", "__delitem__",
    "__iadd__", "__imul__")))
_MISSING = object()


def _dict_setitem(self, key, value):
    old = dict.get(self, key, _MISSING)
    if old is not value and (old is _MISSING or old != value):  # writing back what is already there changes nothing
        _EPOCH[0] += 1
    dict.__setitem__(self, key, value)


TDict = type("TDict", (dict,), {**_tracked(dict, (
    "__delitem__", "pop", "popitem", "update", "setdefault", "clear", "__ior__")), "__setitem__": _dict_setitem})

_WATCHED = {"items": TList, "parts": TList, "entries": TDict, "fields": TDict, "opaque": None}


class _Tracked:
    """Mixin for the mutable values: rebinding a watched attribute bumps the epoch and wraps plain lists/dicts."""

    def __setattr__(self, name, value):
        if name in _WATCHED:
            kind = _WATCHED[name]
            if kind is not None and type(value) is not kind and isinstance(value, kind.__mro__[1]):
                value = kind(value)
            old = self.__dict__.get(name, _MISSING)
            # (the first assignment builds the value: nothing else can reach it yet, so no answer can be stale)
            if old is not _MISSING and old is not value and old != value:
                _EPOCH[0] += 1
        object.__setattr__(self, name, value)

    def __post_init__(self):
        for name, kind in _WATCHED.items():
            if kind is not None and hasattr(self, name):
                self.__setattr__(name, getattr(self, name))


class Val:
    """Base class for abstract values."""


class Char(str):
    """A Java char constant, kept distinct from a one-character String only for display."""


@dataclass(frozen=True)
class Const(Val):
    value: object  # str, Char, int, float, bool or None


@dataclass(frozen=True)
class Tainted(Val):
    why: str = "source"
    tid: int = 0  # identity of this tainted value, for interpreters that track validation (0 = untracked)
    prefix: str = ""  # constant text the value is known to start with ("https://api.example.com/" + key)
    lead: str = ""  # constant text immediately before the tainted part ("uploads/" + uuid + SUFFIX: ends with the random token)


class Unknown(Val):
    _instance: "Unknown | None" = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "Unknown"


class Safe(Val):
    """Benign but not a known constant: a parsed number, an encoded string."""

    _instance: "Safe | None" = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "Safe"


UNKNOWN = Unknown()
SAFE = Safe()
TAINT = Tainted("request")


@dataclass(frozen=True)
class Choice(Val):
    alts: frozenset


@dataclass(eq=False)
class ListV(_Tracked, Val):
    items: list
    birth_depth: int = 0
    dirty: bool = False
    opaque: bool = False  # an unmodeled call may have changed the contents


@dataclass(eq=False)
class MapV(_Tracked, Val):
    entries: dict
    birth_depth: int = 0
    dirty: bool = False
    opaque: bool = False


@dataclass(eq=False)
class BuilderV(_Tracked, Val):
    """A StringBuilder or StringBuffer: the parts appended so far."""

    parts: list
    birth_depth: int = 0
    dirty: bool = False
    opaque: bool = False


@dataclass(eq=False)
class Obj(_Tracked, Val):
    cls: object = None  # a ClassInfo for user classes, else None
    fields: dict = field(default_factory=dict)
    kind: str | None = None  # "request" for an HttpServletRequest-like object
    type_name: str | None = None


def join(a: Val, b: Val) -> Val:
    if a is b or a == b:
        return a
    alts: set = set()
    for v in (a, b):
        if isinstance(v, Choice):
            alts |= set(v.alts)
        else:
            alts.add(v)
    if len(alts) == 1:
        return next(iter(alts))
    # Distinct containers are kept as alternatives (a call or a sink is judged against each one) rather
    # than merged, so what each path built is not lost.
    return Choice(frozenset(alts))


def join_all(vals) -> Val:
    out = None
    for v in vals:
        out = v if out is None else join(out, v)
    return out if out is not None else UNKNOWN


def _has_taint(v: Val) -> bool:
    return classify(v) == "tainted"


_RANK = {"safe": 0, "unknown": 1, "tainted": 2}


def classify(v: Val, _depth: int = 0, _memo: dict | None = None) -> str:
    """"safe", "unknown" or "tainted". Containers are judged by what they hold.

    The answer for a value depends only on the value and the depth it is reached at. A mutable value keeps its
    answers for as long as the global epoch has not moved (nothing anywhere changed), and a walk keeps a per-call
    memo for values that share sub-structure, so a densely linked object graph is not walked again and again.
    "tainted" outranks everything, so a walk stops at the first tainted part."""
    if _depth > 6:
        return "unknown"
    t = type(v)
    if t is Unknown:  # leaves first: most of what a container holds
        return "unknown"
    if t is Const or t is Safe:
        return "safe"
    if t is Tainted:
        return "tainted"
    if _memo is None:
        _memo = {}
    if isinstance(v, _Tracked):
        cache = v.__dict__.get("_cls")
        if cache is None or cache[0] != _EPOCH[0]:
            cache = (_EPOCH[0], {})
            object.__setattr__(v, "_cls", cache)
        got = cache[1].get(_depth)
        if got is not None:
            if _CHECK:
                assert got == classify_reference(v, _depth), "stale classify cache"
            return got
        out = _classify_node(v, _depth, _memo)
        if cache[0] == _EPOCH[0]:  # (a classify never mutates, but never store an answer from another epoch)
            cache[1][_depth] = out
        return out
    return _classify_node(v, _depth, _memo)


def _classify_node(v: Val, _depth: int, _memo: dict) -> str:
    key = (id(v), _depth)
    hit = _memo.get(key)
    if hit is not None:
        return hit
    if isinstance(v, Choice):
        worst, parts = None, v.alts
    elif isinstance(v, ListV):
        worst, parts = ("unknown" if v.opaque else "safe"), v.items
    elif isinstance(v, MapV):
        worst, parts = ("unknown" if v.opaque else "safe"), v.entries.values()
    elif isinstance(v, BuilderV):
        worst, parts = ("unknown" if v.opaque else "safe"), v.parts
    elif isinstance(v, Obj):
        # A request object is the source container: handing it to code that is not analyzed proves
        # nothing about what that code does with it, so it is never "safe".
        out = "unknown"
        for item in v.fields.values():
            if classify(item, _depth + 1, _memo) == "tainted":
                out = "tainted"
                break
        _memo[key] = out
        return out
    else:
        return "unknown"
    rank = _RANK
    cur = -1 if worst is None else rank[worst]
    at_limit = _depth >= 6  # one level further is past the depth limit, where every part counts as "unknown"
    for item in parts:
        t = type(item)
        if at_limit or t is Unknown:
            r = 1
        elif t is Const or t is Safe:
            continue  # "safe" is the lowest rank: it cannot raise the worst
        else:
            r = rank[classify(item, _depth + 1, _memo)]
        if r > cur:
            cur = r
            if r == 2:
                break
    worst = "safe" if cur <= 0 else "unknown" if cur == 1 else "tainted"
    _memo[key] = worst
    return worst


def classify_reference(v: Val, _depth: int = 0) -> str:
    """The plain recursive definition of `classify`, with no caching. Tests compare against it."""
    if _depth > 6:
        return "unknown"
    if isinstance(v, (Const, Safe)):
        return "safe"
    if isinstance(v, Tainted):
        return "tainted"
    if isinstance(v, Choice):
        return max((classify_reference(a, _depth + 1) for a in v.alts), key=_RANK.get)
    if isinstance(v, (ListV, MapV, BuilderV)):
        parts = v.items if isinstance(v, ListV) else v.entries.values() if isinstance(v, MapV) else v.parts
        worst = "unknown" if v.opaque else "safe"
        for item in parts:
            worst = max(worst, classify_reference(item, _depth + 1), key=_RANK.get)
        return worst
    if isinstance(v, Obj):
        return "tainted" if any(classify_reference(i, _depth + 1) == "tainted" for i in v.fields.values()) else "unknown"
    return "unknown"


def add_prefix(result: Val, vals) -> Val:
    """A tainted result of concatenating `vals` (in order) remembers the constant text it starts with (cut at the first character
    that could be a format placeholder, so a fixed host or a local-path start survives string building) and the constant text
    directly before the first tainted part."""
    if not isinstance(result, Tainted) or (result.prefix and result.lead):
        return result
    acc, lead, cur, seen_unknown, prefix_done = "", "", "", False, False
    for v in vals:
        if isinstance(v, Const) and v.value is not None and not isinstance(v.value, bool):
            txt = v.value if isinstance(v.value, str) else str(v.value)
            cur += txt
            if not seen_unknown:
                acc += txt
        elif isinstance(v, Tainted):
            lead = cur + (v.lead if not cur else "")
            if not seen_unknown:
                acc += v.prefix
            break
        else:
            seen_unknown = True
            cur = ""
    for i, ch in enumerate(acc):
        if ch in "%{$#":
            acc = acc[:i]
            break
    if not acc and not lead:
        return result
    return Tainted(result.why, result.tid, acc or result.prefix, lead or result.lead)


# Looks like `scheme://host/`: once the path slash is reached nothing the program appends can change the host.
import re as _re
_FIXED_HOST = _re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://[^/?#@\\]+/")


def host_is_fixed(v: Val) -> bool:
    return isinstance(v, Tainted) and bool(_FIXED_HOST.match(v.prefix))


@dataclass(eq=False)
class ClosureV(Val):
    """A lambda value with the variables it was created with, so calling it later sees what it captured."""

    node: object
    file: object
    env: dict


_LOCAL_PATH = _re.compile(r"^/[^/\\]")


def path_prefix_safe(v: Val) -> bool:
    """A value that starts with `/segment`: a same-site path, so a redirect or fetch built on it cannot leave the site."""
    return isinstance(v, Tainted) and bool(_LOCAL_PATH.match(v.prefix))


RANDOM_MARK = "\x01RANDOM"  # stands for an unguessable generated token (UUID.randomUUID())


def random_adjacent(v: Val) -> bool:
    """Attacker text appended directly to an unguessable name (`uploads/` + uuid + suffix): a `..` component cannot be formed,
    and later components resolve inside a directory nobody can guess, so path traversal is not possible."""
    return isinstance(v, Tainted) and v.lead.endswith(RANDOM_MARK)
