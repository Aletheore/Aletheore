import re

import toon

from aletheore.adapters.openai_compatible import _get_by_dot_path
from aletheore.evidence_view import MAX_SECTION_CHARS, read_bounded
from aletheore.toon_encoding import to_toon


def _modules(n):
    return [{"path": f"pkg/mod_{i}.py", "imports": [], "imported_by": [f"pkg/x_{i}.py"], "loc": 10 * i} for i in range(n)]


def _evidence(n_modules=4000):
    return {
        "repository": {
            "languages": [{"name": "Python", "file_count": 3, "loc": 30}],
            "modules": _modules(n_modules),
            "dead_code": {"unreachable_modules": [], "unused_dependencies": []},
        },
        "git": {"total_commits": 12, "branches": [{"name": f"b{i}", "stale_days": i} for i in range(3000)]},
    }


def read(evidence, path, **kw):
    return read_bounded(evidence, path, _get_by_dot_path, **kw)


def test_a_section_within_the_budget_is_returned_exactly_as_before():
    evidence = _evidence()
    assert read(evidence, "repository.languages") == to_toon(evidence["repository"]["languages"])
    assert read(evidence, "git.total_commits") == to_toon(12)


def test_a_missing_path_is_none():
    assert read(_evidence(), "repository.nope") is None
    assert read(_evidence(), "repository.languages[0:5].x") is None


def test_an_oversized_object_becomes_an_outline_naming_the_big_children_and_their_paths():
    out = read(_evidence(), "repository")
    assert len(out) < MAX_SECTION_CHARS * 1.1
    assert "_bounded_view" in out
    # small children are present in full, the large one is elided with its path
    assert "Python" in out
    assert "[elided list of 4000 items" in out
    assert "read 'repository.modules'" in out
    assert "pkg/mod_5.py" not in out


def test_an_oversized_list_returns_a_first_page_and_the_exact_next_path():
    evidence = _evidence()
    out = read(evidence, "repository.modules")
    assert len(out) <= MAX_SECTION_CHARS
    match = re.search(r"read 'repository.modules\[(\d+):(\d+)\]' for the next page", out)
    assert match, out[:300]
    assert "pkg/mod_0.py" in out
    assert f"of 4000 in 'repository.modules'" in out


def test_paging_through_a_large_list_loses_no_item():
    evidence = _evidence(1500)
    seen = []
    path = "repository.modules"
    for _ in range(200):
        out = read(evidence, path)
        decoded = toon.decode(out)
        page = decoded["items"] if isinstance(decoded, dict) else decoded
        seen.extend(item["path"] for item in page)
        match = re.search(r"read '(repository\.modules\[\d+:\d+\])' for the next page", out)
        if not match:
            break
        path = match.group(1)
    assert seen == [m["path"] for m in evidence["repository"]["modules"]]


def test_a_slice_that_fits_says_where_to_continue_and_the_last_slice_is_plain():
    evidence = _evidence()
    first = read(evidence, "repository.modules[0:3]")
    assert re.search(r"read 'repository\.modules\[3:\d+\]' for the next page", first)
    assert toon.decode(first)["items"] == evidence["repository"]["modules"][0:3]
    tail = read(evidence, "repository.modules[3990:]")
    assert toon.decode(tail)[-1]["path"] == "pkg/mod_3999.py"
    assert "_bounded_view" not in tail


def test_an_empty_or_out_of_range_slice_says_so_instead_of_failing():
    out = read(_evidence(), "repository.modules[9000:9010]")
    assert "is empty" in out and "4000 items" in out


def test_the_budget_is_a_parameter():
    evidence = _evidence(50)
    assert "_bounded_view" not in read(evidence, "repository.modules")
    assert "_bounded_view" in read(evidence, "repository.modules", max_chars=500)


def test_the_root_is_an_outline_too():
    out = read(_evidence(), "")
    assert "_bounded_view" in out
    assert len(out) < MAX_SECTION_CHARS * 1.1


def test_a_single_item_over_the_budget_is_bounded_not_returned_whole():
    big = {"path": "big.py", "symbols": [{"name": f"s{i}", "doc": "x" * 40} for i in range(2000)], "loc": 3}
    evidence = {"repository": {"modules": [big, {"path": "small.py"}]}}
    out = read(evidence, "repository.modules", max_chars=5_000)
    assert len(out) < 12_000
    assert "too large" in out
    assert "repository.modules[0].symbols" in out


def test_the_next_page_hint_is_sized_by_the_budget_not_by_a_tiny_slice():
    evidence = {"repository": {"modules": _modules(4000)}}
    out = read(evidence, "repository.modules[0:2]")
    end = int(re.search(r"modules\[2:(\d+)\]", out).group(1))
    assert end > 100


def test_slicing_a_non_list_says_so_instead_of_no_such_path():
    out = read(_evidence(10), "git.total_commits[0:2]")
    assert out is not None and "not a list" in out
    assert read(_evidence(10), "nope.nothing[0:2]") is None
