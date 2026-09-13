"""Tests for _clickable_suggestion/_substitution_parses_cleanly - the gate
deciding whether a Flash Review suggestion is safe to render as a real
GitHub one-click "```suggestion" block instead of today's inert plain
code fence.

This gate exists because GitHub's suggestion feature does a literal,
unreviewed text substitution the instant someone clicks Apply - a wrong
accept here is a wrong commit to a customer's real repository, a
materially different failure mode than a wrong `issue` or `suggestion`
string a human reads before acting on. Every test below either confirms a
genuinely safe case is accepted (and, where relevant, correctly
re-indented), or confirms one specific way a suggestion can be unsafe is
caught - see each test's own docstring for which.
"""
from scan_worker.flash_review import (
    _clickable_suggestion,
    _substitution_parses_cleanly,
    _validate_findings,
)

_ADD_FUNCTION_SOURCE = "def add(a, b):\n    return a + b + 1\n"


def test_accepts_a_correct_single_line_fix_with_matching_indentation():
    finding = {"file": "check.py", "line": 2, "suggestion": "    return a + b"}
    file_contents = {"check.py": _ADD_FUNCTION_SOURCE}
    assert _clickable_suggestion(finding, file_contents, exact_valid_lines={2}) == "    return a + b"


def test_rejects_when_there_is_no_suggestion_at_all():
    finding = {"file": "check.py", "line": 2}
    file_contents = {"check.py": _ADD_FUNCTION_SOURCE}
    assert _clickable_suggestion(finding, file_contents, exact_valid_lines={2}) is None


def test_rejects_a_multi_line_suggestion():
    # A suggestion spanning multiple lines would tell GitHub to replace the
    # one anchored line with several - only ever correct by coincidence,
    # since the prompt explicitly asks for a like-for-like single line.
    finding = {"file": "check.py", "line": 2, "suggestion": "    x = 1\n    return a + b"}
    file_contents = {"check.py": _ADD_FUNCTION_SOURCE}
    assert _clickable_suggestion(finding, file_contents, exact_valid_lines={2}) is None


def test_rejects_a_suggestion_smuggling_a_fence():
    # Defense in depth: review_diff's own earlier structural filter already
    # drops any suggestion containing "```" before this function ever sees
    # it, but this check must not assume that's still true forever.
    finding = {"file": "check.py", "line": 2, "suggestion": "    return a + b\n```"}
    file_contents = {"check.py": _ADD_FUNCTION_SOURCE}
    assert _clickable_suggestion(finding, file_contents, exact_valid_lines={2}) is None


def test_rejects_when_the_line_is_only_near_the_diff_not_exactly_in_it():
    # A one-click substitution always overwrites exactly the anchored
    # position, unlike an ordinary finding a human reads and locates
    # themselves - so the DIFF_LINE_TOLERANCE ordinary findings get does
    # not apply here; the line must be an exact, currently-valid diff line.
    finding = {"file": "check.py", "line": 2, "suggestion": "    return a + b"}
    file_contents = {"check.py": _ADD_FUNCTION_SOURCE}
    assert _clickable_suggestion(finding, file_contents, exact_valid_lines={5}) is None


def test_rejects_when_no_file_contents_are_available():
    finding = {"file": "check.py", "line": 2, "suggestion": "    return a + b"}
    assert _clickable_suggestion(finding, None, exact_valid_lines={2}) is None
    assert _clickable_suggestion(finding, {}, exact_valid_lines={2}) is None


def test_rejects_when_this_files_content_was_not_fetched():
    finding = {"file": "check.py", "line": 2, "suggestion": "    return a + b"}
    file_contents = {"other.py": "x = 1\n"}
    assert _clickable_suggestion(finding, file_contents, exact_valid_lines={2}) is None


def test_rejects_a_line_number_outside_the_files_real_bounds():
    finding = {"file": "check.py", "line": 99, "suggestion": "    return a + b"}
    file_contents = {"check.py": _ADD_FUNCTION_SOURCE}
    assert _clickable_suggestion(finding, file_contents, exact_valid_lines={99}) is None


def test_reindents_a_suggestion_missing_its_leading_whitespace():
    # The single most common real case, not a hypothetical: measured
    # directly against deepseek-chat-v3-0324 across 20 live, non-mocked
    # calls, the model omitted the required leading whitespace in the
    # large majority of otherwise-correct single-line fixes, despite the
    # prompt explicitly asking for "the same leading whitespace/
    # indentation as the line it replaces". GitHub's literal substitution
    # never re-indents, so trusting the model's own whitespace would have
    # made this feature fire on almost nothing real - indentation is
    # mechanical, so the system imposes the one indentation level that is
    # unambiguously correct for a same-line replacement instead of
    # rejecting a suggestion for a whitespace detail the content itself
    # got right.
    finding = {"file": "check.py", "line": 2, "suggestion": "return a + b"}
    file_contents = {"check.py": _ADD_FUNCTION_SOURCE}
    assert _clickable_suggestion(finding, file_contents, exact_valid_lines={2}) == "    return a + b"


def test_reindents_a_suggestion_with_the_wrong_indentation_style_or_depth():
    # Tabs where the file uses spaces, or the right style but wrong depth -
    # same fix as the missing-whitespace case above: re-indent to the real
    # line's own leading whitespace rather than reject.
    file_contents = {"check.py": _ADD_FUNCTION_SOURCE}
    tabs_finding = {"file": "check.py", "line": 2, "suggestion": "\treturn a + b"}
    assert _clickable_suggestion(tabs_finding, file_contents, exact_valid_lines={2}) == "    return a + b"
    wrong_depth_finding = {"file": "check.py", "line": 2, "suggestion": "        return a + b"}
    assert _clickable_suggestion(wrong_depth_finding, file_contents, exact_valid_lines={2}) == "    return a + b"


def test_rejects_a_suggestion_that_would_introduce_a_syntax_error():
    finding = {"file": "check.py", "line": 2, "suggestion": "return a + b +"}
    file_contents = {"check.py": _ADD_FUNCTION_SOURCE}
    assert _clickable_suggestion(finding, file_contents, exact_valid_lines={2}) is None


def test_rejects_when_the_original_file_does_not_parse_cleanly():
    # No clean baseline to compare the substitution against - "couldn't
    # verify" is treated as "looks wrong", never as "probably fine".
    finding = {"file": "check.py", "line": 2, "suggestion": "return a + b"}
    file_contents = {"check.py": "def add(a, b:\n    return a + b + 1\n"}  # missing paren
    assert _clickable_suggestion(finding, file_contents, exact_valid_lines={2}) is None


def test_rejects_a_file_extension_with_no_tree_sitter_grammar():
    finding = {"file": "notes.txt", "line": 1, "suggestion": "hello"}
    file_contents = {"notes.txt": "hi\n"}
    assert _clickable_suggestion(finding, file_contents, exact_valid_lines={1}) is None


def test_rejects_a_suggestion_identical_to_the_line_it_claims_to_fix():
    # A "fix" identical to the line it replaces is not a fix - real bug
    # found via the same 20-case live run: a suggestion equal to a
    # DIFFERENT real line in the file (the model cited the wrong line,
    # then verbatim-copied that wrong line's own text as its "suggestion")
    # trivially passed the similarity check (identical text scores a
    # perfect 1.0) until this explicit no-op check was added.
    finding = {"file": "check.py", "line": 1, "suggestion": "def add(a, b):"}
    file_contents = {"check.py": _ADD_FUNCTION_SOURCE}
    assert _clickable_suggestion(finding, file_contents, exact_valid_lines={1}) is None


def test_rejects_a_suggestion_for_the_wrong_line_that_reindents_coincidentally_cleanly():
    # Real bug found via 20-case live-model testing, not a hand-constructed
    # hypothetical: deepseek-chat-v3-0324 (real call, no mock) cited line 1
    # ("def add(a, b):") for a bug actually on line 2, with no quoted
    # literal in `issue` for _line_citation_content_matches to catch the
    # wrong line against. The suggestion "return a + b" is a real diff
    # line, re-indents cleanly (both lines have zero indentation), and
    # still parses after substitution (deleting a function signature and
    # inserting a bare return is valid Python at module scope) - without
    # the similarity check this would have rendered as a real one-click
    # button that deletes the function's own signature.
    finding = {
        "file": "calc.py", "line": 1,
        "issue": "Modified add() function now adds 1 to result",
        "suggestion": "return a + b",
    }
    file_contents = {"calc.py": _ADD_FUNCTION_SOURCE}
    assert _clickable_suggestion(finding, file_contents, exact_valid_lines={1, 2}) is None


def test_substitution_parses_cleanly_directly_confirms_a_real_fix():
    lines = _ADD_FUNCTION_SOURCE.splitlines()
    assert _substitution_parses_cleanly("check.py", lines, 2, "    return a + b") is True


def test_substitution_parses_cleanly_directly_catches_a_broken_fix():
    lines = _ADD_FUNCTION_SOURCE.splitlines()
    assert _substitution_parses_cleanly("check.py", lines, 2, "    return a + b +") is False


def test_accepts_and_reindents_a_correct_fix_in_a_different_language_with_tabs():
    # Confirms re-indentation and the tree-sitter grammar lookup both work
    # for a non-Python, tab-indented language, not just the space-indented
    # Python fixture used everywhere else in this file.
    source = "function add(a, b) {\n\treturn a + b + 1;\n}\n"
    finding = {"file": "check.js", "line": 2, "suggestion": "return a + b;"}
    file_contents = {"check.js": source}
    assert _clickable_suggestion(finding, file_contents, exact_valid_lines={2}) == "\treturn a + b;"


def test_accepts_real_model_generated_fixes_across_four_languages():
    # Similarity-ratio regression guard using the exact real files/lines/
    # suggestions measured while calibrating the 0.5 threshold in a live,
    # non-mocked run against deepseek-chat-v3-0324, so a future edit to
    # this check can't silently tighten it past what real model output
    # actually produces. Suggestions are given WITHOUT leading whitespace,
    # matching the model's own real (indentation-omitting) output, and
    # each fixture is wrapped in real enclosing syntax (a function/class) -
    # a bare `return` statement is a syntax error in every one of these
    # languages, which would make the tree-sitter differential check
    # reject even a correct fix for the wrong reason (no clean baseline).
    real_fixes = [
        ("calc.py", "def add(a, b):\n    return a + b + 1\n", 2, "return a + b", "    return a + b"),
        ("limit.rs", "fn under_limit(x: i32) -> bool {\n    x <= 100\n}\n", 2, "x < 100", "    x < 100"),
        (
            "Access.cs",
            "class Access {\n    bool CanEnter(bool isAdmin, bool isMember) {\n"
            "        return isAdmin || isMember && false;\n    }\n}\n",
            3, "return isAdmin || isMember;", "        return isAdmin || isMember;",
        ),
        (
            "check.ts", "function isZero(n: number): boolean {\n  return n == 0;\n}\n",
            2, "return n === 0;", "  return n === 0;",
        ),
    ]
    for filename, content, line, suggestion, expected in real_fixes:
        finding = {"file": filename, "line": line, "suggestion": suggestion}
        result = _clickable_suggestion(finding, {filename: content}, exact_valid_lines={line})
        assert result == expected, f"real fix wrongly rejected/mis-corrected: {filename} line {line} -> {result!r}"


def test_validate_findings_marks_a_real_suggestion_clickable_and_reindents_it_end_to_end():
    # Exercises the actual integration point in _validate_findings, not
    # just the isolated _clickable_suggestion unit above - confirms the
    # diff_text -> valid_lines -> exact-line-check wiring is really
    # connected, and that the corrected (re-indented) text is written back
    # into finding["suggestion"] itself so the rendered comment and the
    # real one-click substitution are always the same string.
    diff_text = "--- check.py ---\n@@ -1,2 +1,2 @@\n def add(a, b):\n+    return a + b + 1\n"
    file_contents = {"check.py": _ADD_FUNCTION_SOURCE}
    findings = [{
        "file": "check.py", "line": 2, "issue": "off by one",
        "suggestion": "return a + b",  # deliberately missing its leading whitespace
    }]

    kept = _validate_findings(findings, diff_text, file_contents)

    assert len(kept) == 1
    assert kept[0]["suggestion_clickable"] is True
    assert kept[0]["suggestion"] == "    return a + b"


def test_validate_findings_marks_a_wrong_line_suggestion_not_clickable_end_to_end():
    diff_text = "--- calc.py ---\n@@ -1,2 +1,2 @@\n def add(a, b):\n+    return a + b + 1\n"
    file_contents = {"calc.py": _ADD_FUNCTION_SOURCE}
    findings = [{
        "file": "calc.py", "line": 1, "issue": "off by one",
        "suggestion": "return a + b",
    }]

    kept = _validate_findings(findings, diff_text, file_contents)

    assert len(kept) == 1
    assert kept[0]["suggestion_clickable"] is False
    # The original, unmodified model text is preserved for the prose
    # fallback rendering when it's not treated as clickable.
    assert kept[0]["suggestion"] == "return a + b"


def test_validate_findings_leaves_findings_without_a_suggestion_unannotated():
    diff_text = "--- check.py ---\n@@ -1,2 +1,2 @@\n def add(a, b):\n+    return a + b + 1\n"
    findings = [{"file": "check.py", "line": 2, "issue": "off by one"}]

    kept = _validate_findings(findings, diff_text)

    assert kept == [{"file": "check.py", "line": 2, "issue": "off by one"}]
    assert "suggestion_clickable" not in kept[0]
