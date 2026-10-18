import json

import pytest

from aletheore.mcp_budget import (
    DEFAULT_BUDGET_CHARS,
    apply_budget,
    budget_chars,
)


def size_of(data):
    return len(json.dumps(data))


def rows(n, prefix="r"):
    return [{"id": i, "severity": ["info", "error", "warning"][i % 3], "name": f"{prefix}{i}", "msg": "x" * 40} for i in range(n)]


def big():
    return {"checked": True, "findings": rows(2000), "schema": {"tables": rows(300, "t"), "dialect": ["sql"]}, "small": [1, 2, 3]}


def call(data, budget=6000, **kw):
    return apply_budget(data, size_of=size_of, budget_chars=budget, **kw)


# --- flag helpers -----------------------------------------------------------

def test_budget_chars_default_override_and_invalid_values():
    assert budget_chars({}) == DEFAULT_BUDGET_CHARS == 20000
    assert budget_chars({"ALETHEORE_MCP_BUDGET_CHARS": "5000"}) == 5000
    for bad in ("abc", "0", "-5", "10.5"):
        with pytest.raises(ValueError, match="ALETHEORE_MCP_BUDGET_CHARS"):
            budget_chars({"ALETHEORE_MCP_BUDGET_CHARS": bad})


# --- under budget -----------------------------------------------------------

def test_a_result_within_budget_is_returned_unchanged():
    data = {"checked": True, "items": rows(5)}
    assert call(data, budget=100000) is data


# --- over budget: overview --------------------------------------------------

def test_over_budget_result_fits_the_budget_and_keeps_its_keys():
    data = big()
    out = call(data)
    assert size_of(out) <= 6000
    assert set(out) == set(data) | {"_budget"}
    assert out["checked"] is True and out["small"] == [1, 2, 3]
    assert out["schema"]["dialect"] == ["sql"]


def test_truncated_lists_are_reported_with_totals_and_next_offset():
    out = call(big())
    t = out["_budget"]["truncated"]
    assert set(t) == {"findings", "schema.tables"}
    for path, info in t.items():
        shown = len(out["findings"]) if path == "findings" else len(out["schema"]["tables"])
        assert info["shown"] == shown and info["next_offset"] == shown
        assert info["total"] == {"findings": 2000, "schema.tables": 300}[path]
        assert shown >= 3
    assert "section" in out["_budget"]["how_to_get_more"]


def test_every_nonempty_list_keeps_a_few_items_even_under_a_tiny_budget():
    out = call(big(), budget=1500)
    assert len(out["findings"]) >= 3 and len(out["schema"]["tables"]) >= 3


def test_facets_summarise_low_cardinality_fields_of_truncated_lists():
    info = call(big())["_budget"]["truncated"]["findings"]
    assert info["facets"]["severity"] == {"info": 667, "error": 667, "warning": 666}
    assert "name" not in info["facets"]  # high-cardinality fields are not faceted


def test_output_is_deterministic():
    assert call(big()) == call(big())


def test_a_top_level_list_result_is_budgeted_under_the_root_path():
    out = call(rows(2000))
    assert out["_budget"]["truncated"]["$"]["total"] == 2000
    assert len(out["items"]) < 2000


def test_one_oversized_string_is_trimmed_with_a_visible_marker():
    data = {"items": [{"id": 1, "blob": "y" * 50000}] + rows(400)}
    out = call(data, budget=6000)
    blob = out["items"][0]["blob"]
    assert len(blob) < 1000 and "chars omitted" in blob


# --- drill-down -------------------------------------------------------------

def collect(data, section, **kw):
    items, offset = [], 0
    while offset is not None:
        page = call(data, section=section, offset=offset, **kw)
        assert size_of(page) <= 6000
        items.extend(page["items"])
        offset = page["next_offset"]
    return items


def test_paging_a_section_reconstructs_the_whole_list_exactly():
    data = big()
    assert collect(data, "findings") == data["findings"]
    assert collect(data, "schema.tables") == data["schema"]["tables"]


def test_limit_caps_page_size_and_never_exceeds_the_budget():
    page = call(big(), section="findings", limit=7)
    assert len(page["items"]) == 7 and page["next_offset"] == 7 and page["total"] == 2000
    huge = call(big(), section="findings", limit=100000)
    assert size_of(huge) <= 6000 and huge["shown"] < 2000


def test_filter_narrows_a_section_before_paging():
    data = big()
    want = [r for r in data["findings"] if "r17" in json.dumps(r)]
    got = collect(data, "findings", filter="R17")  # case-insensitive substring
    assert got == want
    page = call(data, section="findings", filter="r17")
    assert page["total"] == len(want) and page["unfiltered_total"] == 2000


def test_an_unknown_section_lists_the_available_ones():
    out = call(big(), section="nope")
    assert "unknown section" in out["error"]
    assert out["sections"] == {"findings": 2000, "schema.tables": 300, "schema.dialect": 1, "small": 3}


def test_offset_past_the_end_returns_an_empty_final_page():
    page = call(big(), section="small", offset=99)
    assert page["items"] == [] and page["next_offset"] is None and page["total"] == 3


# --- ordering ---------------------------------------------------------------

def test_sort_keys_rank_items_before_truncating_and_paging():
    rank = {"error": 0, "warning": 1, "info": 2}
    data = {"findings": rows(900)}
    sort_by = {"findings": lambda r: rank[r["severity"]]}
    out = apply_budget(data, size_of=size_of, budget_chars=3000, sort_by=sort_by)
    assert all(r["severity"] == "error" for r in out["findings"])  # the most severe come first
    paged = []
    off = 0
    while off is not None:
        p = apply_budget(data, size_of=size_of, budget_chars=3000, section="findings", offset=off, sort_by=sort_by)
        paged.extend(p["items"])
        off = p["next_offset"]
    assert [r["id"] for r in paged] == [r["id"] for r in sorted(data["findings"], key=sort_by["findings"])]
