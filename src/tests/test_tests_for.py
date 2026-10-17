from pathlib import Path

import pytest

from aletheore.tests_for import TestsForError
from aletheore.tests_for import tests_for_report as render_tests_for

# pytest's default collection prefix matches "tests_for_report" as if it were itself a test function once it's
# imported into this module's namespace - aliasing the import (not the production name) sidesteps that.


def _ev(graph: dict[str, list[str]], scanned_at: str = "2999-01-01T00:00:00+00:00") -> dict:
    return {
        "scanned_at": scanned_at,
        "repository": {"modules": [{"path": p, "imported_by": by} for p, by in graph.items()]},
    }


def test_direct_importer_that_is_a_test_file_is_found(tmp_path: Path):
    graph = {"a.py": ["tests/test_a.py"], "tests/test_a.py": []}
    text = render_tests_for(_ev(graph), tmp_path, "a.py")
    assert "1 test file(s) exercise a.py" in text and "tests/test_a.py" in text


def test_transitive_importer_through_another_module_is_found(tmp_path: Path):
    # tests/test_b.py -> b.py -> a.py ; a test on b transitively covers a
    graph = {"a.py": ["b.py"], "b.py": ["tests/test_b.py"], "tests/test_b.py": []}
    text = render_tests_for(_ev(graph), tmp_path, "a.py")
    assert "tests/test_b.py" in text


def test_non_test_importer_is_not_counted(tmp_path: Path):
    graph = {"a.py": ["b.py"], "b.py": []}
    text = render_tests_for(_ev(graph), tmp_path, "a.py")
    assert "no test file imports a.py" in text


def test_naming_convention_match_without_any_import(tmp_path: Path):
    # test_a.py never imports a.py (e.g. it only uses a pytest fixture) but still matches by name.
    graph = {"a.py": [], "test_a.py": []}
    text = render_tests_for(_ev(graph), tmp_path, "a.py")
    assert "1 test file(s) exercise a.py (1 by naming convention only, no import found)" in text
    assert "test_a.py" in text


def test_js_style_naming_conventions(tmp_path: Path):
    graph = {"foo.js": [], "foo.test.js": [], "foo_test.js": [], "foo.spec.js": []}
    text = render_tests_for(_ev(graph), tmp_path, "foo.js")
    assert "3 test file(s) exercise foo.js" in text
    assert "foo.test.js" in text and "foo_test.js" in text and "foo.spec.js" in text


def test_unrelated_same_stem_in_a_different_directory_is_still_a_convention_match(tmp_path: Path):
    # Documented scope: naming convention matches by basename only, regardless of directory - a deliberate
    # trade for recall over precision (the import-graph signal is the precise one).
    graph = {"pkg/a.py": [], "other/test_a.py": []}
    text = render_tests_for(_ev(graph), tmp_path, "pkg/a.py")
    assert "other/test_a.py" in text


def test_unknown_target_suggests_closest(tmp_path: Path):
    graph = {"a.py": []}
    with pytest.raises(TestsForError, match="a.py"):
        render_tests_for(_ev(graph), tmp_path, "a.pyy")


def test_stale_scan_is_flagged(tmp_path: Path):
    (tmp_path / "a.py").write_text("x")
    ev = _ev({"a.py": ["tests/test_a.py"], "tests/test_a.py": []}, scanned_at="2000-01-01T00:00:00+00:00")
    assert "STALE" in render_tests_for(ev, tmp_path, "a.py")


def test_missing_scan_time_is_unknown_not_current(tmp_path: Path):
    ev = _ev({"a.py": ["tests/test_a.py"], "tests/test_a.py": []})
    del ev["scanned_at"]
    text = render_tests_for(ev, tmp_path, "a.py")
    assert "freshness unknown" in text


def test_dedupes_a_file_found_by_both_signals(tmp_path: Path):
    # tests/test_a.py both imports a.py AND matches the naming convention - must be listed once, and the
    # "naming convention only" count must not include it.
    graph = {"a.py": ["tests/test_a.py"], "tests/test_a.py": []}
    text = render_tests_for(_ev(graph), tmp_path, "a.py")
    assert text.count("tests/test_a.py") == 1
    assert "naming convention only" not in text


def test_cli_tests_for_end_to_end(tmp_path: Path):
    from unittest.mock import patch

    from typer.testing import CliRunner

    from aletheore.cli import app

    graph = {"a.py": ["tests/test_a.py"], "tests/test_a.py": []}
    with patch("aletheore.cli.load_evidence", return_value=_ev(graph)):
        ok = CliRunner().invoke(app, ["tests-for", "a.py", str(tmp_path)])
        bad = CliRunner().invoke(app, ["tests-for", "nope.py", str(tmp_path)])
    assert ok.exit_code == 0 and "1 test file(s) exercise a.py" in ok.output
    assert bad.exit_code == 1 and "error:" in bad.output
