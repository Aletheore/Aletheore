# The PyPI package is "python-toon" (xaviviro/python-toon) - it installs itself
# as the top-level module "toon", not "python_toon". The name "toon" alone is
# ALSO a real, unrelated PyPI package ("Tools for neuroscience experiments",
# aforren1/toon) - confirmed by actually inspecting it, not assumed. There is
# no collision today since aletheore doesn't depend on anything neuroscience-
# related, but this is why the dependency is pinned specifically to
# "python-toon", not the ambiguous bare name.
#
# The TOON spec's own reference implementation (PyPI: "toon-format") was tried
# first and rejected: its encoder is a literal stub as of 0.1.0
# ("NotImplementedError: TOON encoder is not yet implemented"), confirmed by
# actually calling it, not assumed from the README.
import math

import toon


class ToonEncodingError(Exception):
    """Raised when data can't be TOON-encoded, wrapping whatever the
    underlying library raised (confirmed live: at minimum ToonDecodeError-
    shaped round-trip failures on certain nested-list shapes, and
    RecursionError on pathologically deep data). Every caller of to_toon
    decides its own fallback here - skip a side file, fall back to JSON,
    surface a clean CLI error - so this module never decides that for them.
    """


def _sanitize_for_toon(data: object) -> object:
    # toon.encode() silently turns float('inf')/float('nan') into `null`
    # with no error - confirmed directly: toon.encode({"v": float("inf")})
    # -> 'v: null', indistinguishable from a real null on decode. air.json
    # (written via json.dumps, which permits Infinity/NaN by default) keeps
    # the real value, so an unsanitized TOON copy would silently diverge
    # from the canonical file on any ratio/division-derived evidence field.
    # Replacing non-finite floats with their str() here keeps that honest -
    # a visible "inf"/"nan" string survives round-tripping instead of a
    # silent, indistinguishable null.
    if isinstance(data, float):
        if math.isnan(data) or math.isinf(data):
            return str(data)
        return data
    if isinstance(data, dict):
        return {key: _sanitize_for_toon(value) for key, value in data.items()}
    if isinstance(data, list):
        return [_sanitize_for_toon(item) for item in data]
    return data


def to_toon(data: object) -> str:
    try:
        sanitized = _sanitize_for_toon(data)
        encoded = toon.encode(sanitized)
        # toon.encode() can succeed while producing output toon.decode()
        # then rejects - confirmed live and reproducibly:
        # {"nested": [[1, [2, 3]], [4, 5]]} encodes cleanly, but
        # toon.decode() on that exact output raises
        # ToonDecodeError("Expected 2 items, but got 0"). A heterogeneous
        # nested-list shape like this would otherwise slip past every
        # try/except in this codebase - they all catch ToonEncodingError,
        # but encode() itself never raises here, so the corruption would
        # sit silently in a written air.toon/MCP result until something
        # else calls decode() on it, possibly much later and with no way
        # to trace the failure back to this specific write. Verifying the
        # round trip here, once, means every existing call site's handling
        # also catches this class - not just the shapes where the
        # underlying library happens to raise on encode.
        if toon.decode(encoded) != sanitized:
            raise ValueError("encoded output does not round-trip back to the original data")
        return encoded
    except Exception as exc:
        raise ToonEncodingError(str(exc)) from exc
