import os

from typer.testing import CliRunner

from aletheore.cli import app
from aletheore.code_locator import locate_report
from tests.test_mcp_server import make_repo_with_evidence

runner = CliRunner()


def _repo_with_foo(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    (repo / "a.py").write_text("def foo(): return 1\n")
    os.utime(repo / "a.py", (1577836800, 1577836800))  # 2020, before the evidence's scanned_at
    return repo


def test_locate_command_prints_the_header_and_raw_source(tmp_path):
    repo = _repo_with_foo(tmp_path)

    result = runner.invoke(app, ["locate", "foo", str(repo)])

    assert result.exit_code == 0
    assert result.output == "a.py::foo L1-1\ndef foo(): return 1\n"


def test_locate_command_says_when_nothing_matches_and_exits_nonzero_like_usages(tmp_path):
    repo = _repo_with_foo(tmp_path)

    result = runner.invoke(app, ["locate", "no_such_identifier_anywhere", str(repo)])

    assert result.exit_code == 1 and "no match" in result.output and "grep" in result.output


def test_locate_command_without_evidence_tells_the_user_to_scan(tmp_path):
    result = runner.invoke(app, ["locate", "foo", str(tmp_path)])

    assert result.exit_code == 1 and "aletheore scan" in result.output


def test_locate_report_warns_when_a_file_changed_after_the_scan(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    (repo / "a.py").write_text("def foo(): return 2\n")
    os.utime(repo / "a.py", (4102444800, 4102444800))  # year 2100

    import json

    evidence = json.loads((repo / ".aletheore" / "air.json").read_text())
    text = locate_report(evidence, repo, "foo")

    assert text.startswith("note: a.py changed since the last scan") and "a.py::foo L1-1" in text
