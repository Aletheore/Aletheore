import pytest

from veridion.query import (
    QUERY_FUNCTIONS,
    BranchNotFoundInEvidenceError,
    ModuleNotFoundInEvidenceError,
    find_branch,
    find_cluster,
    find_imported_by,
    find_imports,
    find_layer_violations,
    find_licenses,
    find_ownership,
    find_secrets_for_file,
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
                    "symbols": {"functions": ["login"], "classes": ["AuthError"]},
                },
                {
                    "path": "app/config.py",
                    "imports": [],
                    "imported_by": ["app/auth.py"],
                    "symbols": {"functions": ["load"], "classes": []},
                },
            ]
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
        "functions": ["login"],
        "classes": ["AuthError"],
    }


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


def test_query_functions_registry_has_all_ten_kinds_with_correct_requires_target():
    expected = {
        "imports": True,
        "imported-by": True,
        "symbols": True,
        "branch": True,
        "ownership": False,
        "secrets": True,
        "vulnerabilities": False,
        "licenses": False,
        "cluster": True,
        "layer-violations": False,
    }
    assert set(QUERY_FUNCTIONS.keys()) == set(expected.keys())
    for kind, requires_target in expected.items():
        _func, actual_requires_target = QUERY_FUNCTIONS[kind]
        assert actual_requires_target == requires_target, kind
