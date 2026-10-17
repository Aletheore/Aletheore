"""Per-call output budgets for MCP tool results.

Some tools return unbounded lists: on a mid-sized repository a single unscoped
`aletheore_database` call is about a million characters. This module bounds a
result to a character budget WITHOUT losing anything: an over-budget result keeps
its shape, shows the first few items of each long list, says exactly how many were
left out, and the same tool can be called again with `section`, `offset`, `limit`
and `filter` to page through everything. Paging every page of a section yields
the original list exactly.

Off by default (see `budget_enabled`): with the flag unset the server returns
exactly what it always has. The flag is read once, when the server is built.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Mapping

BUDGET_ENV_VAR = "ALETHEORE_MCP_BUDGET"
BUDGET_CHARS_ENV_VAR = "ALETHEORE_MCP_BUDGET_CHARS"
DEFAULT_BUDGET_CHARS = 20_000

_TRUTHY = {"1", "true", "yes", "on"}
_MIN_SHOWN = 3  # every non-empty list keeps at least this many items in an overview
_MAX_STRING = 500  # longer strings inside shown items are trimmed with a visible marker
_FACET_MAX_VALUES = 12
_FACET_MIN_LIST = 10
_ROOT = "$"

SizeOf = Callable[[Any], int]


def budget_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return env.get(BUDGET_ENV_VAR, "").strip().lower() in _TRUTHY


def budget_chars(environ: Mapping[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    raw = env.get(BUDGET_CHARS_ENV_VAR)
    if raw is None or not raw.strip():
        return DEFAULT_BUDGET_CHARS
    text = raw.strip()
    if not text.isdigit() or int(text) < 1:
        raise ValueError(f"{BUDGET_CHARS_ENV_VAR} must be a positive whole number, got {raw!r}")
    return int(text)


# --- finding and rebuilding lists ---------------------------------------------

def _find_lists(data: Any) -> dict[str, list]:
    """Every list reachable through dicts, keyed by dotted path ('$' for a root list)."""
    found: dict[str, list] = {}

    def walk(node: Any, path: str) -> None:
        if isinstance(node, list):
            found[path or _ROOT] = node
        elif isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else str(key))

    walk(data, "")
    return found


def _rebuild(node: Any, path: str, replacements: dict[str, list]) -> Any:
    if isinstance(node, list):
        return replacements.get(path or _ROOT, node)
    if isinstance(node, dict):
        return {k: _rebuild(v, f"{path}.{k}" if path else str(k), replacements) for k, v in node.items()}
    return node


def _ordered(path: str, items: list, sort_by: Mapping[str, Callable[[Any], Any]] | None) -> list:
    if not sort_by:
        return items
    key = sort_by.get(path) or sort_by.get(path.rsplit(".", 1)[-1])
    return sorted(items, key=key) if key else items


def _trim(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) > _MAX_STRING:
            return f"{value[:_MAX_STRING]}...[{len(value) - _MAX_STRING} chars omitted]"
        return value
    if isinstance(value, dict):
        return {k: _trim(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_trim(v) for v in value]
    return value


def _facets(items: list) -> dict[str, dict[str, int]]:
    """Counts for low-cardinality string/bool fields, so a truncated list still tells you its makeup."""
    if len(items) < _FACET_MIN_LIST or not all(isinstance(i, dict) for i in items[:50]):
        return {}
    fields = [k for k, v in items[0].items() if isinstance(v, (str, bool))]
    out: dict[str, dict[str, int]] = {}
    for field in fields:
        counts: dict[str, int] = {}
        usable = True
        for item in items:
            value = item.get(field)
            if not isinstance(value, (str, bool)):
                usable = False
                break
            counts[str(value)] = counts.get(str(value), 0) + 1
            if len(counts) > _FACET_MAX_VALUES:
                usable = False
                break
        if usable and len(counts) >= 1:
            out[field] = counts
    return out


def _json_len(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str))


HOW_TO_GET_MORE = (
    "Some lists were cut to fit the output budget. Call this tool again with section=<path> and "
    "offset=<next_offset> to page through one list (optional: limit=<items per page>, filter=<text> "
    "to keep only items containing that text)."
)


# --- the two views --------------------------------------------------------------

def _overview(
    data: Any,
    lists: dict[str, list],
    size_of: SizeOf,
    budget: int,
    sort_by: Mapping[str, Callable[[Any], Any]] | None,
) -> Any:
    ordered = {p: _ordered(p, l, sort_by) for p, l in lists.items()}
    shown = {p: len(l) for p, l in ordered.items()}
    sample = {p: [_trim(i) for i in l[:20]] for p, l in ordered.items()}
    avg = {p: (sum(_json_len(i) for i in s) / len(s)) if s else 0 for p, s in sample.items()}

    def estimated() -> float:
        return sum(shown[p] * avg[p] for p in shown) + 600

    # Phase 1: cheap size estimates pick a starting point (avoids encoding a million characters repeatedly).
    while estimated() > budget * 0.9:
        candidates = [p for p in shown if shown[p] > _MIN_SHOWN]
        if not candidates:
            break
        biggest = max(candidates, key=lambda p: shown[p] * avg[p])
        shown[biggest] = max(_MIN_SHOWN, int(shown[biggest] * 0.7))

    facets = {p: _facets(ordered[p]) for p in shown if shown[p] < len(ordered[p])}

    def build() -> Any:
        replacements = {p: [_trim(i) for i in ordered[p][: shown[p]]] for p in shown if shown[p] < len(ordered[p])}
        truncated = {
            p: {"total": len(ordered[p]), "shown": shown[p], "next_offset": shown[p], "facets": facets[p]}
            for p in replacements
        }
        if not truncated:
            return data
        marker = {"truncated": truncated, "how_to_get_more": HOW_TO_GET_MORE}
        body = _rebuild(data, "", replacements)
        if isinstance(body, list):
            return {"items": body, "_budget": marker}
        return {**body, "_budget": marker}

    # Phase 2: verify with the real encoder and tighten until it fits (or nothing is left to cut).
    out = build()
    while size_of(out) > budget:
        candidates = [p for p in shown if shown[p] > _MIN_SHOWN]
        if not candidates:
            break
        for p in candidates:
            shown[p] = max(_MIN_SHOWN, int(shown[p] * 0.85))
        out = build()
    return out


def _section_view(
    lists: dict[str, list],
    section: str,
    offset: int,
    limit: int | None,
    needle: str | None,
    size_of: SizeOf,
    budget: int,
    sort_by: Mapping[str, Callable[[Any], Any]] | None,
) -> dict:
    if section not in lists:
        return {
            "error": f"unknown section {section!r}",
            "sections": {p: len(l) for p, l in lists.items()},
        }
    items = _ordered(section, lists[section], sort_by)
    unfiltered_total = len(items)
    if needle:
        low = needle.lower()
        items = [i for i in items if low in (i if isinstance(i, str) else _dump(i)).lower()]
    total = len(items)
    offset = max(0, offset)

    def view(page: list) -> dict:
        shown_end = offset + len(page)
        out: dict[str, Any] = {
            "section": section,
            "total": total,
            "offset": offset,
            "shown": len(page),
            "next_offset": shown_end if shown_end < total else None,
            "items": page,
        }
        if needle:
            out["unfiltered_total"] = unfiltered_total
        return out

    used = _json_len(view([]))
    page: list = []
    for item in items[offset:]:
        if limit is not None and len(page) >= limit:
            break
        trimmed = _trim(item)
        cost = _json_len(trimmed) + 2
        if page and used + cost > budget:
            break
        page.append(trimmed)
        used += cost
    out = view(page)
    while len(page) > 1 and size_of(out) > budget:  # the estimate is JSON-based; the real encoder decides
        page = page[: max(1, int(len(page) * 0.9))]
        out = view(page)
    return out


def _dump(item: Any) -> str:
    return json.dumps(item, ensure_ascii=False, default=str)


def apply_budget(
    data: Any,
    *,
    size_of: SizeOf,
    budget_chars: int,
    section: str | None = None,
    offset: int = 0,
    limit: int | None = None,
    filter: str | None = None,  # noqa: A002 - matches the tool parameter name agents see
    sort_by: Mapping[str, Callable[[Any], Any]] | None = None,
) -> Any:
    """Bound `data` to `budget_chars` as measured by `size_of`, or page one of its lists.

    No `section`: returns `data` untouched when it already fits; otherwise the same shape with
    long lists cut and an `_budget` entry describing what was left out and how to get it.
    With `section` (a dotted path from the `sections` listing, '$' for a root list): returns one page of
    that list, after the optional case-insensitive `filter`, in a deterministic order.
    """
    lists = _find_lists(data)
    if section is None:
        if offset or limit is not None or filter:
            return {"error": "offset, limit and filter apply to one list: pass section=<path> too", "sections": {p: len(l) for p, l in lists.items()}}
        if size_of(data) <= budget_chars:
            return data
        return _overview(data, lists, size_of, budget_chars, sort_by) if lists else data
    if limit is not None and limit < 1:
        return {"error": "limit must be at least 1"}
    return _section_view(lists, section, offset, limit, filter, size_of, budget_chars, sort_by)
