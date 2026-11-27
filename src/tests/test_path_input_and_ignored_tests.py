"""User-typed paths (Windows backslashes, absolute, mixed case) and tests hidden by `ignored_paths`.

Evidence paths are always repo-relative and forward-slash. These tests pin that every command taking a file or a
directory accepts the spellings a Windows shell produces, and that `tests-for` / `dependents` say so when the repo's
own `.aletheore.json` keeps its tests out of the scan instead of answering "no tests".
"""

import ast
import json
from pathlib import Path

import pytest

from aletheore.cycles import cycles_report
from aletheore.dependents import DependentsError, dependents_report
from aletheore.scope_filter import ignored_test_patterns, normalize_under, posix_rel, resolve_target
from aletheore.tests_for import TestsForError
from aletheore.tests_for import tests_for_report as render_tests_for
from aletheore.usages import usages_report


def _ev(graph: dict[str, list[str]]) -> dict:
    return {
        "scanned_at": "2999-01-01T00:00:00+00:00",
        "repository": {"modules": [{"path": p, "imported_by": by, "language": "python"} for p, by in graph.items()]},
    }


def _config(repo: Path, ignored: list[str]) -> None:
    (repo / ".aletheore.json").write_text(json.dumps({"ignored_paths": ignored}), encoding="utf-8")


GRAPH = {"src/pkg/query.py": ["src/pkg/cli.py"], "src/pkg/cli.py": [], "src/pkg/Other.py": []}


# ---- posix_rel / normalize_under -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "typed, expected",
    [
        ("src\\pkg\\query.py", "src/pkg/query.py"),
        (".\\src\\pkg\\query.py", "src/pkg/query.py"),
        ("./src/pkg/query.py", "src/pkg/query.py"),
        ("././src/pkg/query.py", "src/pkg/query.py"),
        ("src//pkg///query.py", "src/pkg/query.py"),
        ("  src/pkg/query.py  ", "src/pkg/query.py"),
        ("src\\pkg/query.py", "src/pkg/query.py"),
        ("", ""),
    ],
)
def test_posix_rel(typed, expected):
    assert posix_rel(typed) == expected


@pytest.mark.parametrize(
    "under, expected",
    [
        (["src\\pkg"], ["src/pkg/"]),
        (["src\\pkg\\"], ["src/pkg/"]),
        ([".\\src"], ["src/"]),
        (["."], []),
        ([""], []),
        (None, []),
    ],
)
def test_normalize_under_accepts_windows_spellings(under, expected):
    assert normalize_under(under) == expected


# ---- resolve_target ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "typed",
    ["src/pkg/query.py", "./src/pkg/query.py", "src\\pkg\\query.py", ".\\src\\pkg\\query.py", "SRC\\PKG\\QUERY.PY"],
)
def test_resolve_target_finds_the_file_however_it_is_typed(typed):
    assert resolve_target(typed, set(GRAPH)) == "src/pkg/query.py"


def test_resolve_target_exact_match_wins_over_case_insensitive_one():
    known = {"a/Foo.py", "a/foo.py"}
    assert resolve_target("a/foo.py", known) == "a/foo.py"
    assert resolve_target("a/Foo.py", known) == "a/Foo.py"


def test_resolve_target_never_guesses_between_case_variants():
    known = {"a/Foo.py", "a/foo.py"}
    assert resolve_target("A/FOO.PY", known) == "A/FOO.PY"


def test_resolve_target_unknown_returns_normalized_spelling_for_the_error_message():
    assert resolve_target("nope\\x.py", {"a.py"}) == "nope/x.py"


def test_resolve_target_absolute_path_inside_the_repo(tmp_path: Path):
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    f = tmp_path / "src" / "pkg" / "query.py"
    f.write_text("x = 1\n", encoding="utf-8")
    assert resolve_target(str(f), set(GRAPH), tmp_path) == "src/pkg/query.py"


def test_resolve_target_absolute_path_outside_the_repo_is_not_matched(tmp_path: Path):
    outside = tmp_path / "elsewhere" / "query.py"
    assert resolve_target(str(outside), set(GRAPH), tmp_path / "repo") != "src/pkg/query.py"


def test_resolve_target_windows_drive_path_does_not_crash_on_posix(tmp_path: Path):
    assert resolve_target("C:\\work\\repo\\src\\pkg\\query.py", set(GRAPH), tmp_path) == "C:/work/repo/src/pkg/query.py"


def test_resolve_target_directory_with_trailing_separator():
    known = {"src/pkg/query.py", "src/pkg"}
    assert resolve_target("src\\pkg\\", known) == "src/pkg"
    assert resolve_target("src/pkg/", known) == "src/pkg"


# ---- commands accept those spellings --------------------------------------------------------------------------------


def test_tests_for_accepts_a_windows_path(tmp_path: Path):
    graph = {"src/pkg/query.py": ["tests/test_query.py"], "tests/test_query.py": []}
    a = render_tests_for(_ev(graph), tmp_path, "src/pkg/query.py")
    b = render_tests_for(_ev(graph), tmp_path, "src\\pkg\\query.py")
    assert a == b and "tests/test_query.py" in b


def test_tests_for_unknown_file_still_errors_with_a_close_match(tmp_path: Path):
    with pytest.raises(TestsForError, match="closest"):
        render_tests_for(_ev(GRAPH), tmp_path, "src\\pkg\\quer.py")


def test_dependents_accepts_a_windows_file_and_directory(tmp_path: Path):
    ev = _ev(GRAPH)
    assert dependents_report(ev, tmp_path, "src\\pkg\\query.py") == dependents_report(ev, tmp_path, "src/pkg/query.py")
    assert dependents_report(ev, tmp_path, "src\\pkg") == dependents_report(ev, tmp_path, "src/pkg")
    assert dependents_report(ev, tmp_path, ".\\src\\pkg\\") == dependents_report(ev, tmp_path, "src/pkg")


def test_dependents_unknown_target_errors(tmp_path: Path):
    with pytest.raises(DependentsError):
        dependents_report(_ev(GRAPH), tmp_path, "src\\missing")


def test_dependents_under_filter_accepts_backslashes(tmp_path: Path):
    graph = {"a.py": ["src/pkg/x.py", "other/y.py"], "src/pkg/x.py": [], "other/y.py": []}
    text = dependents_report(_ev(graph), tmp_path, "a.py", under=["src\\pkg"])
    assert "src/pkg/x.py" in text and "other/y.py" not in text.split("Repo-wide")[0]


def test_cycles_under_filter_accepts_backslashes(tmp_path: Path):
    graph = {"src/a.py": ["src/b.py"], "src/b.py": ["src/a.py"], "z/c.py": ["z/d.py"], "z/d.py": ["z/c.py"]}
    ev = {
        "scanned_at": "2999-01-01T00:00:00+00:00",
        "repository": {"modules": [{"path": p, "imports": imp, "imported_by": [], "language": "python"} for p, imp in graph.items()]},
    }
    scoped = cycles_report(ev, tmp_path, under=["src\\"])
    assert scoped == cycles_report(ev, tmp_path, under=["src"])
    assert scoped.startswith("2 file(s) in the requested scope")  # only src/a.py and src/b.py, not z/*


def test_usages_under_argument_is_normalized(tmp_path: Path):
    # smoke: the shared normalizer is wired into usages (an unknown symbol must raise or report, never crash on '\\')
    ev = _ev(GRAPH)
    try:
        usages_report(ev, tmp_path, "nothing_here", under=["src\\pkg"])
    except Exception as exc:  # noqa: BLE001 - only the type of failure matters here
        assert "backslash" not in str(exc).lower()


# ---- ignored_paths hides tests ---------------------------------------------------------------------------------------


def test_tests_for_says_so_when_tests_are_ignored_and_nothing_was_found(tmp_path: Path):
    _config(tmp_path, ["src/tests"])
    text = render_tests_for(_ev({"src/pkg/query.py": []}), tmp_path, "src/pkg/query.py")
    assert text.startswith("No test file imports src/pkg/query.py")
    assert "does not mean it is untested" in text and "src/tests" in text and "ignored_paths" in text


def test_tests_for_ignored_note_present_in_short_and_positive_reports(tmp_path: Path):
    _config(tmp_path, ["src/tests"])
    graph = {"a.py": ["tests/test_a.py"], "tests/test_a.py": []}
    full = render_tests_for(_ev(graph), tmp_path, "a.py")
    short = render_tests_for(_ev(graph), tmp_path, "a.py", summary_only=True)
    assert "excluded by ignored_paths" in full and "excluded by ignored_paths" in short
    none_short = render_tests_for(_ev({"a.py": []}), tmp_path, "a.py", summary_only=True)
    assert "excluded by ignored_paths" in none_short


def test_tests_for_has_no_ignored_note_without_config_or_with_non_test_ignores(tmp_path: Path):
    plain = render_tests_for(_ev({"a.py": []}), tmp_path, "a.py")
    assert "ignored_paths" not in plain
    _config(tmp_path, ["vendor", "docs/plans", "dist/**"])
    assert "ignored_paths" not in render_tests_for(_ev({"a.py": []}), tmp_path, "a.py")


def test_dependents_notes_ignored_tests_unless_no_tests_was_asked(tmp_path: Path):
    _config(tmp_path, ["github-app/tests"])
    ev = _ev({"a.py": ["b.py"], "b.py": []})
    assert "excluded by ignored_paths" in dependents_report(ev, tmp_path, "a.py")
    assert "ignored_paths" not in dependents_report(ev, tmp_path, "a.py", no_tests=True)


@pytest.mark.parametrize(
    "ignored, expected",
    [
        (["src/tests"], ["src/tests"]),
        (["tests/"], ["tests/"]),
        (["tests"], ["tests"]),
        (["**/__tests__/**"], ["**/__tests__/**"]),
        (["**/*.test.ts"], ["**/*.test.ts"]),
        (["e2e"], ["e2e"]),
        (["vendor", "build", "docs/superpowers/plans", "node_modules"], []),
        (["contest", "latest", "attestation"], []),
        ([], []),
    ],
)
def test_ignored_test_patterns(tmp_path: Path, ignored, expected):
    _config(tmp_path, ignored)
    assert ignored_test_patterns(tmp_path) == expected


@pytest.mark.parametrize("text", ["{not json", "[]", '{"ignored_paths": "tests"}', '{"ignored_paths": [1, null]}', ""])
def test_ignored_test_patterns_survives_a_malformed_config(tmp_path: Path, text: str):
    (tmp_path / ".aletheore.json").write_text(text, encoding="utf-8")
    assert ignored_test_patterns(tmp_path) == []


def test_ignored_test_patterns_missing_repo_directory(tmp_path: Path):
    assert ignored_test_patterns(tmp_path / "does-not-exist") == []


# ---- Windows: text file I/O must not depend on the host code page --------------------------------------------------


def _bare_text_io_calls() -> list[str]:
    package = Path(__file__).resolve().parent.parent / "aletheore"
    found = []
    for source in sorted(package.rglob("*.py")):
        if {"skills", "static", "__pycache__"} & set(source.relative_to(package).parts):
            continue
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr not in ("read_text", "write_text"):
                continue
            positional = 1 if node.func.attr == "read_text" else 2  # encoding is the first / second positional
            if len(node.args) < positional and not any(k.arg == "encoding" for k in node.keywords):
                found.append(f"{source.relative_to(package)}:{node.lineno} .{node.func.attr}()")
    return found


def test_read_text_and_write_text_always_name_their_encoding():
    """On Windows the default is the legacy code page (cp1252), which mis-decodes or cannot encode UTF-8 text. Linux and
    macOS default to UTF-8, which is how this class of bug has slipped through before (see the pytest-windows CI job)."""
    assert _bare_text_io_calls() == []


def test_candidate_tests_works_without_a_repo_path_and_accepts_windows_spelling():
    from aletheore.tests_for import candidate_tests

    graph = {"src/pkg/query.py": ["tests/test_query.py"], "tests/test_query.py": []}
    assert candidate_tests(_ev(graph), "src/pkg/query.py") == (["tests/test_query.py"], [])
    assert candidate_tests(_ev(graph), "src\\pkg\\query.py") == (["tests/test_query.py"], [])
    assert candidate_tests(_ev(graph), "missing.py") == ([], [])


# ---- review follow-ups -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("quote", ["'", "‘", "’", "‚", "‛"])
def test_clipboard_powershell_path_escapes_every_quote_like_character(tmp_path: Path, quote: str):
    from aletheore.council.clipboard import read_clipboard_image

    seen: list[list[str]] = []

    def fake_run(argv, **kwargs):
        seen.append(argv)
        raise OSError("no powershell here")

    read_clipboard_image(tmp_path / f"O{quote}Brien", run=fake_run, platform="win32")
    script = seen[0][-1]
    assert f"O{quote}{quote}Brien" in script and f"O{quote}Brien" not in script.replace(f"O{quote}{quote}Brien", "")


def test_no_f_string_reuses_its_own_quote_inside_a_replacement_field():
    """Before Python 3.12 `f"{x.read_text(encoding="utf-8")}"` is a SyntaxError, and the package still supports 3.11."""
    import io
    import sys
    import tokenize

    if sys.version_info < (3, 12):
        pytest.skip("older parsers already reject it at import time")
    package = Path(__file__).resolve().parent.parent / "aletheore"
    offenders = []
    for source in sorted(package.rglob("*.py")):
        if {"skills", "static", "__pycache__"} & set(source.relative_to(package).parts):
            continue
        quotes: list[str] = []  # quote character of each f-string we are currently inside
        for tok in tokenize.generate_tokens(io.StringIO(source.read_text(encoding="utf-8")).readline):
            if tok.type == tokenize.FSTRING_START:
                body = tok.string.lstrip("rRbBfF")
                if quotes and body[:1] in quotes and body[:3] not in ('"""', "'''"):
                    offenders.append(f"{source.relative_to(package)}:{tok.start[0]}")
                quotes.append(body[:3] if body[:3] in ('"""', "'''") else body[:1])
            elif tok.type == tokenize.FSTRING_END:
                quotes.pop()
            elif tok.type == tokenize.STRING and quotes:
                body = tok.string.lstrip("rRbBuU")
                if body[:1] in quotes and quotes[-1] not in ('"""', "'''"):
                    offenders.append(f"{source.relative_to(package)}:{tok.start[0]}")
    assert offenders == []
