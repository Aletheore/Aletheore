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
    tables = [
        {"name": t["name"], "columns": [c["name"] for c in t.get("columns", [])]}
        for t in schema.get("tables", [])
    ]
    relations = [
        {
            "from_table": r["from_table"], "from_column": r["from_column"],
            "to_table": r["to_table"], "to_column": r["to_column"],
            "file": r.get("file"), "line": r.get("line"),
        }
        for r in schema.get("relations", [])
    ]
    if not tables:
        return None
    return {"tables": tables, "relations": relations}


def _endpoints_context(api_endpoints: dict) -> list[dict] | None:
    if not api_endpoints.get("checked"):
        return None
    endpoints = [
        {
            "method": e["method"], "path": e["path"],
            "file": e.get("file"), "line": e.get("line"), "handler": e.get("handler"),
        }
        for e in api_endpoints.get("endpoints", [])
        if not e.get("unresolved")
    ]
    return endpoints or None


def _vulnerabilities_context(vulns: dict) -> list[dict] | None:
    if not vulns.get("checked"):
        return None
    findings = [
        {"package": f["package"], "ecosystem": f["ecosystem"], "advisory_id": f.get("advisory_id"),
         "summary": f.get("summary")}
        for f in vulns.get("findings", [])
    ]
    return findings or None


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
    unreachable = dead_code.get("unreachable_modules", [])
    unused_deps = dead_code.get("unused_dependencies", [])
    if not unreachable and not unused_deps:
        return None
    return {
        "unreachable_modules": [m.get("path", m) if isinstance(m, dict) else m for m in unreachable],
        "unused_dependencies": unused_deps,
    }


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
    return names or None
