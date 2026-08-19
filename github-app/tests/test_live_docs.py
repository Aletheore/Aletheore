import json
from unittest.mock import MagicMock

from scan_worker.live_docs import (
    _content_hash,
    _symbol_snippet,
    generate_file_descriptions,
    generate_file_descriptions_combined,
)


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


def test_combined_handles_generate_and_polish_symbols_in_one_call():
    # One module with both an undocumented symbol (needs generate) and an
    # already-documented one (needs polish) - the whole point of the
    # combined function is covering both in a single LLM call instead of
    # the two separate calls generate_file_descriptions would need.
    module = _module("a.py", [
        _symbol("add", start_line=1, end_line=2),
        _symbol("sub", start_line=3, end_line=4, docstring="subtracts b from a"),
    ])
    source_lines = [
        "def add(a, b):", "    return a + b",
        "def sub(a, b):", "    return a - b",
    ]
    adapter = _adapter(json.dumps({
        "add": {"description": "Adds two numbers and returns the sum."},
        "sub": {"description": "Subtracts `b` from `a` and returns the result."},
    }))

    result = generate_file_descriptions_combined(module, source_lines, adapter)

    assert adapter.simple_completion.call_count == 1
    assert result["add"]["mode"] == "generated"
    assert result["sub"]["mode"] == "polished"


def test_combined_sends_existing_docstring_only_for_polish_items():
    module = _module("a.py", [
        _symbol("add", start_line=1, end_line=2),
        _symbol("sub", start_line=3, end_line=4, docstring="subtracts b from a"),
    ])
    source_lines = ["def add(a, b):", "    return a + b", "def sub(a, b):", "    return a - b"]
    adapter = _adapter("{}")

    generate_file_descriptions_combined(module, source_lines, adapter)

    sent_items = json.loads(adapter.simple_completion.call_args[0][1])
    by_name = {item["name"]: item for item in sent_items}
    assert "existing_docstring" not in by_name["add"]
    assert by_name["sub"]["existing_docstring"] == "subtracts b from a"


def test_combined_rejects_a_response_for_a_symbol_name_that_was_never_asked_about():
    module = _module("a.py", [_symbol("add", start_line=1, end_line=2)])
    adapter = _adapter(json.dumps({
        "add": {"description": "Adds two numbers."},
        "unknown_symbol": {"description": "Was never asked about."},
    }))

    result = generate_file_descriptions_combined(module, ["def add(a, b):", "    return a + b"], adapter)

    assert "unknown_symbol" not in result
    assert "add" in result


def test_combined_no_symbols_needing_work_never_calls_the_adapter():
    module = _module("a.py", [])
    adapter = _adapter("{}")

    result = generate_file_descriptions_combined(module, [], adapter)

    assert result == {}
    adapter.simple_completion.assert_not_called()


def test_combined_result_includes_a_content_hash_per_symbol():
    module = _module("a.py", [_symbol("add", start_line=1, end_line=2)])
    source_lines = ["def add(a, b):", "    return a + b"]
    adapter = _adapter(json.dumps({"add": {"description": "Adds two numbers."}}))

    result = generate_file_descriptions_combined(module, source_lines, adapter)

    assert result["add"]["content_hash"] == _content_hash(_symbol_snippet(source_lines, module["symbols"]["functions"][0]))


def test_combined_skips_a_symbol_whose_snippet_hash_is_unchanged():
    # "add" already has a stored description matching its current source -
    # nothing about it changed, so it shouldn't be re-asked about. "sub" has
    # no stored hash (new/never described), so it should still be sent.
    module = _module("a.py", [
        _symbol("add", start_line=1, end_line=2),
        _symbol("sub", start_line=3, end_line=4),
    ])
    source_lines = [
        "def add(a, b):", "    return a + b",
        "def sub(a, b):", "    return a - b",
    ]
    add_hash = _content_hash(_symbol_snippet(source_lines, module["symbols"]["functions"][0]))
    adapter = _adapter(json.dumps({"sub": {"description": "Subtracts b from a."}}))

    result = generate_file_descriptions_combined(
        module, source_lines, adapter, already_hashed={"add": add_hash},
    )

    sent_items = json.loads(adapter.simple_completion.call_args[0][1])
    sent_names = {item["name"] for item in sent_items}
    assert sent_names == {"sub"}
    assert "add" not in result
    assert result["sub"]["description"] == "Subtracts b from a."


def test_combined_still_asks_about_a_symbol_whose_source_actually_changed():
    # "add" has a stored hash, but it doesn't match the symbol's current
    # source - the function body changed since it was last described, so it
    # must be re-asked about even though a row already exists for it.
    module = _module("a.py", [_symbol("add", start_line=1, end_line=2)])
    source_lines = ["def add(a, b):", "    return a + b + 1"]
    adapter = _adapter(json.dumps({"add": {"description": "Adds two numbers plus one."}}))

    result = generate_file_descriptions_combined(
        module, source_lines, adapter, already_hashed={"add": "stale-hash-from-before-the-edit"},
    )

    adapter.simple_completion.assert_called_once()
    assert result["add"]["description"] == "Adds two numbers plus one."


def test_combined_all_symbols_unchanged_never_calls_the_adapter():
    module = _module("a.py", [_symbol("add", start_line=1, end_line=2)])
    source_lines = ["def add(a, b):", "    return a + b"]
    add_hash = _content_hash(_symbol_snippet(source_lines, module["symbols"]["functions"][0]))
    adapter = _adapter("{}")

    result = generate_file_descriptions_combined(
        module, source_lines, adapter, already_hashed={"add": add_hash},
    )

    assert result == {}
    adapter.simple_completion.assert_not_called()
