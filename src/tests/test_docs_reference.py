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
