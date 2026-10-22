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
    ],
)
def test_routes_clear_questions_with_high_confidence(question, intent):
    decision = tr.route(question, None, PATHS)
    assert (decision.confidence, decision.intent) == ("high", intent)


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
