"""Bounded reads of the audit evidence.

The audit's tool loop re-sends the whole conversation on every round, so an
oversized evidence read is paid for again on each later round. Measured on this
repository: one read of the top-level `repository` section returned about 260,000
tokens (`repository.modules` alone is two thirds of the whole evidence), and a
single audit then sent 4.9 million input tokens. The model needed a few thousand
tokens of it.

A read that fits within the budget is returned exactly as before. A larger one
comes back as an outline (large children named, with their size and the path to
read) or as the first page of a list, with the exact path for the next page, so
nothing is lost: everything is still reachable, it just has to be asked for.
"""
import re
from collections.abc import Callable

from aletheore.toon_encoding import to_toon

# About 9,000 tokens of TOON. A section at or under this is returned whole.
MAX_SECTION_CHARS = 30_000
# A separate, independently-calibrated ratio from live_wiki.py's
# _SUBSYSTEM_BATCH_CHARS_PER_TOKEN - not a duplicate to reconcile. That one
# measures JSON-encoded subsystem payloads; this one measures TOON-encoded
# evidence text. Different content shapes genuinely tokenize at different
# densities, so one ratio updating is not a signal the other is stale.
_APPROX_CHARS_PER_TOKEN = 3.3
_SLICE_RE = re.compile(r"^(?P<base>.*)\[(?P<start>\d*):(?P<end>\d*)\]$")
# Room for the page header, so a full page plus its header still fits the budget.
_HEADER_CHARS = 700


def _approx_tokens(chars: int) -> int:
    return round(chars / _APPROX_CHARS_PER_TOKEN)


def _describe(value) -> str:
    if isinstance(value, list):
        return f"list of {len(value)} items"
    if isinstance(value, dict):
        keys = ", ".join(list(value)[:8])
        more = ", ..." if len(value) > 8 else ""
        return f"object with {len(value)} keys ({keys}{more})"
    return f"text of {len(str(value))} characters"


def _child_path(base: str, key: str) -> str:
    return f"{base}.{key}" if base else key


def _outline(value: dict, base: str, max_chars: int) -> str:
    sizes = {key: len(to_toon(child)) for key, child in value.items()}
    kept: set[str] = set()
    used = 0
    for key in sorted(sizes, key=sizes.get):
        if used + sizes[key] > max_chars:
            continue
        kept.add(key)
        used += sizes[key]
    shown: dict = {
        "_bounded_view": (
            "this section is too large to return whole: children marked [elided ...] "
            "are not included, read the path given for each to see it"
        )
    }
    for key, child in value.items():
        if key in kept:
            shown[key] = child
        else:
            shown[key] = (
                f"[elided {_describe(child)}, about {_approx_tokens(sizes[key])} tokens; "
                f"read '{_child_path(base, key)}' to see it]"
            )
    return to_toon(shown)


def _fit_count(items: list, max_chars: int) -> int:
    """The most leading items whose TOON encoding fits the budget (at least 1)."""
    budget = max_chars - _HEADER_CHARS
    low, high = 1, len(items)
    while low < high:
        mid = (low + high + 1) // 2
        if len(to_toon(items[:mid])) <= budget:
            low = mid
        else:
            high = mid - 1
    return low


def _page(whole: list, base: str, start: int, end: int, max_chars: int) -> str:
    total = len(whole)
    end = min(end, total)
    window = whole[start:end]
    if not window:
        return to_toon({"_bounded_view": f"{base}[{start}:{end}] is empty; the list has {total} items"})
    if len(to_toon(window)) <= max_chars:
        if end >= total:
            return to_toon(window)
        # The slice fits, but the list goes on: say where, so paging is mechanical.
        # The next page is sized by the budget, not by this (possibly tiny) slice.
        step = _fit_count(whole[end:], max_chars)
        return to_toon(
            {
                "_bounded_view": (
                    f"items {start}:{end} of {total} in '{base}'; "
                    f"read '{base}[{end}:{min(total, end + step)}]' for the next page"
                ),
                "items": window,
            }
        )
    count = _fit_count(window, max_chars)
    nxt = start + count
    hint = f"items {start}:{nxt} of {total} in '{base}'"
    if nxt < total:
        step = _fit_count(whole[nxt:], max_chars)
        hint += f"; read '{base}[{nxt}:{min(total, nxt + step)}]' for the next page"
    if count == 1 and len(to_toon(window[:1])) > max_chars:
        # One item alone is over budget: bound it like any other oversized value.
        item_path = f"{base}[{start}]"
        hint += f"; item {start} is itself too large and is shown bounded below"
        return to_toon({"_bounded_view": hint}) + "\n" + _bound(window[0], item_path, max_chars)
    return to_toon({"_bounded_view": hint, "items": window[:count]})


def _bound(value, path: str, max_chars: int) -> str:
    encoded = to_toon(value)
    if len(encoded) <= max_chars:
        return encoded
    if isinstance(value, dict):
        return _outline(value, path, max_chars)
    if isinstance(value, list):
        return _page(value, path, 0, len(value), max_chars)
    return encoded[:max_chars] + f"\n[... cut at {max_chars} characters ...]"


def read_bounded(
    evidence,
    path: str,
    get_by_path: Callable[[object, str], object],
    max_chars: int = MAX_SECTION_CHARS,
) -> str | None:
    """TOON text for `path`, bounded to roughly `max_chars`, or None when the
    path does not exist. `[start:end]` as the last segment reads a slice of a
    list. Raises ToonEncodingError, as to_toon does, for unencodable data."""
    match = _SLICE_RE.match(path)
    if match:
        base = match.group("base")
        start = int(match.group("start") or 0)
        whole = get_by_path(evidence, base)
        if whole is None:
            return None
        if not isinstance(whole, list):
            return to_toon(
                {"_bounded_view": f"'{base}' is not a list, so it cannot be sliced; read '{base}' itself"}
            )
        end = int(match.group("end")) if match.group("end") else len(whole)
        return _page(whole, base, start, end, max_chars)

    value = get_by_path(evidence, path)
    if value is None:
        return None
    return _bound(value, path, max_chars)
