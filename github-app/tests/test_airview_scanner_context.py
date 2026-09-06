"""No test coverage existed for this module at all before this file - found
via audit of PR #545. Focused especially on `_schema_context`, where a real
bug was found and fixed alongside these tests: a table's own real `file`/
`line` (always attached by schema_map.py's `_merge_schema_events`,
regardless of source) was being discarded here, contradicting this
module's own docstring ("Schema/endpoint entries that carry a real
file:line are safe to cite")."""

from scan_worker.airview_scanner_context import build_repo_context


def test_build_repo_context_omits_every_field_when_nothing_was_scanned():
    assert build_repo_context({}) == {}


def test_schema_context_omitted_when_not_checked():
    evidence = {"repository": {"database": {"schema": {"checked": False, "tables": [
        {"name": "users", "columns": [], "file": "db/schema.sql", "line": 1},
    ]}}}}
    assert "database_schema" not in build_repo_context(evidence)


def test_schema_context_omitted_when_checked_but_no_tables():
    evidence = {"repository": {"database": {"schema": {"checked": True, "tables": []}}}}
    assert "database_schema" not in build_repo_context(evidence)


def test_schema_context_includes_table_file_and_line():
    evidence = {"repository": {"database": {"schema": {
        "checked": True,
        "tables": [
            {"name": "users", "columns": [{"name": "id"}, {"name": "email"}],
             "file": "db/schema.sql", "line": 12},
        ],
        "relations": [],
    }}}}
    context = build_repo_context(evidence)
    table = context["database_schema"]["tables"][0]
    assert table == {
        "name": "users", "columns": ["id", "email"],
        "file": "db/schema.sql", "line": 12,
    }


def test_schema_context_includes_relation_file_and_line():
    evidence = {"repository": {"database": {"schema": {
        "checked": True,
        "tables": [{"name": "posts", "columns": [], "file": "db/schema.sql", "line": 1}],
        "relations": [{
            "from_table": "posts", "from_column": "author_id",
            "to_table": "users", "to_column": "id",
            "file": "migrations/002.sql", "line": 5,
        }],
    }}}}
    relation = build_repo_context(evidence)["database_schema"]["relations"][0]
    assert relation["file"] == "migrations/002.sql"
    assert relation["line"] == 5


def test_endpoints_context_excludes_unresolved_and_includes_location():
    evidence = {"repository": {"api_endpoints": {
        "checked": True,
        "endpoints": [
            {"method": "GET", "path": "/users", "file": "routes.py", "line": 10,
             "handler": "list_users"},
            {"method": "GET", "path": None, "unresolved": True, "file": "routes.py", "line": 20,
             "handler": "dynamic"},
        ],
    }}}
    endpoints = build_repo_context(evidence)["api_endpoints"]
    assert len(endpoints) == 1
    assert endpoints[0]["path"] == "/users"


def test_endpoints_context_omitted_when_all_unresolved():
    evidence = {"repository": {"api_endpoints": {
        "checked": True,
        "endpoints": [{"method": "GET", "path": None, "unresolved": True}],
    }}}
    assert "api_endpoints" not in build_repo_context(evidence)


def test_vulnerabilities_context_present_when_checked():
    evidence = {"security": {"dependency_vulnerabilities": {
        "checked": True,
        "findings": [{"package": "lodash", "ecosystem": "npm", "advisory_id": "GHSA-1",
                       "summary": "prototype pollution"}],
    }}}
    findings = build_repo_context(evidence)["dependency_vulnerabilities"]
    assert findings == [
        {"package": "lodash", "ecosystem": "npm", "advisory_id": "GHSA-1",
         "summary": "prototype pollution"}
    ]


def test_licenses_context_flags_non_permissive_categories_under_the_cap():
    evidence = {"security": {"dependency_licenses": {
        "checked": True,
        "repo_license": {"category": "permissive"},
        "findings": [
            {"package": "gpl-lib", "license": "GPL-3.0", "category": "copyleft"},
            {"package": "mit-lib", "license": "MIT", "category": "permissive"},
        ],
    }}}
    licenses = build_repo_context(evidence)["dependency_licenses"]
    assert licenses["dependency_count"] == 2
    assert licenses["by_category"] == {"copyleft": 1, "permissive": 1}
    assert licenses["flagged_packages"] == [
        {"package": "gpl-lib", "license": "GPL-3.0", "category": "copyleft"}
    ]


def test_licenses_context_omits_flagged_packages_list_above_the_cap():
    evidence = {"security": {"dependency_licenses": {
        "checked": True,
        "repo_license": {"category": "permissive"},
        "findings": [
            {"package": f"pkg{i}", "license": "GPL-3.0", "category": "copyleft"}
            for i in range(13)
        ],
    }}}
    licenses = build_repo_context(evidence)["dependency_licenses"]
    assert "flagged_packages" not in licenses
    assert licenses["by_category"] == {"copyleft": 13}


def test_dead_code_context_omitted_when_empty():
    evidence = {"repository": {"dead_code": {"unreachable_modules": [], "unused_dependencies": []}}}
    assert "dead_code" not in build_repo_context(evidence)


def test_dead_code_context_normalizes_module_dicts_to_paths():
    evidence = {"repository": {"dead_code": {
        "unreachable_modules": [{"path": "legacy/old.py"}, "legacy/other.py"],
        "unused_dependencies": ["left-pad"],
    }}}
    dead_code = build_repo_context(evidence)["dead_code"]
    assert dead_code["unreachable_modules"] == ["legacy/old.py", "legacy/other.py"]
    assert dead_code["unused_dependencies"] == ["left-pad"]


def test_infrastructure_context_omitted_when_nothing_present():
    evidence = {"repository": {"infrastructure": {}}}
    assert "infrastructure" not in build_repo_context(evidence)


def test_infrastructure_context_flattens_compose_services_and_flags_iac():
    evidence = {"repository": {"infrastructure": {
        "docker_compose_services": [{"services": ["web", "db"]}],
        "kubernetes_manifests": ["k8s/deploy.yaml"],
        "terraform_files": [],
        "helm_charts": [],
    }}}
    infra = build_repo_context(evidence)["infrastructure"]
    assert infra["docker_compose_services"] == ["web", "db"]
    assert infra["has_kubernetes_manifests"] is True
    assert infra["has_terraform"] is False


def test_env_vars_context_returns_sorted_unique_names():
    evidence = {"repository": {"environment_variables": {"declared": [
        {"name": "DATABASE_URL"}, {"name": "API_KEY"}, {"name": "API_KEY"},
    ]}}}
    assert build_repo_context(evidence)["environment_variables"] == ["API_KEY", "DATABASE_URL"]


def test_env_vars_context_omitted_when_none_declared():
    evidence = {"repository": {"environment_variables": {"declared": []}}}
    assert "environment_variables" not in build_repo_context(evidence)
