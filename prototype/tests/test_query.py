import pytest

from aletheore.query import (
    QUERY_FUNCTIONS,
    BranchNotFoundInEvidenceError,
    ModuleNotFoundInEvidenceError,
    SymbolNotFoundInEvidenceError,
    find_branch,
    find_cluster,
    find_dead_code_evidence,
    find_endpoints,
    find_hotspots,
    find_imported_by,
    find_imports,
    find_layer_violations,
    find_licenses,
    find_ownership,
    find_secrets_for_file,
    find_symbol_source,
    find_symbols,
    find_vulnerabilities,
)


def make_evidence():
    return {
        "repository": {
            "modules": [
                {
                    "path": "app/auth.py",
                    "imports": ["app/config.py"],
                    "imported_by": ["app/routes.py"],
                    "symbols": {
                        "functions": [{"name": "login", "start_line": 4, "end_line": 5}],
                        "classes": [{"name": "AuthError", "start_line": 8, "end_line": 9}],
                    },
                },
                {
                    "path": "app/config.py",
                    "imports": [],
                    "imported_by": ["app/auth.py"],
                    "symbols": {
                        "functions": [{"name": "load", "start_line": 3, "end_line": 4}],
                        "classes": [],
                    },
                },
            ],
            "api_endpoints": {
                "checked": True,
                "endpoints": [
                    {
                        "method": "GET",
                        "path": "/users",
                        "framework": "flask",
                        "file": "app.py",
                        "line": 1,
                        "handler": "list_users",
                        "unresolved": False,
                    }
                ],
            },
            "dead_code": {
                "unreachable_modules": [{"path": "app/unused.py", "reason": "no imports"}],
                "unused_dependencies": [],
                "entry_points_detected": ["app/main.py"],
            },
        },
        "git": {
            "branches": [
                {
                    "name": "main",
                    "type": "local",
                    "stale_days": 0,
                    "ahead_of_main": 0,
                    "behind_main": 0,
                }
            ],
            "ownership": [
                {"email": "a@example.com", "names": ["Alice"], "commit_count": 5, "percent": 1.0}
            ],
            "hotspots": [
                {
                    "path": "app/auth.py",
                    "churn_count": 3,
                    "co_change_partners": [{"path": "app/config.py", "co_occurrences": 2}],
                    "dependents_count": 1,
                }
            ],
        },
        "security": {
            "secrets": {
                "scanned_files": 2,
                "findings": [
                    {
                        "path": "app/auth.py",
                        "line": 3,
                        "pattern": "aws_access_key_id",
                        "match_preview": "AKIA****...WXYZ",
                        "likely_placeholder": False,
                    }
                ],
            },
            "dependency_vulnerabilities": {"checked": True, "reason": None, "findings": []},
            "dependency_licenses": {
                "checked": True,
                "reason": None,
                "repo_license": {"category": "permissive", "detected_from": "LICENSE text match"},
                "findings": [],
            },
        },
        "architecture": {
            "clusters": [
                {"id": 0, "modules": ["app/auth.py", "app/config.py"], "internal_edges": 1}
            ],
            "layer_violations": {"convention_detected": False, "layers": [], "violations": []},
        },
    }


def test_find_imports_returns_the_module_imports_list():
    assert find_imports(make_evidence(), "app/auth.py") == ["app/config.py"]


def test_find_imported_by_returns_the_module_imported_by_list():
    assert find_imported_by(make_evidence(), "app/config.py") == ["app/auth.py"]


def test_find_symbols_returns_the_module_symbols_dict():
    assert find_symbols(make_evidence(), "app/auth.py") == {
        "functions": [{"name": "login", "start_line": 4, "end_line": 5}],
        "classes": [{"name": "AuthError", "start_line": 8, "end_line": 9}],
    }


def test_find_symbol_source_returns_exact_lines(tmp_path):
    repo = tmp_path
    (repo / "app").mkdir()
    (repo / "app" / "auth.py").write_text(
        "from app import config\n\n\ndef login():\n    return config.load()\n\n"
    )

    result = find_symbol_source(make_evidence(), repo, "app/auth.py", "login")

    assert result["module"] == "app/auth.py"
    assert result["symbol"] == "login"
    assert result["start_line"] == 4
    assert result["end_line"] == 5
    assert result["source"] == "def login():\n    return config.load()"


def test_find_symbol_source_raises_when_symbol_missing(tmp_path):
    with pytest.raises(SymbolNotFoundInEvidenceError, match="nonexistent"):
        find_symbol_source(make_evidence(), tmp_path, "app/auth.py", "nonexistent")


def test_find_symbol_source_raises_when_module_missing(tmp_path):
    with pytest.raises(ModuleNotFoundInEvidenceError):
        find_symbol_source(make_evidence(), tmp_path, "app/missing.py", "login")


def test_find_imports_raises_for_unknown_path():
    with pytest.raises(ModuleNotFoundInEvidenceError):
        find_imports(make_evidence(), "app/does_not_exist.py")


def test_find_branch_returns_the_branch_entry():
    result = find_branch(make_evidence(), "main")
    assert result["stale_days"] == 0


def test_find_branch_raises_for_unknown_branch():
    with pytest.raises(BranchNotFoundInEvidenceError):
        find_branch(make_evidence(), "does-not-exist")


def test_find_ownership_returns_the_whole_list_ignoring_target():
    result = find_ownership(make_evidence(), None)
    assert result == make_evidence()["git"]["ownership"]


def test_find_secrets_for_file_filters_by_path():
    result = find_secrets_for_file(make_evidence(), "app/auth.py")
    assert len(result) == 1
    assert result[0]["pattern"] == "aws_access_key_id"

    assert find_secrets_for_file(make_evidence(), "app/config.py") == []


def test_find_vulnerabilities_returns_the_whole_block_ignoring_target():
    result = find_vulnerabilities(make_evidence(), None)
    assert result == make_evidence()["security"]["dependency_vulnerabilities"]


def test_find_licenses_returns_the_whole_block_ignoring_target():
    result = find_licenses(make_evidence(), None)
    assert result == make_evidence()["security"]["dependency_licenses"]


def test_find_endpoints_returns_the_whole_block_ignoring_target():
    result = find_endpoints(make_evidence(), None)
    assert result == make_evidence()["repository"]["api_endpoints"]


def test_find_cluster_returns_the_cluster_containing_the_file():
    result = find_cluster(make_evidence(), "app/config.py")
    assert result["id"] == 0
    assert "app/auth.py" in result["modules"]


def test_find_cluster_raises_for_unknown_path():
    with pytest.raises(ModuleNotFoundInEvidenceError):
        find_cluster(make_evidence(), "app/does_not_exist.py")


def test_find_layer_violations_returns_the_whole_block_ignoring_target():
    result = find_layer_violations(make_evidence(), None)
    assert result == make_evidence()["architecture"]["layer_violations"]


def test_find_dead_code_evidence_returns_the_whole_block_ignoring_target():
    assert find_dead_code_evidence(make_evidence(), None) == make_evidence()["repository"]["dead_code"]


def test_find_hotspots_returns_git_hotspots_ignoring_target():
    assert find_hotspots(make_evidence(), None) == make_evidence()["git"]["hotspots"]


def test_query_functions_registry_has_all_kinds_with_correct_requires_target():
    expected = {
        "imports": True,
        "imported-by": True,
        "symbols": True,
        "branch": True,
        "ownership": False,
        "secrets": True,
        "vulnerabilities": False,
        "licenses": False,
        "endpoints": False,
        "cluster": True,
        "layer-violations": False,
        "dead-code": False,
        "hotspots": False,
    }
    assert set(QUERY_FUNCTIONS.keys()) == set(expected.keys())
    for kind, requires_target in expected.items():
        _func, actual_requires_target = QUERY_FUNCTIONS[kind]
        assert actual_requires_target == requires_target, kind
