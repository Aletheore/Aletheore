from pathlib import Path

from aletheore.cycles import cycles_report


def _ev(graph: dict[str, list[str]], scanned_at: str = "2999-01-01T00:00:00+00:00") -> dict:
    return {
        "scanned_at": scanned_at,
        "repository": {"modules": [{"path": p, "imports": imps} for p, imps in graph.items()]},
    }


def test_no_cycle_in_a_simple_chain(tmp_path: Path):
    graph = {"a.py": ["b.py"], "b.py": ["c.py"], "c.py": []}
    text = cycles_report(_ev(graph), tmp_path)
    assert "no circular import dependency found" in text


def test_a_three_file_cycle_is_found_with_an_example_chain(tmp_path: Path):
    graph = {"a.py": ["b.py"], "b.py": ["c.py"], "c.py": ["a.py"]}
    text = cycles_report(_ev(graph), tmp_path)
    assert "1 circular dependency group(s) found" in text
    assert "3 file(s): a.py, b.py, c.py" in text
    assert "example cycle: a.py -> b.py -> c.py -> a.py" in text


def test_a_direct_two_file_cycle(tmp_path: Path):
    graph = {"a.py": ["b.py"], "b.py": ["a.py"]}
    text = cycles_report(_ev(graph), tmp_path)
    assert "example cycle: a.py -> b.py -> a.py" in text


def test_two_independent_cycles_are_both_found(tmp_path: Path):
    graph = {"a.py": ["b.py"], "b.py": ["a.py"], "c.py": ["d.py"], "d.py": ["c.py"], "e.py": []}
    text = cycles_report(_ev(graph), tmp_path)
    assert "2 circular dependency group(s) found" in text
    assert "a.py, b.py" in text and "c.py, d.py" in text


def test_an_edge_into_a_cycle_from_outside_does_not_join_the_group(tmp_path: Path):
    # e.py imports into the cycle but is not itself part of it (not reachable back from a/b/c).
    graph = {"a.py": ["b.py"], "b.py": ["c.py"], "c.py": ["a.py"], "e.py": ["a.py"]}
    text = cycles_report(_ev(graph), tmp_path)
    assert "1 circular dependency group(s) found" in text
    assert "e.py" not in text.split("Coverage:")[0]


def test_a_longer_cycle_picks_the_shortest_concrete_chain(tmp_path: Path):
    # a<->b are mutually reachable directly; both also sit on a longer loop through c, d. The shortest concrete
    # cycle through the group's first member should use the direct edge, not the long way around.
    graph = {"a.py": ["b.py"], "b.py": ["a.py", "c.py"], "c.py": ["d.py"], "d.py": ["a.py"]}
    text = cycles_report(_ev(graph), tmp_path)
    assert "example cycle: a.py -> b.py -> a.py" in text


def test_self_import_is_not_a_cycle(tmp_path: Path):
    graph = {"a.py": ["a.py"]}
    text = cycles_report(_ev(graph), tmp_path)
    assert "no circular import dependency found" in text


def test_stale_scan_is_flagged(tmp_path: Path):
    (tmp_path / "a.py").write_text("x")
    graph = {"a.py": ["b.py"], "b.py": ["a.py"]}
    ev = _ev(graph, scanned_at="2000-01-01T00:00:00+00:00")
    assert "STALE" in cycles_report(ev, tmp_path)


def test_missing_scan_time_is_unknown_not_current(tmp_path: Path):
    graph = {"a.py": ["b.py"], "b.py": ["a.py"]}
    ev = _ev(graph)
    del ev["scanned_at"]
    assert "freshness unknown" in cycles_report(ev, tmp_path)


def test_cli_cycles_end_to_end(tmp_path: Path):
    from unittest.mock import patch

    from typer.testing import CliRunner

    from aletheore.cli import app

    graph = {"a.py": ["b.py"], "b.py": ["a.py"]}
    with patch("aletheore.cli.load_evidence", return_value=_ev(graph)):
        result = CliRunner().invoke(app, ["cycles", str(tmp_path)])
    assert result.exit_code == 0 and "1 circular dependency group(s) found" in result.output
