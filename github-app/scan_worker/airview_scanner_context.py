"""Repo-wide scanner context for AIRview prompts.

AIRview's subsystem/file-page prompts (live_wiki.py) are built entirely
from `repository.modules` + the import graph (`related_files`) - none of
the other scanners (database schema, API endpoints, dependency
vulnerabilities/licenses, dead code, infrastructure, environment
variables) ever reach the model. This module builds a single compact,
repo-wide summary of those sections to attach alongside `brief`/
`related_files` in every generation call, the same way `related_files`
already crosses cluster boundaries to give the model context beyond its
own subsystem.

Kept intentionally compact (names, paths, counts - not full nested
structures) since this rides on every single generation call in a build,
not just one: a 198-row raw dependency-license list would roughly double
prompt size for no benefit most subsystems ever need.

Schema/endpoint entries that carry a real file:line are safe to cite -
`citation_verifier.verify_citations` checks against ALL known repo file
paths, not just the calling cluster's own brief. Vulnerability/license/
dead-code/infrastructure/env-var facts are repo-level, not tied to one
file, so the system prompt is told to treat those as background, not to
manufacture a citation for them.
"""

from collections import Counter

# Above this, a single dependency-license category's flagged list stops
# being "a few worth naming" and starts being its own wall of text -
# summarized as a count instead once it crosses this.
MAX_NAMED_LICENSE_FINDINGS = 12
NON_PERMISSIVE_LICENSE_CATEGORIES = {"copyleft", "proprietary", "unknown"}

# Real gap found via audit: every OTHER section below was building an
# unbounded list (all tables, all relations, all endpoints, all
# vulnerability findings, all dead-code entries, all env var names) despite
# this module's own docstring above promising "compact... since this rides
# on every single generation call in a build, not just one" - only the
# license section actually enforced that. A real large repo (500 tables,
# 300 endpoints, 200 env vars - not an extreme case for a big monorepo)
# measured directly at ~155KB of JSON attached to EVERY generation call in
# a full build, not once. Capped the same way licenses already are, on a
# deterministic (sorted, not dict-iteration-order-dependent) prefix so the
# same evidence always produces the same truncated set rather than an
# arbitrary one. `database_schema` and `dead_code` are dicts already, so
# their real total counts are kept alongside the capped list (a truncated
# section should read as "50 of 500", not as if the repo only had 50
# tables); `api_endpoints`/`dependency_vulnerabilities`/
# `environment_variables` stay bare lists at the top level to preserve
# their existing shape for callers, capped without an accompanying count.
MAX_SCHEMA_TABLES = 50
MAX_SCHEMA_RELATIONS = 50
MAX_ENDPOINTS = 50
MAX_VULNERABILITY_FINDINGS = 30
MAX_DEAD_CODE_ENTRIES = 30
MAX_ENV_VARS = 50


def build_repo_context(evidence: dict) -> dict:
    repository = evidence.get("repository", {})
    security = evidence.get("security", {})

    # Every field is omitted rather than sent as `null` when a scanner
    # wasn't run or found nothing - a smaller, unambiguous payload the
    # model never has to reason about a null value for.
    fields = {
        "database_schema": _schema_context(repository.get("database", {}).get("schema", {})),
        "api_endpoints": _endpoints_context(repository.get("api_endpoints", {})),
        "dependency_vulnerabilities": _vulnerabilities_context(
            security.get("dependency_vulnerabilities", {})
        ),
        "dependency_licenses": _licenses_context(security.get("dependency_licenses", {})),
        "dead_code": _dead_code_context(repository.get("dead_code", {})),
        "infrastructure": _infrastructure_context(repository.get("infrastructure", {})),
        "environment_variables": _env_vars_context(repository.get("environment_variables", {})),
    }
    return {key: value for key, value in fields.items() if value is not None}


def _schema_context(schema: dict) -> dict | None:
    if not schema.get("checked"):
        return None
    tables = sorted(
        (
            {
                "name": t["name"], "columns": [c["name"] for c in t.get("columns", [])],
                "file": t.get("file"), "line": t.get("line"),
            }
            for t in schema.get("tables", [])
        ),
        # Real gap found via Flash Review: sorting by name alone leaves
        # same-named tables (a real shape for a monorepo aggregating
        # multiple schemas) in their original, not-guaranteed-stable
        # input order - the full key makes the truncated prefix
        # deterministic for identical evidence regardless of input order.
        key=lambda t: (t["name"], t["file"] or "", t["line"] or 0, tuple(t["columns"])),
    )
    relations = sorted(
        (
            {
                "from_table": r["from_table"], "from_column": r["from_column"],
                "to_table": r["to_table"], "to_column": r["to_column"],
                "file": r.get("file"), "line": r.get("line"),
            }
            for r in schema.get("relations", [])
        ),
        key=lambda r: (
            r["from_table"], r["from_column"], r["to_table"], r["to_column"],
            r["file"] or "", r["line"] or 0,
        ),
    )
    if not tables:
        return None
    result = {"tables": tables[:MAX_SCHEMA_TABLES], "relations": relations[:MAX_SCHEMA_RELATIONS]}
    if len(tables) > MAX_SCHEMA_TABLES:
        result["tables_total_count"] = len(tables)
    if len(relations) > MAX_SCHEMA_RELATIONS:
        result["relations_total_count"] = len(relations)
    return result


def _endpoints_context(api_endpoints: dict) -> list[dict] | None:
    if not api_endpoints.get("checked"):
        return None
    endpoints = sorted(
        (
            {
                "method": e["method"], "path": e["path"],
                "file": e.get("file"), "line": e.get("line"), "handler": e.get("handler"),
            }
            for e in api_endpoints.get("endpoints", [])
            if not e.get("unresolved")
        ),
        # file/line/handler added per Flash Review: two endpoints sharing
        # a path+method (a real shape for versioned or duplicate routes)
        # otherwise kept their original, not-guaranteed-stable input order.
        key=lambda e: (e["path"] or "", e["method"] or "", e["file"] or "", e["line"] or 0, e["handler"] or ""),
    )
    return endpoints[:MAX_ENDPOINTS] or None


def _vulnerabilities_context(vulns: dict) -> list[dict] | None:
    if not vulns.get("checked"):
        return None
    findings = sorted(
        (
            {"package": f["package"], "ecosystem": f["ecosystem"], "advisory_id": f.get("advisory_id"),
             "summary": f.get("summary")}
            for f in vulns.get("findings", [])
        ),
        # advisory_id/summary added per Flash Review: two findings for the
        # same package+ecosystem (a real shape - multiple advisories
        # against one installed version) otherwise kept their original,
        # not-guaranteed-stable input order.
        key=lambda f: (f["package"], f["ecosystem"], f["advisory_id"] or "", f["summary"] or ""),
    )
    return findings[:MAX_VULNERABILITY_FINDINGS] or None


def _licenses_context(licenses: dict) -> dict | None:
    if not licenses.get("checked"):
        return None
    findings = licenses.get("findings", [])
    by_category = Counter(f.get("category", "unknown") for f in findings)
    flagged = [f for f in findings if f.get("category") in NON_PERMISSIVE_LICENSE_CATEGORIES]
    result = {
        "repo_license_category": licenses.get("repo_license", {}).get("category"),
        "dependency_count": len(findings),
        "by_category": dict(by_category),
    }
    if flagged and len(flagged) <= MAX_NAMED_LICENSE_FINDINGS:
        result["flagged_packages"] = [
            {"package": f["package"], "license": f.get("license"), "category": f.get("category")}
            for f in flagged
        ]
    return result


def _dead_code_context(dead_code: dict) -> dict | None:
    unreachable = sorted(
        # Real crash risk found via Flash Review: a dict entry lacking a
        # "path" key previously fell through to str(dict) here rather than
        # the bare dict itself - sorting a list that mixes dicts and
        # strings raises TypeError in Python (no '<' between the two).
        # Always normalizing to a string keeps the list homogeneous and
        # therefore always sortable, regardless of what shape any one
        # entry happens to be.
        (m.get("path", str(m)) if isinstance(m, dict) else m for m in dead_code.get("unreachable_modules", [])),
    )
    unused_deps = sorted(dead_code.get("unused_dependencies", []))
    if not unreachable and not unused_deps:
        return None
    result = {
        "unreachable_modules": unreachable[:MAX_DEAD_CODE_ENTRIES],
        "unused_dependencies": unused_deps[:MAX_DEAD_CODE_ENTRIES],
    }
    if len(unreachable) > MAX_DEAD_CODE_ENTRIES:
        result["unreachable_modules_total_count"] = len(unreachable)
    if len(unused_deps) > MAX_DEAD_CODE_ENTRIES:
        result["unused_dependencies_total_count"] = len(unused_deps)
    return result


def _infrastructure_context(infrastructure: dict) -> dict | None:
    services = [
        service
        for entry in infrastructure.get("docker_compose_services", [])
        for service in entry.get("services", [])
    ]
    has_k8s = bool(infrastructure.get("kubernetes_manifests"))
    has_terraform = bool(infrastructure.get("terraform_files"))
    has_helm = bool(infrastructure.get("helm_charts"))
    if not services and not (has_k8s or has_terraform or has_helm):
        return None
    return {
        "docker_compose_services": services,
        "has_kubernetes_manifests": has_k8s,
        "has_terraform": has_terraform,
        "has_helm_charts": has_helm,
    }


def _env_vars_context(env_vars: dict) -> list[str] | None:
    names = sorted({e["name"] for e in env_vars.get("declared", []) if e.get("name")})
    return names[:MAX_ENV_VARS] or None
