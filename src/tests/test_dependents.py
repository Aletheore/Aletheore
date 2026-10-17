from pathlib import Path

import pytest

from aletheore.dependents import DependentsError, dependents_of, dependents_report


def _ev(graph: dict[str, list[str]], unparseable=0):
    return {
        "scanned_at": "2999-01-01T00:00:00+00:00",
        "repository": {
            "modules": [{"path": p, "language": "python", "imported_by": by} for p, by in graph.items()],
            "unparseable_files": ["x"] * unparseable,
        },
    }


# a <- b <- c <- d ; a <- e ; duplicate entries and a cycle back to a
GRAPH = {"a.py": ["b.py", "b.py", "e.py", "a.py"], "b.py": ["c.py"], "c.py": ["d.py", "a.py"], "d.py": [], "e.py": []}


def test_transitive_deduplicated_sorted_excludes_target():
    allp, direct = dependents_of(_ev(GRAPH), "a.py")
    assert allp == ["b.py", "c.py", "d.py", "e.py"]
    assert direct == {"b.py", "e.py"}


def test_report_lists_files_and_states_coverage(tmp_path: Path):
    text = dependents_report(_ev(GRAPH, unparseable=2), tmp_path, "a.py")
    assert text.splitlines()[0].startswith("4 files import a.py")
    assert text.count("\n.py") == 0 and "b.py\nc.py\nd.py\ne.py" in text
    assert "2 direct importers plus 2" in text and "2 unparseable" in text and "no listed file changed" in text
    assert "importlib" in text


def test_exclude_filters_and_counts(tmp_path: Path):
    text = dependents_report(_ev(GRAPH), tmp_path, "a.py", ["d.py"])
    assert "3 files import a.py" in text and "1 more left out" in text and "d.py\n" not in text.split("\n\n")[0]


def test_unknown_target_suggests_closest(tmp_path: Path):
    with pytest.raises(DependentsError, match="b.py"):
        dependents_report(_ev(GRAPH), tmp_path, "b.pyy")


def test_stale_scan_is_flagged(tmp_path: Path):
    (tmp_path / "a.py").write_text("x")
    ev = _ev(GRAPH)
    ev["scanned_at"] = "2000-01-01T00:00:00+00:00"
    assert "STALE" in dependents_report(ev, tmp_path, "a.py")


def test_exclude_matches_whole_names_only(tmp_path: Path):
    g = {"a.py": ["pkg/locale.py", "locale/x.py"], "pkg/locale.py": [], "locale/x.py": []}
    text = dependents_report(_ev(g), tmp_path, "a.py", ["locale"])
    assert "pkg/locale.py" in text and "locale/x.py" not in text.split("\n\n")[0]


def test_imports_and_imported_by_list_each_file_once():
    from aletheore.query import find_imported_by, find_imports

    ev = {"repository": {"modules": [{"path": "a.py", "imports": ["b.py", "b.py", "c.py"], "imported_by": ["x.py", "x.py", "x.py"]}]}}
    assert find_imports(ev, "a.py") == ["b.py", "c.py"]
    assert find_imported_by(ev, "a.py") == ["x.py"]


def test_under_limits_listing_to_a_directory(tmp_path: Path):
    g = {"pkg/a.py": ["pkg/b.py", "bench/x.py"], "pkg/b.py": [], "bench/x.py": []}
    text = dependents_report(_ev(g), tmp_path, "pkg/a.py", under=["pkg"])
    assert "1 files import pkg/a.py" in text and "bench/x.py" not in text.split("\n\n")[0] and "1 more left out" in text


def test_cli_dependents_end_to_end(tmp_path: Path):
    from unittest.mock import patch

    from typer.testing import CliRunner

    from aletheore.cli import app

    with patch("aletheore.cli.load_evidence", return_value=_ev(GRAPH)):
        ok = CliRunner().invoke(app, ["dependents", "a.py", str(tmp_path), "--exclude", "d.py"])
        bad = CliRunner().invoke(app, ["dependents", "nope.py", str(tmp_path)])
    assert ok.exit_code == 0 and "3 files import a.py" in ok.output
    assert bad.exit_code == 1 and "error:" in bad.output


def test_under_accepts_dot_slash_and_dot(tmp_path: Path):
    g = {"pkg/a.py": ["pkg/b.py", "bench/x.py"], "pkg/b.py": [], "bench/x.py": []}
    assert "1 files import pkg/a.py" in dependents_report(_ev(g), tmp_path, "pkg/a.py", under=["./pkg/"])
    assert "2 files import pkg/a.py" in dependents_report(_ev(g), tmp_path, "pkg/a.py", under=["."])


def test_missing_scan_time_is_reported_as_unknown_not_current(tmp_path: Path):
    ev = _ev(GRAPH)
    del ev["scanned_at"]
    text = dependents_report(ev, tmp_path, "a.py")
    assert "freshness unknown" in text and "no listed file changed" not in text


def test_changed_target_is_named_as_such(tmp_path: Path):
    (tmp_path / "a.py").write_text("x")
    ev = _ev(GRAPH)
    ev["scanned_at"] = "2000-01-01T00:00:00+00:00"
    assert "the file asked about and" in dependents_report(ev, tmp_path, "a.py")


def test_is_test_path_covers_common_conventions():
    from aletheore.dependents import is_test_path

    for p in ("tests/a.py", "pkg/test_a.py", "pkg/a_test.go", "src/a.test.ts", "src/a.spec.js", "x/__tests__/a.js",
              "pkg/conftest.py", "src/FooTest.java", "spec/a_spec.rb"):
        assert is_test_path(p), p
    for p in ("pkg/a.py", "pkg/testing.py", "pkg/contest.go", "src/latest.ts", "src/attest.js"):
        assert not is_test_path(p), p


def test_no_tests_drops_test_files_and_counts_them(tmp_path: Path):
    g = {"pkg/a.go": ["pkg/b.go", "pkg/b_test.go"], "pkg/b.go": [], "pkg/b_test.go": []}
    text = dependents_report(_ev(g), tmp_path, "pkg/a.go", no_tests=True)
    assert "1 files import pkg/a.go" in text and "b_test.go" not in text.split("\n\n")[0] and "1 more left out" in text


def _go_ev():
    # pkg/lib has two files; app imports one of them, cmd imports app, a test file in tools imports lib
    mods = {
        "pkg/lib/a.go": ["app/m.go", "tools/x_test.go"],
        "pkg/lib/b.go": ["app/m.go", "tools/x_test.go"],
        "app/m.go": ["cmd/main.go"],
        "cmd/main.go": [],
        "tools/x_test.go": [],
    }
    return {
        "scanned_at": "2999-01-01T00:00:00+00:00",
        "repository": {"modules": [{"path": p, "language": "go", "imported_by": by} for p, by in mods.items()], "unparseable_files": []},
    }


def test_directory_target_is_a_package_and_packages_flag_collapses_to_directories(tmp_path: Path):
    text = dependents_report(_go_ev(), tmp_path, "pkg/lib", no_tests=True, packages=True)
    head = text.split("\n\n")[0].splitlines()
    assert head[0].startswith("2 packages (directories) import pkg/lib") and head[1:] == ["app", "cmd"]
    assert "this package (2 files)" in text


def test_directory_target_without_flags_lists_files_and_unknown_directory_errors(tmp_path: Path):
    text = dependents_report(_go_ev(), tmp_path, "pkg/lib", no_tests=True)
    assert "app/m.go" in text and "cmd/main.go" in text and "pkg/lib/a.go" not in text.split("\n\n")[0]
    with pytest.raises(DependentsError, match="not a module or a directory"):
        dependents_report(_go_ev(), tmp_path, "pkg/nope")
