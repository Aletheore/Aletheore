"""Abstract values for the flow interpreter.

Classification is what matters to callers: a value is "safe" (a constant, or provably benign),
"tainted" (carries attacker-controlled data) or "unknown" (could be either). Only "safe" lets a
finding be dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
class ListV(Val):
    items: list
    birth_depth: int = 0
    dirty: bool = False
    opaque: bool = False  # an unmodeled call may have changed the contents


@dataclass(eq=False)
class MapV(Val):
    entries: dict
    birth_depth: int = 0
    dirty: bool = False
    opaque: bool = False


@dataclass(eq=False)
class BuilderV(Val):
    """A StringBuilder or StringBuffer: the parts appended so far."""

    parts: list
    birth_depth: int = 0
    dirty: bool = False
    opaque: bool = False


@dataclass(eq=False)
class Obj(Val):
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


def classify(v: Val, _depth: int = 0) -> str:
    """"safe", "unknown" or "tainted". Containers are judged by what they hold."""
    if _depth > 6:
        return "unknown"
    if isinstance(v, (Const, Safe)):
        return "safe"
    if isinstance(v, Tainted):
        return "tainted"
    if isinstance(v, Choice):
        return max((classify(a, _depth + 1) for a in v.alts), key=_RANK.get)
    if isinstance(v, ListV):
        worst = "unknown" if v.opaque else "safe"
        for item in v.items:
            c = classify(item, _depth + 1)
            worst = max(worst, c, key=_RANK.get)
        return worst
    if isinstance(v, MapV):
        worst = "unknown" if v.opaque else "safe"
        for item in v.entries.values():
            worst = max(worst, classify(item, _depth + 1), key=_RANK.get)
        return worst
    if isinstance(v, BuilderV):
        worst = "unknown" if v.opaque else "safe"
        for item in v.parts:
            worst = max(worst, classify(item, _depth + 1), key=_RANK.get)
        return worst
    if isinstance(v, Obj):
        # A request object is the source container: handing it to code that is not analyzed proves
        # nothing about what that code does with it, so it is never "safe".
        worst = "unknown"
        if v.fields:
            for item in v.fields.values():
                if classify(item, _depth + 1) == "tainted":
                    return "tainted"
        return worst
    return "unknown"


def add_prefix(result: Val, vals) -> Val:
    """A tainted result of concatenating `vals` (in order) remembers the constant text it starts with, cut at the first
    character that could be a format placeholder, so a fixed host or a local-path start survives string building."""
    if not isinstance(result, Tainted) or result.prefix:
        return result
    acc = ""
    for v in vals:
        if isinstance(v, Const) and isinstance(v.value, str):
            acc += v.value
        elif isinstance(v, Const) and v.value is not None and not isinstance(v.value, bool):
            acc += str(v.value)
        elif isinstance(v, Tainted):
            acc += v.prefix
            break
        else:
            break
    for i, ch in enumerate(acc):
        if ch in "%{$#":
            acc = acc[:i]
            break
    return Tainted(result.why, result.tid, acc) if acc else result


# Looks like `scheme://host/`: once the path slash is reached nothing the program appends can change the host.
import re as _re
_FIXED_HOST = _re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://[^/?#@\\]+/")


def host_is_fixed(v: Val) -> bool:
    return isinstance(v, Tainted) and bool(_FIXED_HOST.match(v.prefix))
