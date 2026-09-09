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


def test_two_undocumented_symbols_sharing_a_name_are_never_requested():
    # Real bug found via audit: two symbols sharing a name (a real, if
    # unusual, shape - a conditional redefinition, or a scanner capturing
    # both branches of an @overload pair) both got sent to the model under
    # the identical name key, but the model's own name-keyed response
    # contract can only ever carry one entry per name - whichever wrote
    # last in the name-keyed result dict silently discarded the other's
    # real description. Excluded from the request entirely instead: a
    # colliding name degrades to no AI description for either symbol,
    # never a wrong one silently attributed to the wrong symbol.
    module = _module(
        "a.py",
        [
            _symbol("foo", start_line=1, end_line=2),
            _symbol("foo", start_line=4, end_line=5),
        ],
    )
    adapter = _adapter(json.dumps({"foo": {"description": "Ambiguous - which foo?"}}))

    result = generate_file_descriptions(
        module, ["def foo():", "    return 1", "", "def foo():", "    return 2"], adapter
    )

    assert result == {}
    adapter.simple_completion.assert_not_called()


def test_a_colliding_name_does_not_block_an_unrelated_symbol_in_the_same_file():
    module = _module(
        "a.py",
        [
            _symbol("foo", start_line=1, end_line=2),
            _symbol("foo", start_line=4, end_line=5),
            _symbol("bar", start_line=7, end_line=8),
        ],
    )
    adapter = _adapter(json.dumps({"bar": {"description": "Returns a constant."}}))

    result = generate_file_descriptions(
        module,
        ["def foo():", "    return 1", "", "def foo():", "    return 2", "", "def bar():", "    return 3"],
        adapter,
    )

    assert result == {"bar": {"description": "Returns a constant.", "mode": "generated"}}


def test_a_name_shared_across_different_docstring_states_is_not_treated_as_a_collision():
    # Real Flash Review finding on the fix above: name_counts used to be
    # computed across ALL symbols regardless of docstring state, so an
    # undocumented "foo" got excluded just because a DIFFERENTLY
    # documented "foo" also existed in the file - even though
    # generate_file_descriptions' own single-mode request only ever sends
    # the undocumented one. There's no real name collision in what this
    # call's own request actually contains; excluding it was a false
    # negative, not a safety measure.
    module = _module(
        "a.py",
        [
            _symbol("foo", start_line=1, end_line=2),
            _symbol("foo", start_line=4, end_line=5, docstring="Existing doc."),
        ],
    )
    adapter = _adapter(json.dumps({"foo": {"description": "Returns 1."}}))

    result = generate_file_descriptions(
        module, ["def foo():", "    return 1", "", "def foo():", "    return 2"], adapter
    )

    assert result == {"foo": {"description": "Returns 1.", "mode": "generated"}}


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


def test_combined_two_symbols_sharing_a_name_are_never_requested():
    # Same collision class as generate_file_descriptions' own test, but
    # for the combined generate+polish path's own separate hashes/result
    # collapse logic - a symbol needing "generate" colliding by name with
    # one needing "polish" is exactly as ambiguous to the name-keyed
    # response contract as two generate-mode symbols colliding.
    module = _module(
        "a.py",
        [
            _symbol("foo", start_line=1, end_line=2),
            _symbol("foo", start_line=4, end_line=5, docstring="Existing doc."),
        ],
    )
    adapter = _adapter(json.dumps({"foo": {"description": "Ambiguous - which foo?"}}))

    result = generate_file_descriptions_combined(
        module, ["def foo():", "    return 1", "", "def foo():", "    return 2"], adapter
    )

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
