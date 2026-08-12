from aletheore.wiki_mapping import (
    MAX_SYMBOLS_PER_FILE,
    build_cluster_briefs,
    rank_files_by_importance,
)


def make_evidence(functions_per_file: int = 2) -> dict:
    return {
        "repository": {
            "modules": [
                {
                    "path": "auth/login.py",
                    "language": "python",
                    "symbols": {
                        "functions": [
                            {"name": f"fn_{i}", "start_line": i, "end_line": i + 2}
                            for i in range(functions_per_file)
                        ],
                        "classes": [{"name": "LoginHandler", "start_line": 50, "end_line": 80}],
                    },
                },
                {"path": "auth/tokens.py", "language": "python", "symbols": {}},
            ]
        },
        "architecture": {
            "clusters": [{"id": 0, "modules": ["auth/login.py", "auth/tokens.py"], "internal_edges": 1}]
        },
    }


def test_build_cluster_briefs_returns_one_brief_per_cluster():
    briefs = build_cluster_briefs(make_evidence())
    assert len(briefs) == 1
    assert briefs[0]["cluster_id"] == 0


def test_build_cluster_briefs_includes_all_member_files():
    briefs = build_cluster_briefs(make_evidence())
    paths = {f["path"] for f in briefs[0]["files"]}
    assert paths == {"auth/login.py", "auth/tokens.py"}


def test_build_cluster_briefs_extracts_functions_and_classes():
    briefs = build_cluster_briefs(make_evidence())
    login_file = next(f for f in briefs[0]["files"] if f["path"] == "auth/login.py")
    names = {s["name"] for s in login_file["key_symbols"]}
    assert "fn_0" in names
    assert "LoginHandler" in names
    kinds = {s["name"]: s["kind"] for s in login_file["key_symbols"]}
    assert kinds["LoginHandler"] == "class"
    assert kinds["fn_0"] == "function"


def test_build_cluster_briefs_caps_symbols_per_file():
    briefs = build_cluster_briefs(make_evidence(functions_per_file=MAX_SYMBOLS_PER_FILE + 10))
    login_file = next(f for f in briefs[0]["files"] if f["path"] == "auth/login.py")
    assert len(login_file["key_symbols"]) == MAX_SYMBOLS_PER_FILE


def test_build_cluster_briefs_skips_files_missing_from_modules():
    evidence = make_evidence()
    evidence["architecture"]["clusters"][0]["modules"].append("ghost/deleted.py")
    briefs = build_cluster_briefs(evidence)
    paths = {f["path"] for f in briefs[0]["files"]}
    assert "ghost/deleted.py" not in paths


def test_build_cluster_briefs_includes_a_deterministic_fallback_name():
    briefs = build_cluster_briefs(make_evidence())
    assert briefs[0]["fallback_name"] == "auth"


def test_build_cluster_briefs_handles_empty_evidence():
    assert build_cluster_briefs({"repository": {"modules": []}, "architecture": {"clusters": []}}) == []


def _ranking_evidence() -> dict:
    return {
        "repository": {
            "modules": [
                {
                    "path": "app.py",
                    "imported_by": ["main.py"],
                    "symbols": {"functions": [{"name": f"f{i}"} for i in range(40)], "classes": []},
                },
                {
                    "path": "util.py",
                    "imported_by": ["app.py", "main.py", "cli.py"],
                    "symbols": {"functions": [{"name": "helper"}], "classes": []},
                },
                {
                    "path": "tests/test_app.py",
                    "imported_by": ["a.py", "b.py", "c.py", "d.py", "e.py"],
                    "symbols": {"functions": [{"name": f"t{i}"} for i in range(30)], "classes": []},
                },
            ]
        },
        "git": {"hotspots": [{"path": "app.py", "churn_count": 90}]},
    }


def test_rank_files_by_importance_counts_size_not_just_in_degree():
    """A god-module sits at the top of the import tree, so few files import it.
    Ranking on in-degree alone buried Flask's app.py below typing.py."""
    ranked = rank_files_by_importance(_ranking_evidence())
    order = [r["path"] for r in ranked]
    assert order.index("app.py") < order.index("util.py")


def test_rank_files_by_importance_reads_churn_count_key():
    ranked = rank_files_by_importance(_ranking_evidence())
    app = next(r for r in ranked if r["path"] == "app.py")
    assert app["churn"] == 90


def test_rank_files_by_importance_demotes_tests_below_application_code():
    ranked = rank_files_by_importance(_ranking_evidence())
    order = [r["path"] for r in ranked]
    assert order.index("tests/test_app.py") == len(order) - 1
    assert next(r for r in ranked if r["path"] == "tests/test_app.py")["demoted"] is True


def test_rank_files_by_importance_is_stable_for_equal_scores():
    evidence = {
        "repository": {
            "modules": [
                {"path": "b.py", "imported_by": [], "symbols": {}},
                {"path": "a.py", "imported_by": [], "symbols": {}},
            ]
        }
    }
    assert [r["path"] for r in rank_files_by_importance(evidence)] == ["a.py", "b.py"]


def test_rank_files_by_importance_survives_missing_git_and_symbols():
    evidence = {"repository": {"modules": [{"path": "a.py"}]}}
    assert [r["path"] for r in rank_files_by_importance(evidence)] == ["a.py"]


def test_rank_files_by_importance_lifts_public_api_over_internal_utilities():
    """In-degree actively works against entry points: a module re-exported by
    __init__.py is imported once, while a leaf utility is imported by
    everything. Measured on psf/requests - api.py, the whole public API,
    ranked 17th and got no page while compat.py, a shim, ranked 1st."""
    evidence = {
        "repository": {
            "modules": [
                {"path": "pkg/__init__.py", "imports": ["pkg/api.py"], "imported_by": [], "symbols": {}},
                {
                    "path": "pkg/api.py",
                    "imports": [], "imported_by": ["pkg/__init__.py"],
                    "symbols": {"functions": [{"name": f"f{i}"} for i in range(8)]},
                },
                {
                    "path": "pkg/compat.py",
                    "imports": [], "imported_by": [f"pkg/m{i}.py" for i in range(16)],
                    "symbols": {"functions": [{"name": "shim"}]},
                },
            ]
        }
    }
    ranked = rank_files_by_importance(evidence)
    order = [r["path"] for r in ranked]
    assert order.index("pkg/api.py") < order.index("pkg/compat.py")
    assert next(r for r in ranked if r["path"] == "pkg/api.py")["public_api"] is True
    assert next(r for r in ranked if r["path"] == "pkg/compat.py")["public_api"] is False
