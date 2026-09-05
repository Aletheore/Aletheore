import pytest

from aletheore.ast_pattern import (
    InvalidPatternError,
    UnknownLanguageError,
    search_ast_pattern,
)


def test_search_ast_pattern_matches_a_function_with_a_try_statement(tmp_path):
    (tmp_path / "app.py").write_text(
        "def plain():\n"
        "    return 1\n"
        "\n"
        "def guarded():\n"
        "    try:\n"
        "        return risky()\n"
        "    except ValueError:\n"
        "        return None\n"
    )

    result = search_ast_pattern(
        tmp_path,
        "python",
        "(function_definition name: (identifier) @name body: (block (try_statement))) @whole",
    )

    assert result["truncated"] is False
    assert len(result["matches"]) == 1
    match = result["matches"][0]
    assert match["file"] == "app.py"
    assert match["captures"]["name"][0]["text"] == "guarded"
    assert match["captures"]["whole"][0]["start_line"] == 4


def test_search_ast_pattern_returns_nothing_when_no_file_matches(tmp_path):
    (tmp_path / "app.py").write_text("def plain():\n    return 1\n")

    result = search_ast_pattern(
        tmp_path, "python", "(function_definition body: (block (try_statement))) @whole"
    )

    assert result["matches"] == []
    assert result["truncated"] is False


def test_search_ast_pattern_reports_line_numbers_one_indexed(tmp_path):
    (tmp_path / "app.py").write_text(
        "\n\ndef third_line():\n    pass\n"
    )

    result = search_ast_pattern(tmp_path, "python", "(function_definition) @f")

    assert result["matches"][0]["captures"]["f"][0]["start_line"] == 3


def test_search_ast_pattern_rejects_an_unknown_language(tmp_path):
    with pytest.raises(UnknownLanguageError):
        search_ast_pattern(tmp_path, "cobol", "(anything)")


def test_search_ast_pattern_rejects_a_query_that_fails_to_compile(tmp_path):
    (tmp_path / "app.py").write_text("def f():\n    pass\n")

    with pytest.raises(InvalidPatternError):
        search_ast_pattern(tmp_path, "python", "(this_node_type_does_not_exist)")


def test_search_ast_pattern_works_for_a_second_language_not_just_python(tmp_path):
    """Proves the mechanism is language-agnostic, not hardcoded to Python -
    same function, a Rust grammar and query instead."""
    (tmp_path / "main.rs").write_text(
        "fn plain() -> i32 { 1 }\n\n"
        "fn guarded() -> Result<i32, String> {\n"
        "    match risky() {\n"
        "        Ok(v) => Ok(v),\n"
        "        Err(e) => Err(e),\n"
        "    }\n"
        "}\n"
    )

    result = search_ast_pattern(
        tmp_path,
        "rust",
        "(function_item name: (identifier) @name "
        "body: (block (expression_statement (match_expression))))",
    )

    assert len(result["matches"]) == 1
    assert result["matches"][0]["captures"]["name"][0]["text"] == "guarded"


def test_search_ast_pattern_typescript_covers_both_ts_and_tsx_grammars(tmp_path):
    """Real regression risk: .ts and .tsx are different grammar objects
    (TS_LANGUAGE vs TSX_LANGUAGE) under the same "typescript" language
    name - a query compiled against only one would silently miss the
    other extension entirely."""
    (tmp_path / "plain.ts").write_text("function greet(): string {\n  return 'hi';\n}\n")
    (tmp_path / "component.tsx").write_text("function Greet(): string {\n  return 'hi';\n}\n")

    result = search_ast_pattern(
        tmp_path, "typescript", "(function_declaration name: (identifier) @name) @whole"
    )

    matched_files = {r["file"] for r in result["matches"]}
    assert matched_files == {"plain.ts", "component.tsx"}


def test_search_ast_pattern_skips_a_file_over_the_size_cap(tmp_path):
    # Real MAX_SOURCE_FILE_BYTES (2MB), not a monkeypatched constant - the
    # actual parse-and-query work now runs inside a subprocess (see
    # ast_pattern.py's module docstring), which does its own fresh import
    # and never sees a patch applied to this test's own process.
    from aletheore.scanner.graph import MAX_SOURCE_FILE_BYTES

    (tmp_path / "app.py").write_text(
        "def f():\n    pass\n" + ("# padding\n" * (MAX_SOURCE_FILE_BYTES // 10))
    )

    result = search_ast_pattern(tmp_path, "python", "(function_definition) @f")

    assert result["matches"] == []


def test_search_ast_pattern_skips_an_unreadable_file_without_losing_other_results(tmp_path):
    """Real regression: an unhandled OSError on one file used to crash the
    whole call, losing every other file's real matches too. A genuine
    chmod-000 file, not a monkeypatched _read_and_parse - the parse-and-
    query work now runs inside a subprocess (see ast_pattern.py's module
    docstring), which does its own fresh import and never sees a patch
    applied to this test's own process; real filesystem permissions do
    propagate correctly."""
    (tmp_path / "good.py").write_text("def real_match():\n    pass\n")
    bad = tmp_path / "bad.py"
    bad.write_text("def also_matches():\n    pass\n")
    bad.chmod(0)
    try:
        result = search_ast_pattern(tmp_path, "python", "(function_definition) @f")
    finally:
        bad.chmod(0o644)  # tmp_path cleanup needs this back or it can't delete the file

    matched_files = {m["file"] for m in result["matches"]}
    assert matched_files == {"good.py"}
    assert result["truncated"] is False


def test_search_ast_pattern_truncates_past_the_match_cap(tmp_path, monkeypatch):
    import aletheore.ast_pattern as ast_pattern_module

    monkeypatch.setattr(ast_pattern_module, "_AST_PATTERN_MATCH_CAP", 3)
    source = "\n\n".join(f"def f{i}():\n    pass" for i in range(10))
    (tmp_path / "app.py").write_text(source)

    result = search_ast_pattern(tmp_path, "python", "(function_definition) @f")

    assert len(result["matches"]) == 3
    assert result["truncated"] is True


def test_search_ast_pattern_truncates_past_the_char_budget(tmp_path, monkeypatch):
    import aletheore.ast_pattern as ast_pattern_module

    monkeypatch.setattr(ast_pattern_module, "_AST_PATTERN_TOTAL_CHAR_BUDGET", 20)
    source = "\n\n".join(f"def f{i}():\n    pass" for i in range(10))
    (tmp_path / "app.py").write_text(source)

    result = search_ast_pattern(tmp_path, "python", "(function_definition) @f")

    assert len(result["matches"]) < 10
    assert result["truncated"] is True


def test_search_ast_pattern_drops_a_single_match_that_alone_exceeds_the_char_budget(
    tmp_path, monkeypatch
):
    """Real Flash Review finding on this PR: checking only whether
    total_chars had ALREADY reached the budget let one oversized match
    through in full regardless of size, pushing the real total arbitrarily
    far past _AST_PATTERN_TOTAL_CHAR_BUDGET. A match whose own captures
    alone exceed the budget must be dropped entirely, not appended and
    then merely flagged."""
    import aletheore.ast_pattern as ast_pattern_module

    monkeypatch.setattr(ast_pattern_module, "_AST_PATTERN_TOTAL_CHAR_BUDGET", 20)
    (tmp_path / "app.py").write_text(
        "def small():\n    pass\n\n"
        "def big():\n    " + "x = 1\n    " * 20 + "\n"
    )

    result = search_ast_pattern(tmp_path, "python", "(function_definition) @f")

    total_chars = sum(
        len(capture["text"])
        for match in result["matches"]
        for capture_list in match["captures"].values()
        for capture in capture_list
    )
    assert total_chars <= 20
    assert result["truncated"] is True
