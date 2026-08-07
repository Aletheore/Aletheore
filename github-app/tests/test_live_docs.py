import json
from unittest.mock import MagicMock

from scan_worker.live_docs import generate_file_descriptions


def _adapter(response_text: str) -> MagicMock:
    adapter = MagicMock()
    adapter.simple_completion.return_value = response_text
    return adapter


def _symbol(name: str, **overrides) -> dict:
    base = {
        "name": name,
        "start_line": 1,
        "end_line": 2,
        "params": "(a, b)",
        "docstring": None,
        "return_type": None,
        "is_public": True,
    }
    base.update(overrides)
    return base


def _module(path: str, functions: list[dict]) -> dict:
    return {"path": path, "language": "python", "symbols": {"functions": functions, "classes": []}}


def test_generates_a_description_for_an_undocumented_public_symbol():
    module = _module("a.py", [_symbol("add")])
    source_lines = ["def add(a, b):", "    return a + b"]
    adapter = _adapter(json.dumps({"add": {"description": "Adds two numbers and returns the sum."}}))

    result = generate_file_descriptions(module, source_lines, adapter)

    assert result["add"]["description"] == "Adds two numbers and returns the sum."
    assert result["add"]["mode"] == "generated"


def test_skips_private_symbols_entirely():
    module = _module("a.py", [_symbol("_helper", is_public=False)])
    adapter = _adapter("{}")

    result = generate_file_descriptions(module, ["def _helper():", "    pass"], adapter)

    assert result == {}
    adapter.simple_completion.assert_not_called()


def test_skips_symbols_that_already_have_a_docstring_when_not_polishing():
    module = _module("a.py", [_symbol("add", docstring="Adds two numbers.")])
    adapter = _adapter("{}")

    result = generate_file_descriptions(module, ["def add(a, b):", "    return a + b"], adapter)

    assert result == {}
    adapter.simple_completion.assert_not_called()


def test_rejects_a_response_for_a_symbol_name_that_was_never_asked_about():
    module = _module("a.py", [_symbol("add")])
    # model hallucinates an entry for a symbol name it was never given
    adapter = _adapter(json.dumps({
        "add": {"description": "Adds two numbers."},
        "subtract": {"description": "Subtracts two numbers."},
    }))

    result = generate_file_descriptions(module, ["def add(a, b):", "    return a + b"], adapter)

    assert "subtract" not in result
    assert "add" in result


def test_polishes_an_existing_docstring_when_requested():
    module = _module("a.py", [_symbol(
        "add", start_line=1, end_line=3, docstring="adds a and b together and give the sum back",
    )])
    adapter = _adapter(json.dumps({"add": {"description": "Adds `a` and `b` and returns their sum."}}))

    result = generate_file_descriptions(
        module,
        ["def add(a, b):", '    """adds a and b together..."""', "    return a + b"],
        adapter,
        polish_existing=True,
    )

    assert result["add"]["mode"] == "polished"
    assert result["add"]["description"] == "Adds `a` and `b` and returns their sum."


def test_polish_mode_skips_symbols_with_no_existing_docstring():
    module = _module("a.py", [_symbol("add", docstring=None)])
    adapter = _adapter("{}")

    result = generate_file_descriptions(
        module, ["def add(a, b):", "    return a + b"], adapter, polish_existing=True,
    )

    assert result == {}
    adapter.simple_completion.assert_not_called()


def test_malformed_model_response_yields_no_descriptions_not_a_crash():
    module = _module("a.py", [_symbol("add")])
    adapter = _adapter("not json at all")

    result = generate_file_descriptions(module, ["def add(a, b):", "    return a + b"], adapter)

    assert result == {}


def test_response_missing_description_key_is_dropped():
    module = _module("a.py", [_symbol("add")])
    adapter = _adapter(json.dumps({"add": {"not_description": "oops"}}))

    result = generate_file_descriptions(module, ["def add(a, b):", "    return a + b"], adapter)

    assert result == {}


def test_no_symbols_needing_work_never_calls_the_adapter():
    module = _module("a.py", [])
    adapter = _adapter("{}")

    result = generate_file_descriptions(module, [], adapter)

    assert result == {}
    adapter.simple_completion.assert_not_called()
