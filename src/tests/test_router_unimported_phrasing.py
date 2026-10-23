"""'Which modules does nothing import' asked many ways must reach the dead-code query, not stall on a tie.

A trace of the benchmark showed an agent asking "Which modules in this directory are not imported by any other
module in the repo?", the router answering low confidence (dead_code and neighborhood both matched "imported by"),
and the agent falling back to grep. neighborhood needs a file and the question named only a directory, so it
could never have run.
"""

import pytest

from aletheore import tool_router as tr

PATHS = ["pkg/a.py", "pkg/b.py", "pkg/sub/c.py", "other/d.py"]


@pytest.mark.parametrize(
    "question",
    [
        "Which modules in this directory are not imported by any other module in the repo?",
        "which modules under pkg are never imported anywhere",
        "list the files in pkg that nothing imports",
        "which files in pkg have no importers",
        "which modules in pkg are not imported by anything",
        "modules in pkg that nobody imports",
        "in pkg, which files aren't imported by any other file",
        "find pkg modules with no one importing them",
    ],
)
def test_unimported_phrasings_reach_dead_code_with_high_confidence(question):
    decision = tr.route(question, "pkg", PATHS)
    assert (decision.confidence, decision.intent) == ("high", "dead_code"), question
    assert decision.scope == "pkg"


def test_a_tie_is_broken_toward_the_query_that_can_run_without_a_file():
    # neighborhood needs a file; with none named, only dead_code can answer
    decision = tr.route("which modules are not imported by any other module", None, PATHS)
    assert (decision.confidence, decision.intent) == ("high", "dead_code")
    assert "needs a file" not in decision.reason


def test_a_genuine_tie_between_runnable_queries_is_still_low_confidence():
    # secrets and vulnerabilities both run without a file, so the router must not pick one
    decision = tr.route("licenses and vulnerabilities", None, PATHS)
    assert decision.confidence == "low"
    assert decision.candidates


def test_who_imports_this_file_still_goes_to_the_import_query_when_a_file_is_named():
    decision = tr.route("what is imported by pkg/a.py", None, PATHS)
    assert decision.intent in ("neighborhood", "impact")
    assert decision.confidence == "high"
