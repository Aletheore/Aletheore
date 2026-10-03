import json
from unittest.mock import MagicMock, patch

from aletheore.static_analysis.bandit_scanner import (
    _relative_path,
    _sql_injection_is_plausible,
    check_bandit,
)


def _mock_run(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


def test_relative_path_normalizes_to_forward_slashes_even_on_windows(tmp_path, monkeypatch):
    # Real bug found on Windows CI in semgrep_scanner.py's identical helper,
    # audited into every scanner sharing the same str(Path(...)) pattern:
    # it renders with the OS's native separator - a backslash-joined path
    # on Windows - while every other path in this codebase's evidence uses
    # .as_posix(). Can't be reproduced by just running on this (POSIX)
    # machine - str(PosixPath(...)) already uses forward slashes here - so
    # this simulates Windows' real Path.relative_to() return shape
    # directly (a PureWindowsPath) rather than requiring an actual Windows
    # machine to prove the fix.
    from pathlib import Path, PureWindowsPath

    repo = tmp_path / "repo"
    repo.mkdir()
    raw_path = "./pkg/app.py"
    expected_target = (repo / raw_path).resolve()
    windows_relative = PureWindowsPath("pkg", "app.py")
    original_relative_to = Path.relative_to

    def patched_relative_to(self, other):
        if self == expected_target and other == repo.resolve():
            return windows_relative
        return original_relative_to(self, other)

    monkeypatch.setattr(Path, "relative_to", patched_relative_to)

    result = _relative_path(raw_path, repo)

    assert result == "pkg/app.py"
    assert "\\" not in result


def test_check_bandit_is_checked_true_with_no_findings_when_no_python_source(tmp_path):
    with patch("aletheore.static_analysis.bandit_scanner.shutil.which") as mock_which:
        result = check_bandit(tmp_path)

    mock_which.assert_not_called()
    assert result == {"checked": True, "reason": None, "findings": []}


def test_check_bandit_reports_not_installed(tmp_path):
    (tmp_path / "app.py").write_text("import os\n")

    with patch("aletheore.static_analysis.bandit_scanner.shutil.which", return_value=None):
        result = check_bandit(tmp_path)

    assert result == {"checked": False, "reason": "bandit not installed", "findings": []}


def test_check_bandit_normalizes_a_real_finding_shape(tmp_path):
    (tmp_path / "app.py").write_text("import subprocess\n")
    # Real shape confirmed live tonight.
    payload = {
        "results": [
            {
                "filename": "./app.py",
                "issue_severity": "HIGH",
                "issue_text": "subprocess call with shell=True identified, security issue.",
                "line_number": 4,
                "test_id": "B602",
            }
        ]
    }
    mock_result = _mock_run(1, stdout=json.dumps(payload))

    with patch("aletheore.static_analysis.bandit_scanner.shutil.which", return_value="/usr/bin/bandit"), \
         patch("aletheore.static_analysis.bandit_scanner.subprocess.run", return_value=mock_result):
        result = check_bandit(tmp_path)

    assert result["checked"] is True
    assert result["findings"] == [
        {
            "tool": "bandit",
            "rule_id": "B602",
            "severity": "critical",
            "type": "vulnerability",
            "path": "app.py",
            "line": 4,
            "message": "subprocess call with shell=True identified, security issue.",
        }
    ]


def test_check_bandit_treats_a_fatal_exit_code_as_a_real_failure(tmp_path):
    (tmp_path / "app.py").write_text("import os\n")
    mock_result = _mock_run(3, stdout="", stderr="fatal")

    with patch("aletheore.static_analysis.bandit_scanner.shutil.which", return_value="/usr/bin/bandit"), \
         patch("aletheore.static_analysis.bandit_scanner.subprocess.run", return_value=mock_result):
        result = check_bandit(tmp_path)

    assert result["checked"] is False


def test_check_bandit_filters_known_noisy_rules(tmp_path):
    # Real finding (2026-09-23): unfiltered against pallets/flask, B101
    # ("assert used") alone was 1,054 of 1,083 total findings (97%) -
    # fires on every bare `assert`, idiomatic in both pytest-style tests
    # and ordinary programmer sanity checks, not a real security signal.
    (tmp_path / "app.py").write_text("import subprocess\n")
    payload = {
        "results": [
            {
                "filename": "./app.py",
                "issue_severity": "LOW",
                "issue_text": "Use of assert detected.",
                "line_number": 1,
                "test_id": "B101",
            },
            {
                "filename": "./app.py",
                "issue_severity": "HIGH",
                "issue_text": "subprocess call with shell=True identified, security issue.",
                "line_number": 4,
                "test_id": "B602",
            },
        ]
    }
    mock_result = _mock_run(1, stdout=json.dumps(payload))

    with patch("aletheore.static_analysis.bandit_scanner.shutil.which", return_value="/usr/bin/bandit"), \
         patch("aletheore.static_analysis.bandit_scanner.subprocess.run", return_value=mock_result):
        result = check_bandit(tmp_path)

    assert len(result["findings"]) == 1
    assert result["findings"][0]["rule_id"] == "B602"


# Real false positive found live on PR #858 (Aletheore/Aletheore): Bandit's
# own B608 check flagged github-app/app_server/frontend.py's WIKI_HTML - a
# huge f-string building the AIRview page's HTML/JS, with zero SQL or
# database calls anywhere in that module - as "Possible SQL injection
# vector through string-based query construction." Root cause confirmed by
# running the real `bandit` binary against that file directly and tracing
# its own SIMPLE_SQL_RE (bandit/plugins/injection_sql.py): it pairs
# "update\s" with a later "set\s" using an UNBOUNDED `.*` under re.DOTALL,
# so it bridges thousands of characters of unrelated JS/HTML between them.
# The real match: the word "update" in a code comment ("a later
# incremental update broke, not the first build"), paired ~5,700
# characters later with the trailing whitespace after the JS identifier
# "nodeSet" (`const nodeSet = ...`) - "Set " satisfies `set\s`
# case-insensitively. This fixture reproduces that exact shape (confirmed
# against the real bandit binary to still trigger B608 unfiltered, before
# _sql_injection_is_plausible's extra check is applied) at a much smaller
# scale: a JS/HTML f-string with "update" and a "...Set " identifier
# separated by enough filler comment lines to exceed the 300-character
# bound _SQL_KEYWORD_PAIR_RE now requires between a keyword pair.
_WIKI_LIKE_FALSE_POSITIVE_SOURCE = (
    'FETCH_HELPERS = "x"\n'
    "\n"
    "\n"
    "def build(repo):\n"
    '    return "<div>" + f"""\n'
    "<script>\n"
    "{FETCH_HELPERS}\n"
    "// a later incremental update broke, not the first build\n"
    + "".join(
        f"// filler comment line {i} just to add distance between the two words\n"
        for i in range(40)
    )
    + "const nodeSet = new Set();\n"
    "</script>\n"
    '"""\n'
)


def test_sql_injection_is_plausible_rejects_html_js_fstring_with_distant_keywords():
    # The flagged JoinedStr starts at line 5 (the `f"""` line) - matches
    # what a real bandit run reports for this exact fixture.
    assert _sql_injection_is_plausible(_WIKI_LIKE_FALSE_POSITIVE_SOURCE, 5) is False


def test_sql_injection_is_plausible_accepts_percent_format_query_string():
    source = (
        "def get_user(cursor, user_id):\n"
        "    query = \"SELECT * FROM users WHERE id = '%s'\" % user_id\n"
        "    cursor.execute(query)\n"
    )
    assert _sql_injection_is_plausible(source, 2) is True


def test_sql_injection_is_plausible_accepts_concatenated_query_string():
    source = (
        "def get_user(cursor, table, user_id):\n"
        '    query = "SELECT * FROM " + table + " WHERE id = " + user_id\n'
        "    cursor.execute(query)\n"
    )
    assert _sql_injection_is_plausible(source, 2) is True


def test_sql_injection_is_plausible_accepts_format_call_query_string():
    source = (
        "def get_user(cursor, user_id):\n"
        "    cursor.execute(\"SELECT * FROM users WHERE id = {}\".format(user_id))\n"
    )
    assert _sql_injection_is_plausible(source, 2) is True


def test_sql_injection_is_plausible_accepts_a_wide_insert_with_many_columns():
    # Real false-negative found live (backward-audit, 2026-10-03): a wide
    # INSERT against a many-column table puts more than 300 characters
    # between INSERT INTO and VALUES, which the old bound silently dropped
    # even though this is a real, unambiguous SQL statement built with
    # `%`-interpolation.
    columns = ", ".join(f"column_number_{i}" for i in range(30))
    placeholders = ", ".join("%s" for _ in range(30))
    source = (
        "def insert_row(cursor, values):\n"
        f'    query = "INSERT INTO wide_table ({columns}) VALUES ({placeholders})"\n'
        "    cursor.execute(query % values)\n"
    )
    assert _sql_injection_is_plausible(source, 2) is True


def test_sql_injection_is_plausible_accepts_fstring_with_no_literal_keywords_when_executed():
    # No literal SQL keyword pair sits in the string's own text at all (the
    # whole "FROM users" clause is split around the interpolation), so this
    # only qualifies via the direct cursor.execute(...) wrapping - the "or
    # being passed to a known DB-execute call" half of the fix.
    source = (
        "def get_user(cursor, table, user_id):\n"
        '    return cursor.execute(f"SELECT * FROM {table} WHERE id = {user_id}")\n'
    )
    assert _sql_injection_is_plausible(source, 2) is True


def test_check_bandit_drops_b608_false_positive_shaped_like_wiki_html(tmp_path):
    (tmp_path / "frontend.py").write_text(_WIKI_LIKE_FALSE_POSITIVE_SOURCE)
    payload = {
        "results": [
            {
                "filename": "./frontend.py",
                "issue_severity": "MEDIUM",
                "issue_text": "Possible SQL injection vector through string-based query construction.",
                "line_number": 5,
                "test_id": "B608",
            },
        ]
    }
    mock_result = _mock_run(1, stdout=json.dumps(payload))

    with patch("aletheore.static_analysis.bandit_scanner.shutil.which", return_value="/usr/bin/bandit"), \
         patch("aletheore.static_analysis.bandit_scanner.subprocess.run", return_value=mock_result):
        result = check_bandit(tmp_path)

    assert result["findings"] == []


def test_check_bandit_keeps_a_real_b608_true_positive(tmp_path):
    (tmp_path / "vuln.py").write_text(
        "def get_user(cursor, user_id):\n"
        "    query = \"SELECT * FROM users WHERE id = '%s'\" % user_id\n"
        "    cursor.execute(query)\n"
    )
    payload = {
        "results": [
            {
                "filename": "./vuln.py",
                "issue_severity": "MEDIUM",
                "issue_text": "Possible SQL injection vector through string-based query construction.",
                "line_number": 2,
                "test_id": "B608",
            },
        ]
    }
    mock_result = _mock_run(1, stdout=json.dumps(payload))

    with patch("aletheore.static_analysis.bandit_scanner.shutil.which", return_value="/usr/bin/bandit"), \
         patch("aletheore.static_analysis.bandit_scanner.subprocess.run", return_value=mock_result):
        result = check_bandit(tmp_path)

    assert len(result["findings"]) == 1
    assert result["findings"][0]["rule_id"] == "B608"
    assert result["findings"][0]["line"] == 2
