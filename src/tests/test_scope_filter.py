import json
from unittest.mock import patch

from typer.testing import CliRunner

from aletheore.cli import app
from aletheore.scope_filter import SCOPABLE, in_scope, is_test_path, normalize_under, scope_result

DEAD = {
    "unreachable_modules": [{"path": "pkg/a.py", "reason": "r"}, {"path": "pkg/tests/t.py", "reason": "r"}, {"path": "other/b.py", "reason": "r"}],
    "entry_points_detected": ["pkg/__init__.py", "other/main.py"],
    "unused_dependencies": [{"ecosystem": "PyPI", "package": "x"}],
}
ERR = {
    "checked": True,
    "error_types": [{"name": "E1", "file": "pkg/e.py"}, {"name": "E2", "file": "other/e.py"}],
    "raise_sites": [{"file": "pkg/e.py", "line": 1}, {"file": "pkg/test_e.py", "line": 2}],
    "handlers": [{"file": "other/h.py", "line": 3}],
    "by_error_type": [{"name": "E1"}],
    "truncated": True,
}


def test_normalize_under_and_in_scope():
    assert normalize_under(["./pkg/", "a/b", ".", ""]) == ["pkg/", "a/b/"]
    assert in_scope("pkg/a.py", [], ["pkg/"], False) and not in_scope("other/a.py", [], ["pkg/"], False)
    assert not in_scope("pkg/locale/a.py", ["locale"], [], False) and in_scope("pkg/middleware/locale.py", ["locale"], [], False)
    assert not in_scope("pkg/test_a.py", [], [], True) and in_scope("pkg/a.py", [], [], True)


def test_is_test_path_conventions():
    for p in ("tests/a.py", "pkg/test_a.py", "pkg/a_test.go", "src/a.test.ts", "src/a.spec.js", "x/__tests__/a.js", "pkg/conftest.py"):
        assert is_test_path(p), p
    for p in ("pkg/a.py", "pkg/testing.py", "pkg/contest.go", "src/latest.ts"):
        assert not is_test_path(p), p


def test_scope_result_filters_every_listed_path_and_reports_what_it_left_out():
    out = scope_result("dead-code", DEAD, [], ["pkg"], True)
    assert [m["path"] for m in out["unreachable_modules"]] == ["pkg/a.py"]
    assert out["entry_points_detected"] == ["pkg/__init__.py"]
    assert out["unused_dependencies"] == DEAD["unused_dependencies"]  # not path based, left whole
    assert out["scope"]["left_out"] == {"unreachable_modules": 2, "entry_points_detected": 1}
    err = scope_result("error-handling", ERR, [], ["pkg"], True)
    assert [e["name"] for e in err["error_types"]] == ["E1"] and len(err["raise_sites"]) == 1 and err["handlers"] == []
    assert err["truncated"] is True and err["by_error_type"] == ERR["by_error_type"]
    assert ERR["error_types"][1]["file"] == "other/e.py"  # the input is not modified


def test_cli_query_scoping(tmp_path):
    with patch("aletheore.cli.load_evidence", return_value={}), patch.dict(
        "aletheore.cli.QUERY_FUNCTIONS", {"dead-code": (lambda ev, t: DEAD, False), "hotspots": (lambda ev, t: {}, False)}
    ):
        ok = CliRunner().invoke(app, ["query", "dead-code", "--path", str(tmp_path), "--under", "pkg", "--no-tests"])
        bad = CliRunner().invoke(app, ["query", "hotspots", "--path", str(tmp_path), "--under", "pkg"])
    assert ok.exit_code == 0 and [m["path"] for m in json.loads(ok.output)["unreachable_modules"]] == ["pkg/a.py"]
    assert bad.exit_code == 1 and "supported for" in bad.output and set(SCOPABLE) <= set(bad.output.replace(",", " ").split())
