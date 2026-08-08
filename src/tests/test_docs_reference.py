import pytest

from aletheore.docs_reference import UNDOCUMENTED, build_api_reference, build_module_reference


def _module(path: str, functions: list[dict], classes: list[dict] | None = None) -> dict:
    return {
        "path": path,
        "language": "python",
        "imports": [],
        "imported_by": [],
        "symbols": {"functions": functions, "classes": classes or []},
    }


def _symbol(name: str, **overrides) -> dict:
    base = {
        "name": name,
        "start_line": 3,
        "end_line": 5,
        "params": "()",
        "docstring": None,
        "return_type": None,
        "is_public": True,
    }
    base.update(overrides)
    return base


def test_build_module_reference_includes_public_symbol_with_docstring_and_citation():
    evidence = {"repository": {"modules": [_module(
        "src/greet.py",
        [_symbol("greet", params="(name: str)", docstring="Return a greeting.", return_type="str", start_line=3)],
    )]}}
    md = build_module_reference(evidence, "src/greet.py")
    assert "greet(name: str) -> str" in md
    assert "Return a greeting." in md
    assert "src/greet.py:3" in md


def test_build_module_reference_marks_missing_docstring_as_undocumented_not_invented():
    evidence = {"repository": {"modules": [_module("src/a.py", [_symbol("f")])]}}
    md = build_module_reference(evidence, "src/a.py")
    assert UNDOCUMENTED in md


def test_build_module_reference_dedents_a_multiline_docstring():
    # A real Python docstring's continuation lines carry the source's own
    # indentation - left as raw text, 4+ leading spaces read as a Markdown
    # code block instead of flowing prose (caught via a real dogfooding run
    # of `aletheore docs` against this repo's own src/aletheore/).
    docstring = "First line.\n    Second line, indented to match source.\n    Third line."
    evidence = {"repository": {"modules": [_module(
        "src/a.py", [_symbol("f", docstring=docstring)]
    )]}}
    md = build_module_reference(evidence, "src/a.py")
    assert "    Second line" not in md
    assert "Second line, indented to match source." in md


def test_build_module_reference_excludes_private_symbols():
    evidence = {"repository": {"modules": [_module(
        "src/a.py", [_symbol("_helper", is_public=False)]
    )]}}
    md = build_module_reference(evidence, "src/a.py")
    assert "_helper" not in md
    assert "No public symbols found" in md


def test_build_module_reference_raises_for_unknown_module():
    evidence = {"repository": {"modules": []}}
    with pytest.raises(ValueError, match="src/missing.py"):
        build_module_reference(evidence, "src/missing.py")


def test_build_module_reference_includes_public_classes():
    evidence = {"repository": {"modules": [_module(
        "src/a.py", [], classes=[_symbol("Widget", docstring="A widget.", params=None)],
    )]}}
    md = build_module_reference(evidence, "src/a.py")
    assert "Widget" in md
    assert "A widget." in md


def test_build_api_reference_returns_one_entry_per_module_with_at_least_one_public_symbol():
    evidence = {"repository": {"modules": [
        _module("src/a.py", [_symbol("f")]),
        _module("src/empty.py", []),
        _module("src/all_private.py", [_symbol("_g", is_public=False)]),
    ]}}
    refs = build_api_reference(evidence)
    assert set(refs) == {"src/a.py"}
    assert "f" in refs["src/a.py"]


def test_build_api_reference_excludes_test_files_even_with_public_symbols():
    # Test functions are module-level and unprefixed, so is_public sees them
    # as ordinary public API - but a Docs page documenting an app's public
    # surface shouldn't include its own test suite, dogfooding-confirmed
    # (test_dashboard.py functions showed up as "generated" API entries).
    evidence = {"repository": {"modules": [
        _module("src/a.py", [_symbol("f")]),
        _module("tests/test_a.py", [_symbol("test_f_does_the_thing")]),
        _module("src/a_test.py", [_symbol("test_g")]),
    ]}}
    refs = build_api_reference(evidence)
    assert set(refs) == {"src/a.py"}


def test_build_module_reference_renders_ai_generated_description_with_marker():
    evidence = {"repository": {"modules": [_module("src/a.py", [_symbol("f", docstring=None)])]}}
    md = build_module_reference(
        evidence, "src/a.py",
        ai_descriptions={"f": {"description": "Does the thing.", "mode": "generated"}},
    )
    assert "Does the thing." in md
    assert "AI-generated" in md
    assert UNDOCUMENTED not in md


def test_build_module_reference_renders_polished_description_with_marker():
    evidence = {"repository": {"modules": [_module(
        "src/a.py", [_symbol("f", docstring="does thing ok")]
    )]}}
    md = build_module_reference(
        evidence, "src/a.py",
        ai_descriptions={"f": {"description": "Does the thing correctly.", "mode": "polished"}},
    )
    assert "Does the thing correctly." in md
    assert "AI-polished" in md
    assert "does thing ok" not in md


def test_build_module_reference_ignores_ai_descriptions_for_symbols_not_in_it():
    evidence = {"repository": {"modules": [_module(
        "src/a.py", [_symbol("f", docstring="Real docstring.")]
    )]}}
    md = build_module_reference(
        evidence, "src/a.py",
        ai_descriptions={"other_symbol": {"description": "x", "mode": "generated"}},
    )
    assert "Real docstring." in md
    assert "AI-generated" not in md
    assert "AI-polished" not in md


def test_build_api_reference_threads_ai_descriptions_by_module():
    evidence = {"repository": {"modules": [
        _module("src/a.py", [_symbol("f", docstring=None)]),
        _module("src/b.py", [_symbol("g", docstring=None)]),
    ]}}
    refs = build_api_reference(evidence, ai_descriptions_by_module={
        "src/a.py": {"f": {"description": "Generated for f.", "mode": "generated"}},
    })
    assert "Generated for f." in refs["src/a.py"]
    assert UNDOCUMENTED in refs["src/b.py"]
