import json
import subprocess

from aletheore.evidence_resolution import (
    attach_dependency_evidence,
    attach_risk_evidence,
    empty_resolution,
    merge_resolution,
    normalize_resolution,
    resolve_endpoint,
    resolve_owner,
    resolve_recent_commit,
)


def make_evidence():
    return {
        "repository": {
            "modules": [
                {
                    "path": "app/routes.py",
                    "imports": ["app/services/users.py", "os"],
                    "imported_by": [],
                    "symbols": {
                        "functions": [{"name": "list_users", "start_line": 10, "end_line": 20}],
                        "classes": [],
                    },
                }
            ],
            "api_endpoints": {
                "checked": True,
                "endpoints": [
                    {
                        "method": "GET",
                        "path": "/v1/users",
                        "framework": "fastapi",
                        "file": "app/routes.py",
                        "line": 12,
                        "handler": "list_users",
                        "unresolved": False,
                    }
                ],
            },
        },
        "security": {
            "secrets": {
                "findings": [
                    {
                        "path": "app/routes.py",
                        "line": 15,
                        "pattern": "generic_secret",
                        "likely_placeholder": False,
                    }
                ]
            },
            "dependency_vulnerabilities": {
                "findings": [
                    {
                        "package": "os",
                        "severity": "HIGH",
                        "advisory_id": "GHSA-test",
                    }
                ]
            },
            "dependency_licenses": {
                "findings": [
                    {
                        "package": "app-services",
                        "license": "UNKNOWN",
                        "severity": "medium",
                    }
                ]
            },
        },
        "architecture": {
            "layer_violations": {
                "violations": [
                    {
                        "from": "app/routes.py",
                        "to": "app/services/users.py",
                        "reason": "route depends on lower layer incorrectly",
                    }
                ]
            }
        },
    }


def test_empty_resolution_exposes_missing_evidence():
    result = empty_resolution("endpoint")

    assert result["kind"] == "endpoint"
    assert result["file"] is None
    assert result["line"] is None
    assert result["symbol"] is None
    assert result["owner"] is None
    assert result["commit"] is None
    assert result["dependency"] is None
    assert result["risk"] == []
    assert result["confidence"] == "unavailable"
    assert result["owner_status"] == "unavailable"
    assert result["commit_status"] == "unavailable"
    assert result["dependency_status"] == "unavailable"
    assert result["risk_status"] == "unavailable"


def test_normalize_resolution_preserves_source_location():
    result = normalize_resolution(
        kind="symbol",
        file="app/routes.py",
        line=10,
        end_line=20,
        symbol="list_users",
        evidence_path="repository.modules[0].symbols.functions[0]",
        confidence="exact",
    )

    assert result["file"] == "app/routes.py"
    assert result["line"] == 10
    assert result["end_line"] == 20
    assert result["symbol"] == "list_users"
    assert result["confidence"] == "exact"


def test_merge_resolution_does_not_replace_exact_with_unavailable():
    exact = normalize_resolution(kind="endpoint", file="app/routes.py", line=12, confidence="exact")
    unavailable = empty_resolution("endpoint")

    result = merge_resolution(exact, unavailable)

    assert result["file"] == "app/routes.py"
    assert result["line"] == 12
    assert result["confidence"] == "exact"


def test_resolve_endpoint_returns_file_line_and_handler_symbol():
    result = resolve_endpoint(make_evidence(), "get", "/v1/users")

    assert result["kind"] == "endpoint"
    assert result["file"] == "app/routes.py"
    assert result["line"] == 12
    assert result["symbol"] == "list_users"
    assert result["confidence"] == "exact"
    assert result["evidence_path"] == "repository.api_endpoints.endpoints[0]"


def test_resolve_endpoint_is_unavailable_for_unknown_endpoint():
    result = resolve_endpoint(make_evidence(), "POST", "/missing")

    assert result["kind"] == "endpoint"
    assert result["file"] is None
    assert result["line"] is None
    assert result["confidence"] == "unavailable"
    assert result["evidence_status"] == "unavailable"


def test_resolve_owner_uses_last_matching_codeowners_rule(tmp_path):
    repo = tmp_path
    (repo / ".github").mkdir()
    (repo / ".github" / "CODEOWNERS").write_text(
        "* @global\napp/ @app-team\napp/routes.py @api-team @second-owner\n"
    )

    result = resolve_owner(repo, "app/routes.py")

    assert result["owner"] == ["@api-team", "@second-owner"]
    assert result["owner_status"] == "available"
    assert result["confidence"] == "inferred"


def test_resolve_owner_returns_unavailable_without_codeowners(tmp_path):
    result = resolve_owner(tmp_path, "app/routes.py")

    assert result["owner"] is None
    assert result["owner_status"] == "unavailable"


def test_resolve_recent_commit_returns_file_commit(tmp_path):
    repo = tmp_path
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Alice"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "alice@example.com"], cwd=repo, check=True)
    (repo / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add app"], cwd=repo, check=True, capture_output=True)

    result = resolve_recent_commit(repo, "app.py")

    assert result["commit"]["subject"] == "add app"
    assert result["commit"]["author"] == "Alice"
    assert result["commit_status"] == "available"
    assert result["confidence"] == "weak"


def test_resolve_recent_commit_returns_unavailable_for_non_git_repo(tmp_path):
    result = resolve_recent_commit(tmp_path, "app.py")

    assert result["commit"] is None
    assert result["commit_status"] == "unavailable"


def test_attach_dependency_evidence_uses_module_imports():
    resolution = resolve_endpoint(make_evidence(), "GET", "/v1/users")

    result = attach_dependency_evidence(make_evidence(), resolution)

    assert result["dependency"] == ["app/services/users.py", "os"]
    assert result["dependency_status"] == "available"


def test_attach_risk_evidence_attaches_matching_findings():
    resolution = resolve_endpoint(make_evidence(), "GET", "/v1/users")

    result = attach_risk_evidence(make_evidence(), resolution)

    categories = {risk["category"] for risk in result["risk"]}
    assert "secret" in categories
    assert "architecture" in categories
    assert "vulnerability" in categories
    assert result["risk_status"] == "available"


def test_resolution_is_json_serializable():
    result = attach_risk_evidence(
        make_evidence(),
        attach_dependency_evidence(make_evidence(), resolve_endpoint(make_evidence(), "GET", "/v1/users")),
    )

    json.dumps(result)
