"""No test coverage existed for this module at all before this file - found
via audit of PR #545. Focused especially on `_schema_context`, where a real
bug was found and fixed alongside these tests: a table's own real `file`/
`line` (always attached by schema_map.py's `_merge_schema_events`,
regardless of source) was being discarded here, contradicting this
module's own docstring ("Schema/endpoint entries that carry a real
file:line are safe to cite")."""

from scan_worker.airview_scanner_context import (
    MAX_DEAD_CODE_ENTRIES,
    MAX_ENDPOINTS,
    MAX_ENV_VARS,
    MAX_INFRASTRUCTURE_SERVICES,
    MAX_SCHEMA_RELATIONS,
    MAX_SCHEMA_TABLES,
    MAX_VULNERABILITY_FINDINGS,
    build_repo_context,
)


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
    assert infra["docker_compose_services"] == ["db", "web"]
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


# ---------------------------------------------------------------------------
# Real gap found via audit: every section below (except licenses) built an
# unbounded list despite this module's own docstring promising "compact...
# since this rides on every single generation call in a build, not just
# one". A real large repo (500 tables, 300 endpoints, 200 env vars) measured
# at ~155KB of JSON attached to EVERY generation call before these caps.
# ---------------------------------------------------------------------------


def test_schema_context_caps_tables_and_relations_deterministically():
    evidence = {"repository": {"database": {"schema": {
        "checked": True,
        "tables": [
            {"name": f"t{i:03d}", "columns": [], "file": "db/schema.sql", "line": i}
            for i in range(MAX_SCHEMA_TABLES + 5)
        ],
        "relations": [
            {"from_table": f"t{i:03d}", "from_column": "x_id", "to_table": "t000", "to_column": "id",
             "file": "db/schema.sql", "line": i}
            for i in range(MAX_SCHEMA_RELATIONS + 5)
        ],
    }}}}
    schema = build_repo_context(evidence)["database_schema"]
    assert len(schema["tables"]) == MAX_SCHEMA_TABLES
    assert schema["tables_total_count"] == MAX_SCHEMA_TABLES + 5
    # Deterministic (sorted by name), not dict/list-iteration-order-dependent -
    # the same evidence must always cap to the same prefix.
    assert schema["tables"][0]["name"] == "t000"
    assert len(schema["relations"]) == MAX_SCHEMA_RELATIONS
    assert schema["relations_total_count"] == MAX_SCHEMA_RELATIONS + 5


def test_schema_context_tables_with_the_same_name_stay_deterministically_ordered():
    # Real gap found via Flash Review on this same PR: sorting by name
    # alone leaves same-named tables (a real shape for a monorepo
    # aggregating multiple schemas) in Python's stable-sort input order,
    # which is not itself guaranteed to be the same across two runs over
    # logically-equivalent evidence. file/line/columns break the tie.
    evidence = {"repository": {"database": {"schema": {
        "checked": True,
        "tables": [
            {"name": "users", "columns": [{"name": "id"}], "file": "b/schema.sql", "line": 5},
            {"name": "users", "columns": [{"name": "id"}], "file": "a/schema.sql", "line": 1},
        ],
        "relations": [
            {"from_table": "users", "from_column": "id", "to_table": "posts", "to_column": "user_id",
             "file": "b/schema.sql", "line": 9},
            {"from_table": "users", "from_column": "id", "to_table": "posts", "to_column": "user_id",
             "file": "a/schema.sql", "line": 2},
        ],
    }}}}
    schema = build_repo_context(evidence)["database_schema"]
    assert [t["file"] for t in schema["tables"]] == ["a/schema.sql", "b/schema.sql"]
    assert [r["file"] for r in schema["relations"]] == ["a/schema.sql", "b/schema.sql"]


def test_schema_context_omits_total_count_when_under_the_cap():
    evidence = {"repository": {"database": {"schema": {
        "checked": True,
        "tables": [{"name": "users", "columns": [], "file": "db/schema.sql", "line": 1}],
        "relations": [],
    }}}}
    schema = build_repo_context(evidence)["database_schema"]
    assert "tables_total_count" not in schema
    assert "relations_total_count" not in schema


def test_endpoints_context_caps_deterministically():
    evidence = {"repository": {"api_endpoints": {
        "checked": True,
        "endpoints": [
            {"method": "GET", "path": f"/x{i:03d}", "file": "routes.py", "line": i, "handler": f"h{i}"}
            for i in range(MAX_ENDPOINTS + 5)
        ],
    }}}
    endpoints = build_repo_context(evidence)["api_endpoints"]
    assert len(endpoints) == MAX_ENDPOINTS
    assert endpoints[0]["path"] == "/x000"


def test_endpoints_context_with_the_same_path_and_method_stay_deterministically_ordered():
    # Real gap found via Flash Review: two endpoints sharing a path+method
    # (a real shape for versioned or duplicate routes) otherwise kept
    # their original, not-guaranteed-stable input order.
    evidence = {"repository": {"api_endpoints": {
        "checked": True,
        "endpoints": [
            {"method": "GET", "path": "/x", "file": "b.py", "line": 5, "handler": "h2"},
            {"method": "GET", "path": "/x", "file": "a.py", "line": 1, "handler": "h1"},
        ],
    }}}
    endpoints = build_repo_context(evidence)["api_endpoints"]
    assert [e["file"] for e in endpoints] == ["a.py", "b.py"]


def test_vulnerabilities_context_caps_deterministically():
    evidence = {"security": {"dependency_vulnerabilities": {
        "checked": True,
        "findings": [
            {"package": f"pkg{i:03d}", "ecosystem": "npm", "advisory_id": f"GHSA-{i}", "summary": "x"}
            for i in range(MAX_VULNERABILITY_FINDINGS + 5)
        ],
    }}}
    findings = build_repo_context(evidence)["dependency_vulnerabilities"]
    assert len(findings) == MAX_VULNERABILITY_FINDINGS
    assert findings[0]["package"] == "pkg000"


def test_vulnerabilities_context_with_the_same_package_and_ecosystem_stay_deterministically_ordered():
    # Real gap found via Flash Review: two findings for the same
    # package+ecosystem (a real shape - multiple advisories against one
    # installed version) otherwise kept their original, not-guaranteed-
    # stable input order.
    evidence = {"security": {"dependency_vulnerabilities": {
        "checked": True,
        "findings": [
            {"package": "lodash", "ecosystem": "npm", "advisory_id": "GHSA-2", "summary": "b"},
            {"package": "lodash", "ecosystem": "npm", "advisory_id": "GHSA-1", "summary": "a"},
        ],
    }}}
    findings = build_repo_context(evidence)["dependency_vulnerabilities"]
    assert [f["advisory_id"] for f in findings] == ["GHSA-1", "GHSA-2"]


def test_dead_code_context_normalizes_a_pathless_dict_entry_instead_of_crashing():
    # Real crash risk found via Flash Review: a dict entry lacking a
    # "path" key previously stayed a raw dict, and sorting a list that
    # mixes dicts and strings raises TypeError in Python.
    evidence = {"repository": {"dead_code": {
        "unreachable_modules": [{"other_field": "x"}, "legacy/m.py"],
        "unused_dependencies": [],
    }}}
    dead_code = build_repo_context(evidence)["dead_code"]
    assert all(isinstance(entry, str) for entry in dead_code["unreachable_modules"])
    assert "legacy/m.py" in dead_code["unreachable_modules"]


def test_dead_code_context_caps_deterministically_with_total_counts():
    evidence = {"repository": {"dead_code": {
        "unreachable_modules": [{"path": f"legacy/m{i:03d}.py"} for i in range(MAX_DEAD_CODE_ENTRIES + 5)],
        "unused_dependencies": [f"pkg{i:03d}" for i in range(MAX_DEAD_CODE_ENTRIES + 5)],
    }}}
    dead_code = build_repo_context(evidence)["dead_code"]
    assert len(dead_code["unreachable_modules"]) == MAX_DEAD_CODE_ENTRIES
    assert dead_code["unreachable_modules_total_count"] == MAX_DEAD_CODE_ENTRIES + 5
    assert dead_code["unreachable_modules"][0] == "legacy/m000.py"
    assert len(dead_code["unused_dependencies"]) == MAX_DEAD_CODE_ENTRIES
    assert dead_code["unused_dependencies_total_count"] == MAX_DEAD_CODE_ENTRIES + 5


def test_infrastructure_context_caps_services_deterministically_with_total_count():
    # Real bug found via audit: this was the one section PR #586's
    # unboundedness fix missed - it never capped or deterministically
    # sorted docker_compose_services, unlike every sibling section above.
    evidence = {"repository": {"infrastructure": {
        "docker_compose_services": [
            {"services": [f"svc{i:03d}" for i in range(MAX_INFRASTRUCTURE_SERVICES + 5)]}
        ],
    }}}
    infra = build_repo_context(evidence)["infrastructure"]
    assert len(infra["docker_compose_services"]) == MAX_INFRASTRUCTURE_SERVICES
    assert infra["docker_compose_services_total_count"] == MAX_INFRASTRUCTURE_SERVICES + 5
    assert infra["docker_compose_services"][0] == "svc000"


def test_env_vars_context_caps_deterministically():
    evidence = {"repository": {"environment_variables": {"declared": [
        {"name": f"VAR_{i:03d}"} for i in range(MAX_ENV_VARS + 5)
    ]}}}
    names = build_repo_context(evidence)["environment_variables"]
    assert len(names) == MAX_ENV_VARS
    assert names[0] == "VAR_000"


def test_build_repo_context_stays_small_for_a_large_real_repo():
    # Real measurement this guards: 500 tables/499 relations/300 endpoints/
    # 200 env vars/120 vulnerabilities/150 dead-code entries produced
    # ~155KB of JSON before the caps above existed - not an extreme case
    # for a large real monorepo, and this rides on EVERY generation call in
    # a full build, not just one.
    import json

    evidence = {
        "repository": {
            "database": {"schema": {
                "checked": True,
                "tables": [
                    {"name": f"t{i}", "columns": [{"name": "id"}], "file": "db/schema.sql", "line": i}
                    for i in range(500)
                ],
                "relations": [
                    {"from_table": f"t{i}", "from_column": "x_id", "to_table": f"t{i + 1}",
                     "to_column": "id", "file": "db/schema.sql", "line": i}
                    for i in range(499)
                ],
            }},
            "api_endpoints": {"checked": True, "endpoints": [
                {"method": "GET", "path": f"/x{i}", "file": "routes.py", "line": i, "handler": f"h{i}"}
                for i in range(300)
            ]},
            "environment_variables": {"declared": [{"name": f"VAR_{i}"} for i in range(200)]},
            "dead_code": {
                "unreachable_modules": [{"path": f"legacy/m{i}.py"} for i in range(150)],
                "unused_dependencies": [f"pkg{i}" for i in range(100)],
            },
        },
        "security": {"dependency_vulnerabilities": {"checked": True, "findings": [
            {"package": f"pkg{i}", "ecosystem": "npm", "advisory_id": f"GHSA-{i}", "summary": "x" * 60}
            for i in range(120)
        ]}},
    }
    payload_bytes = len(json.dumps(build_repo_context(evidence)).encode("utf-8"))
    assert payload_bytes < 30_000
