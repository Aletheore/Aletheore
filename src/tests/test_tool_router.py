import pytest

from aletheore import tool_router as tr

PATHS = [
    "src/api/orders.py",
    "src/api/users.py",
    "src/billing/orders.py",
    "src/core/query_builder.py",
    "web/src/app/page.tsx",
]


@pytest.mark.parametrize(
    "question,intent",
    [
        ("what breaks if I change src/core/query_builder.py", "impact"),
        ("who calls src/core/query_builder.py?", "impact"),
        ("which tests cover src/api/users.py", "tests"),
        ("who owns src/api/users.py", "owner"),
        ("which files change most often", "hotspot"),
        ("are there any leaked secrets", "secrets"),
        ("any vulnerable dependencies?", "vulnerabilities"),
        ("what endpoints does this expose", "endpoints"),
        ("what is this repo", "overview"),
        ("where is the retry logic implemented", "locate"),
        ("list unused files", "dead_code"),
        # forward vs reverse: what a file needs is not what needs it
        ("what does src/api/users.py depend on", "dependencies"),
        ("how many modules does src/api/users.py depend on through imports, directly or transitively?", "dependencies"),
        ("transitive dependencies of src/core/query_builder.py", "dependencies"),
        ("what depends on src/core/query_builder.py", "impact"),
        ("who depends on src/api/users.py", "impact"),
        # ownership asked as a commit count must not become a hotspot question
        ("who has made the most commits touching src/api/users.py?", "owner"),
        ("which author has the most commits on src/api/users.py", "owner"),
        ("which files have the most commits", "hotspot"),
    ],
)
def test_routes_clear_questions_with_high_confidence(question, intent):
    decision = tr.route(question, None, PATHS)
    assert (decision.confidence, decision.intent) == ("high", intent)


def test_a_one_word_directory_is_a_scope_only_when_the_question_marks_it_as_a_place():
    quoted = tr.route("Under the directory `web` list the modules that no other module imports.", None, PATHS)
    assert (quoted.intent, quoted.scope) == ("dead_code", "web")
    spoken = tr.route("In the web folder, list the modules that nobody imports.", None, PATHS)
    assert (spoken.intent, spoken.scope) == ("dead_code", "web")
    # "web" is only a word here, followed by more of the sentence, not a place
    assert tr.route("list unused files in web projects", None, PATHS).scope is None


def test_cpp_and_csharp_unimported_questions_route_to_dead_code():
    for language in ("C++", "C#", "Java", "Python"):
        decision = tr.route(f"List the modules that no other {language} module imports.", None, PATHS)
        assert (decision.confidence, decision.intent) == ("high", "dead_code"), language


@pytest.mark.parametrize("verb", ["depends on", "uses", "references", "requires", "imports"])
def test_unreachable_questions_route_to_dead_code_whatever_verb_they_use(verb):
    question = f"List the NON-TEST Go source files that no other source file in the repository {verb} (ignore string references)."
    decision = tr.route(question, None, PATHS)
    assert (decision.confidence, decision.intent) == ("high", "dead_code")


NEXT_PATHS = [*PATHS, "docs/app/(doc)/[[...slug]]/page.tsx", "docs/app/(shop)/[id]/page.tsx", "docs/app/(shop)/layout.tsx"]


@pytest.mark.parametrize(
    "target",
    ["docs/app/(doc)/[[...slug]]/page.tsx", "docs/app/(shop)/[id]/page.tsx"],
)
def test_framework_route_paths_with_groups_and_brackets_are_file_targets(target):
    question = (
        f"How many NON-TEST TypeScript/JavaScript source files does `{target}` depend on through imports, "
        "directly or transitively (files it imports, the files those import, and so on, within this repository)? "
        "Give your best estimate."
    )
    decision = tr.route(question, None, NEXT_PATHS)
    assert (decision.confidence, decision.intent, decision.target) == ("high", "dependencies", target)
    assert tr.route(f"what breaks if I change {target}", None, NEXT_PATHS).target == target


def test_parentheses_in_prose_next_to_a_slash_are_not_a_path():
    decision = tr.route("who owns src/api/users.py (and/or src/api/orders.py)", None, PATHS)
    assert decision.intent == "owner"


def test_a_root_level_file_name_does_not_misroute_a_dependency_question():
    paths = ["auth.go", "internal/store.go"]
    decision = tr.route("How many modules does auth.go depend on through imports, directly or transitively?", None, paths)
    assert (decision.confidence, decision.intent, decision.target) == ("high", "dependencies", "auth.go")


def test_a_file_question_without_a_file_is_low_confidence_not_a_guess():
    decision = tr.route("is it safe to rename this", None, PATHS)
    assert decision.confidence == "low"
    assert decision.tool == "aletheore_get_blast_radius"
    assert decision.target is None


def test_an_ambiguous_file_name_is_reported_not_picked():
    decision = tr.route("what breaks if I change orders.py", None, PATHS)
    assert decision.confidence == "low"
    assert decision.candidates == ["src/api/orders.py", "src/billing/orders.py"]


def test_explicit_target_wins_and_a_unique_basename_resolves():
    assert tr.route("who owns this", "src/api/users.py", PATHS).target == "src/api/users.py"
    assert tr.route("what breaks if I change users.py", None, PATHS).target == "src/api/users.py"


def test_unrelated_text_matches_nothing():
    for text in ("tell me a joke", "", "   "):
        decision = tr.route(text, None, PATHS)
        assert decision.confidence == "none"
        assert decision.candidates


def test_tests_beats_impact_when_tests_are_named():
    assert tr.route("which tests break if I change src/api/users.py", None, PATHS).intent == "tests"


def test_a_bare_file_gets_the_impact_answer():
    decision = tr.route("src/api/users.py", None, PATHS)
    assert (decision.confidence, decision.intent) == ("high", "impact")


def test_answer_runs_the_query_and_names_it():
    evidence = {
        "repository": {
            "modules": [
                {"path": "a.py", "imports": [], "imported_by": ["b.py"], "symbols": {"functions": [], "classes": []}},
                {"path": "b.py", "imports": ["a.py"], "imported_by": [], "symbols": {"functions": [], "classes": []}},
            ]
        },
        "git": {"file_ownership": {"a.py": [{"email": "x@y", "names": ["Xi"], "percent": 1.0}]}, "hotspots": []},
    }
    owner = tr.answer("who owns a.py", None, evidence, __import__("pathlib").Path("."))
    assert owner["routed_to"] == "aletheore_ownership"
    assert owner["confidence"] == "high"
    assert owner["answer"][0]["names"] == ["Xi"]
    none = tr.answer("tell me a joke", None, evidence, __import__("pathlib").Path("."))
    assert none["confidence"] == "none" and none["try"]
    low = tr.answer("is it safe to rename this", None, evidence, __import__("pathlib").Path("."))
    assert low["confidence"] == "low" and "file path" in low["next"]


_TEST_DEFINITION = (
    "Test files are any path containing a tests/, test/ or testing/ directory, or a file named test_*.py or *_test.py, "
    "or conftest.py."
)
_UNREACHABLE_QUESTION = (
    "Under the directory `pkg/setup_wizard` (including subdirectories), list the NON-TEST Python modules, other than "
    "__init__.py, that no other Python module in the repository imports. " + _TEST_DEFINITION + " "
    "Answer with ONE JSON array of repo-relative paths and nothing else."
)


def _scoped_evidence():
    def mod(path, imported_by=(), language="python"):
        return {"path": path, "language": language, "imported_by": list(imported_by), "imports": []}

    modules = [
        mod("pkg/setup_wizard/__init__.py"),
        mod("pkg/setup_wizard/run.py"),
        mod("pkg/setup_wizard/ops/defaults.py", ["pkg/setup_wizard/tests/test_defaults.py"]),
        mod("pkg/setup_wizard/ops/used.py", ["pkg/app.py"]),
        mod("pkg/setup_wizard/tests/test_defaults.py"),
        mod("pkg/setup_wizard/ui.js", language="javascript"),
        mod("pkg/elsewhere/orphan.py"),
        mod("pkg/app.py"),
    ]
    return {"repository": {"modules": modules, "dead_code": {"unreachable_modules": []}}}


def test_a_non_test_unreachable_question_routes_to_dead_code_not_tests():
    paths = [m["path"] for m in _scoped_evidence()["repository"]["modules"]]
    decision = tr.route(_UNREACHABLE_QUESTION, None, paths)
    assert (decision.confidence, decision.intent) == ("high", "dead_code")
    assert decision.scope == "pkg/setup_wizard"


def test_a_directory_scoped_import_question_is_answered_for_that_directory_only():
    import pathlib

    out = tr.answer(_UNREACHABLE_QUESTION, None, _scoped_evidence(), pathlib.Path("."))
    assert out["routed_to"] == "aletheore_dead_code" and out["scope"] == "pkg/setup_wizard"
    answer = out["answer"]
    # Python only, test files skipped, a file imported only by its own tests still counts for a non-test question.
    assert answer["unimported"] == ["pkg/setup_wizard/ops/defaults.py", "pkg/setup_wizard/run.py"]
    assert answer["unimported_total"] == 2
    assert answer["importers_counted"] == "non-test only"
    assert "pkg/elsewhere/orphan.py" not in answer["unimported"]


def test_test_qualifiers_and_paths_do_not_pick_the_intent():
    assert "test" not in tr.intent_text_of(_UNREACHABLE_QUESTION).lower()
    assert tr.route("which modules under src/api have no importers", None, PATHS).intent == "dead_code"
    # a snake_case directory name is not an environment variable
    assert tr.route("who owns src/api/setup_wizard", "src/api/users.py", PATHS).intent == "owner"
