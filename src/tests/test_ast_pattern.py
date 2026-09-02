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

    results = search_ast_pattern(
        tmp_path,
        "python",
        "(function_definition name: (identifier) @name body: (block (try_statement))) @whole",
    )

    assert len(results) == 1
    match = results[0]
    assert match["file"] == "app.py"
    assert match["captures"]["name"][0]["text"] == "guarded"
    assert match["captures"]["whole"][0]["start_line"] == 4


def test_search_ast_pattern_returns_nothing_when_no_file_matches(tmp_path):
    (tmp_path / "app.py").write_text("def plain():\n    return 1\n")

    results = search_ast_pattern(
        tmp_path, "python", "(function_definition body: (block (try_statement))) @whole"
    )

    assert results == []


def test_search_ast_pattern_reports_line_numbers_one_indexed(tmp_path):
    (tmp_path / "app.py").write_text(
        "\n\ndef third_line():\n    pass\n"
    )

    results = search_ast_pattern(tmp_path, "python", "(function_definition) @f")

    assert results[0]["captures"]["f"][0]["start_line"] == 3


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

    results = search_ast_pattern(
        tmp_path,
        "rust",
        "(function_item name: (identifier) @name "
        "body: (block (expression_statement (match_expression))))",
    )

    assert len(results) == 1
    assert results[0]["captures"]["name"][0]["text"] == "guarded"


def test_search_ast_pattern_typescript_covers_both_ts_and_tsx_grammars(tmp_path):
    """Real regression risk: .ts and .tsx are different grammar objects
    (TS_LANGUAGE vs TSX_LANGUAGE) under the same "typescript" language
    name - a query compiled against only one would silently miss the
    other extension entirely."""
    (tmp_path / "plain.ts").write_text("function greet(): string {\n  return 'hi';\n}\n")
    (tmp_path / "component.tsx").write_text("function Greet(): string {\n  return 'hi';\n}\n")

    results = search_ast_pattern(
        tmp_path, "typescript", "(function_declaration name: (identifier) @name) @whole"
    )

    matched_files = {r["file"] for r in results}
    assert matched_files == {"plain.ts", "component.tsx"}


def test_search_ast_pattern_skips_a_file_over_the_size_cap(tmp_path, monkeypatch):
    import aletheore.ast_pattern as ast_pattern_module

    monkeypatch.setattr(ast_pattern_module, "MAX_SOURCE_FILE_BYTES", 10)
    (tmp_path / "app.py").write_text("def f():\n    pass\n")  # well over 10 bytes

    results = search_ast_pattern(tmp_path, "python", "(function_definition) @f")

    assert results == []
