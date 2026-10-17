import subprocess
from pathlib import Path

import pytest

from aletheore.usages import UsagesError, usages_report


def _ev(modules: dict[str, dict], scanned_at: str = "2999-01-01T00:00:00+00:00") -> dict:
    return {
        "scanned_at": scanned_at,
        "repository": {
            "modules": [
                {"path": path, "language": "python", "symbols": syms} for path, syms in modules.items()
            ],
        },
    }


def _git_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for rel, content in files.items():
        full = tmp_path / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t.com", "-c", "user.name=t", "commit", "-q", "-m", "x"], cwd=tmp_path, check=True)
    return tmp_path


def test_lists_definition_and_usages_with_source_lines(tmp_path: Path):
    repo = _git_repo(
        tmp_path,
        {
            "a.py": "def parse_config():\n    return {}\n",
            "b.py": "from a import parse_config\n\ndef run():\n    return parse_config()\n",
        },
    )
    ev = _ev({"a.py": {"functions": [{"name": "parse_config", "start_line": 1, "end_line": 2}]}, "b.py": {}})
    text = usages_report(ev, repo, "parse_config")
    assert "Defined (1):" in text and "a.py:1 (function)" in text
    assert "Used 2 time(s)" in text
    assert "b.py:1: from a import parse_config" in text
    assert "b.py:4: return parse_config()" in text
    # the definition line in a.py itself must not be double-counted as a usage
    assert "a.py:1:" not in text.split("Used")[1]


def test_unknown_symbol_suggests_closest(tmp_path: Path):
    ev = _ev({"a.py": {"functions": [{"name": "parse_config", "start_line": 1, "end_line": 2}]}})
    with pytest.raises(UsagesError, match="parse_config"):
        usages_report(ev, tmp_path, "parse_confg")


def test_exclude_and_under_filter_and_count(tmp_path: Path):
    repo = _git_repo(
        tmp_path,
        {
            "a.py": "def f():\n    pass\n",
            "tests/test_a.py": "from a import f\nf()\n",
            "pkg/b.py": "from a import f\nf()\n",
        },
    )
    ev = _ev({"a.py": {"functions": [{"name": "f", "start_line": 1, "end_line": 2}]}, "tests/test_a.py": {}, "pkg/b.py": {}})
    text = usages_report(ev, repo, "f", exclude=["tests"])
    assert "tests/test_a.py" not in text.split("Coverage:")[0]
    assert "pkg/b.py" in text
    assert "left out by --exclude/--under" in text

    text_under = usages_report(ev, repo, "f", under=["pkg"])
    assert "tests/test_a.py" not in text_under.split("Coverage:")[0]
    assert "pkg/b.py:2" in text_under


def test_multiple_definitions_with_the_same_name_are_all_listed(tmp_path: Path):
    repo = _git_repo(
        tmp_path,
        {"a.py": "def f():\n    pass\n", "pkg/b.py": "def f():\n    pass\n", "c.py": "from a import f\nf()\n"},
    )
    ev = _ev(
        {
            "a.py": {"functions": [{"name": "f", "start_line": 1, "end_line": 2}]},
            "pkg/b.py": {"functions": [{"name": "f", "start_line": 1, "end_line": 2}]},
            "c.py": {},
        }
    )
    text = usages_report(ev, repo, "f")
    assert "Defined (2):" in text and "a.py:1 (function)" in text and "pkg/b.py:1 (function)" in text


def test_stale_scan_is_flagged(tmp_path: Path):
    repo = _git_repo(tmp_path, {"a.py": "def f():\n    pass\n"})
    ev = _ev({"a.py": {"functions": [{"name": "f", "start_line": 1, "end_line": 2}]}}, scanned_at="2000-01-01T00:00:00+00:00")
    assert "STALE" in usages_report(ev, repo, "f")


def test_missing_scan_time_is_unknown_not_current(tmp_path: Path):
    repo = _git_repo(tmp_path, {"a.py": "def f():\n    pass\n"})
    ev = _ev({"a.py": {"functions": [{"name": "f", "start_line": 1, "end_line": 2}]}})
    del ev["scanned_at"]
    text = usages_report(ev, repo, "f")
    assert "freshness unknown" in text


def test_falls_back_to_plain_scan_without_git(tmp_path: Path):
    (tmp_path / "a.py").write_text("def f():\n    pass\n")
    (tmp_path / "b.py").write_text("from a import f\nf()\n")
    ev = _ev({"a.py": {"functions": [{"name": "f", "start_line": 1, "end_line": 2}]}, "b.py": {}})
    text = usages_report(ev, tmp_path, "f")
    assert "not a git checkout" in text
    assert "b.py:2: f()" in text


def test_cli_usages_end_to_end(tmp_path: Path):
    from unittest.mock import patch

    from typer.testing import CliRunner

    from aletheore.cli import app

    repo = _git_repo(tmp_path, {"a.py": "def f():\n    pass\n", "b.py": "from a import f\nf()\n"})
    ev = _ev({"a.py": {"functions": [{"name": "f", "start_line": 1, "end_line": 2}]}, "b.py": {}})
    with patch("aletheore.cli.load_evidence", return_value=ev):
        ok = CliRunner().invoke(app, ["usages", "f", str(repo)])
        bad = CliRunner().invoke(app, ["usages", "nope", str(repo)])
    assert ok.exit_code == 0 and "Used 2 time(s)" in ok.output
    assert bad.exit_code == 1 and "error:" in bad.output


def test_recursive_call_inside_the_definition_is_not_counted_as_a_usage(tmp_path: Path):
    # Flash Review finding: only the definition's start_line was excluded, so a recursive call or a self-reference
    # anywhere else in the body was double-counted as a "usage outside its own definition".
    repo = _git_repo(
        tmp_path,
        {
            "a.py": "def fact(n):\n    if n <= 1:\n        return 1\n    return n * fact(n - 1)\n",
            "b.py": "from a import fact\nfact(5)\n",
        },
    )
    ev = _ev({"a.py": {"functions": [{"name": "fact", "start_line": 1, "end_line": 4}]}, "b.py": {}})
    text = usages_report(ev, repo, "fact")
    assert "Used 2 time(s)" in text  # only b.py's import line and call, not the recursive call in a.py
    assert "a.py:4" not in text.split("Coverage:")[0]


@pytest.mark.parametrize("sep", ["\0", ":"])
def test_git_grep_parsing_accepts_either_line_text_delimiter(sep, tmp_path: Path, monkeypatch):
    # git versions differ on whether -z NULs only the path separator or the line/text separator too - pin both,
    # since this exact ambiguity caused a real regression (Flash Review finding) in an earlier fix attempt.
    from unittest.mock import MagicMock

    from aletheore import usages

    fake = MagicMock(returncode=0, stdout=f"a.py\x001{sep}f()\n".encode())
    monkeypatch.setattr(usages.subprocess, "run", lambda *a, **k: fake)
    assert usages._git_grep_usages(tmp_path, "f") == [("a.py", 1, "f()")]


def test_git_grep_handles_a_colon_in_the_file_path(tmp_path: Path):
    # Flash Review finding: splitting git grep's output on ":" silently drops hits in a path containing one
    # (legal on POSIX filesystems) because the line-number field fails int() and the line is discarded.
    repo = _git_repo(tmp_path, {"a.py": "def f():\n    pass\n", "weird:name.py": "from a import f\nf()\n"})
    ev = _ev({"a.py": {"functions": [{"name": "f", "start_line": 1, "end_line": 2}]}, "weird:name.py": {}})
    text = usages_report(ev, repo, "f")
    assert "weird:name.py:2: f()" in text


def test_renamed_import_alias_is_an_honest_gap(tmp_path: Path):
    # Documented limitation: usages matches the literal name, so an alias import is invisible to it. This test
    # pins that known gap rather than silently letting it drift.
    repo = _git_repo(
        tmp_path,
        {"a.py": "def f():\n    pass\n", "b.py": "from a import f as g\ng()\n"},
    )
    ev = _ev({"a.py": {"functions": [{"name": "f", "start_line": 1, "end_line": 2}]}, "b.py": {}})
    text = usages_report(ev, repo, "f")
    assert "Used 1 time(s)" in text  # only the import line, "g()" is not found
    assert "renamed imports" in text
