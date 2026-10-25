"""The router answers "which files are imported the most" and "is there a circular import" from the scan's import graph."""
import pytest

from aletheore import query as q
from aletheore import tool_router as tr


def _evidence(edges: dict[str, list[str]], inferred: set[tuple[str, str]] = frozenset()):
    """Evidence with just the module graph: `edges` maps a path to the paths it imports."""
    paths = set(edges) | {t for targets in edges.values() for t in targets}
    imported_by: dict[str, list[str]] = {p: [] for p in paths}
    for src, targets in edges.items():
        for target in targets:
            imported_by[target].append(src)
    modules = [
        {
            "path": p,
            "language": "python",
            "imports": edges.get(p, []),
            "imported_by": sorted(imported_by[p]),
            "import_confidence": {t: "inferred" for t in edges.get(p, []) if (p, t) in inferred},
        }
        for p in sorted(paths)
    ]
    return {"repository": {"modules": modules}}


HUBS_GRAPH = {
    "app/a.py": ["lib/core.py", "lib/util.py"],
    "app/b.py": ["lib/core.py", "lib/util.py"],
    "app/c.py": ["lib/core.py"],
    "app/d.py": ["lib/core.py", "lib/extra.py"],
    "tests/test_x.py": ["lib/extra.py", "lib/util.py", "lib/core.py"],
    "lib/util.py": ["lib/core.py"],
}


def test_most_imported_ranks_by_distinct_importers_and_never_lists_tests():
    result = q.find_most_imported_modules(_evidence(HUBS_GRAPH), limit=3)
    assert [m["path"] for m in result["most_imported"]] == ["lib/core.py", "lib/util.py", "lib/extra.py"]
    assert [m["imported_by_count"] for m in result["most_imported"]] == [6, 3, 2]
    assert result["importers_counted"] == "all"


def test_a_non_test_question_counts_only_non_test_importers_and_flags_a_tie():
    result = q.find_most_imported_modules(_evidence(HUBS_GRAPH), limit=2, ignore_test_importers=True)
    assert [(m["path"], m["imported_by_count"]) for m in result["most_imported"]] == [("lib/core.py", 5), ("lib/util.py", 2)]
    # lib/extra.py has one non-test importer against two for lib/util.py, so there is no tie at the cut
    assert result["tied_at_cutoff"] is False
    tied = q.find_most_imported_modules(_evidence({"a.py": ["x.py", "y.py"], "b.py": ["x.py", "y.py"]}), limit=1)
    assert tied["tied_at_cutoff"] is True


def test_most_imported_can_be_limited_to_a_directory():
    result = q.find_most_imported_modules(_evidence(HUBS_GRAPH), scope="lib", limit=5)
    assert {m["path"] for m in result["most_imported"]} <= {"lib/core.py", "lib/util.py", "lib/extra.py"}


def test_cycles_reports_an_ordered_cycle_and_finds_none_in_a_dag():
    graph = {"a.py": ["b.py"], "b.py": ["c.py"], "c.py": ["a.py", "d.py"], "d.py": []}
    result = q.find_import_cycles(_evidence(graph))
    assert result["has_cycle"] is True and result["cycle_groups_total"] == 1
    cycle = result["cycle_groups"][0]["example_cycle"]
    assert sorted(cycle) == ["a.py", "b.py", "c.py"]
    for i, module in enumerate(cycle):  # each module imports the next, the last imports the first
        assert cycle[(i + 1) % len(cycle)] in graph[module]
    assert q.find_import_cycles(_evidence({"a.py": ["b.py"], "b.py": []}))["has_cycle"] is False


def test_cycles_prefers_a_short_cycle_and_ignores_test_files_when_asked():
    graph = {"a.py": ["b.py", "c.py"], "b.py": ["a.py"], "c.py": ["d.py"], "d.py": ["a.py"], "tests/test_a.py": ["a.py"]}
    assert len(q.find_import_cycles(_evidence(graph))["cycle_groups"][0]["example_cycle"]) == 2
    only_through_tests = {"a.py": ["tests/test_a.py"], "tests/test_a.py": ["a.py"]}
    assert q.find_import_cycles(_evidence(only_through_tests))["has_cycle"] is True
    assert q.find_import_cycles(_evidence(only_through_tests), ignore_test_files=True)["has_cycle"] is False


def test_cycles_do_not_follow_inferred_edges():
    # a build-tag alternative or an ambiguous resolution is not a real import
    graph = {"a.go": ["a_alt.go"], "a_alt.go": ["a.go"]}
    inferred = {("a.go", "a_alt.go"), ("a_alt.go", "a.go")}
    assert q.find_import_cycles(_evidence(graph, inferred))["has_cycle"] is False
    assert q.find_import_cycles(_evidence(graph))["has_cycle"] is True


def test_deep_import_chains_do_not_hit_the_recursion_limit():
    chain = {f"m{i}.py": [f"m{i + 1}.py"] for i in range(5000)}
    chain["m5000.py"] = ["m0.py"]
    result = q.find_import_cycles(_evidence(chain))
    assert result["cycle_groups"][0]["size"] == 5001


PATHS = sorted({m["path"] for m in _evidence(HUBS_GRAPH)["repository"]["modules"]})


@pytest.mark.parametrize(
    "question",
    [
        "Which 5 NON-TEST Python source files are depended on by the MOST other NON-TEST source files (a file depends on another when it imports it)?",
        "which are the most imported files in this repo",
        "what are the most depended-on modules",
        "show the top 3 hubs",
        "which modules have the highest fan-in",
    ],
)
def test_most_imported_questions_route_to_hubs(question):
    decision = tr.route(question, None, PATHS)
    assert (decision.confidence, decision.intent) == ("high", "hubs")


@pytest.mark.parametrize(
    "question",
    [
        "Is there a circular dependency among the NON-TEST Go source files of this repository (a file depends on another when it imports it)?",
        "are there any import cycles",
        "do any modules import each other",
        "find circular imports under lib",
    ],
)
def test_circular_import_questions_route_to_cycles(question):
    decision = tr.route(question, None, PATHS)
    assert (decision.confidence, decision.intent) == ("high", "cycles")


@pytest.mark.parametrize(
    "question,intent",
    [
        ("which files change most often", "hotspot"),
        ("list unused files", "dead_code"),
        ("what depends on lib/core.py", "impact"),
        ("who owns lib/core.py", "owner"),
    ],
)
def test_the_new_intents_do_not_capture_neighbouring_questions(question, intent):
    assert tr.route(question, None, PATHS).intent == intent


def test_answer_runs_the_hubs_and_cycles_queries_with_the_count_and_scope_from_the_question():
    evidence = _evidence(HUBS_GRAPH)
    hubs = tr.answer("List the top 2 most imported files.", None, evidence, None)
    assert hubs["routed_to"] == "most_imported"
    assert [m["path"] for m in hubs["answer"]["most_imported"]] == ["lib/core.py", "lib/util.py"]
    non_test = tr.answer("Which 2 NON-TEST files are imported by the most NON-TEST files?", None, evidence, None)
    assert [m["imported_by_count"] for m in non_test["answer"]["most_imported"]] == [5, 2]
    cycles = tr.answer("are there any import cycles", None, _evidence({"a.py": ["b.py"], "b.py": ["a.py"]}), None)
    assert cycles["routed_to"] == "import_cycles" and cycles["answer"]["has_cycle"] is True
