import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from aletheore.architecture import build_clusters, detect_layer_violations, load_architecture_config
from aletheore.endpoints import map_api_endpoints
from aletheore.git_intel.analyzer import analyze_git
from aletheore.licenses import check_dependency_licenses
from aletheore.scanner.detect import (
    detect_ai_usage,
    detect_build_tools,
    detect_frameworks,
    detect_languages,
    detect_monorepo,
    detect_policy_docs,
)
from aletheore.scanner.graph import build_module_graph
from aletheore.secrets import find_secrets, find_secrets_in_history, load_secrets_baseline
from aletheore.toon_encoding import to_toon
from aletheore.vulnerabilities import check_vulnerabilities as check_dependency_vulnerabilities

EVIDENCE_VERSION = "0.1.0"


def _noop_progress(_message: str) -> None:
    pass


def _license_progress_reporter(
    report: Callable[[str], None],
) -> Callable[[int, int, str], None]:
    def on_progress(current: int, total: int, name: str) -> None:
        report(f"Checking dependency licenses: {current}/{total} ({name})")

    return on_progress


def scan_repository(
    repo_path: Path,
    check_vulnerabilities: bool = True,
    scan_git_history: bool = True,
    check_licenses: bool = True,
    map_endpoints: bool = True,
    progress: Callable[[str], None] | None = None,
) -> dict:
    report = progress or _noop_progress
    repo_path = repo_path.resolve()

    report("Detecting languages, frameworks, and build tools")
    languages = detect_languages(repo_path)
    frameworks = detect_frameworks(repo_path)
    ai_usage = detect_ai_usage(repo_path)
    policy_docs = detect_policy_docs(repo_path)
    build_tools = detect_build_tools(repo_path)
    monorepo = detect_monorepo(repo_path)

    report("Building module dependency graph (parsing source with tree-sitter)")
    modules, dependency_graph, unparseable_files = build_module_graph(repo_path)

    report("Analyzing git history and ownership")
    git_data = analyze_git(repo_path)

    report("Scanning working tree for secrets")
    secrets_baseline = load_secrets_baseline(repo_path)
    secrets_data = find_secrets(repo_path, baseline=secrets_baseline)
    if scan_git_history:
        report("Scanning git history for secrets (can be slow on large histories)")
        history_data = find_secrets_in_history(repo_path, baseline=secrets_baseline)
    else:
        history_data = {"history_scanned_commits": 0, "history_findings": []}
    secrets_data = {**secrets_data, **history_data}

    report("Clustering modules and checking layer conventions")
    architecture_config = load_architecture_config(repo_path)
    resolution = architecture_config["cluster_resolution"] if architecture_config else 1.0
    custom_markers = architecture_config["layer_markers"] if architecture_config else None
    clusters, cross_cluster_edges = build_clusters(dependency_graph, resolution=resolution)
    layer_violations = detect_layer_violations(dependency_graph, custom_markers=custom_markers)

    if check_vulnerabilities:
        report("Checking dependencies for known vulnerabilities (OSV.dev)")
        vulnerabilities_data = check_dependency_vulnerabilities(repo_path)
    else:
        vulnerabilities_data = {
            "checked": False,
            "reason": "skipped (--no-check-vulnerabilities)",
            "findings": [],
        }

    if check_licenses:
        report("Checking dependency licenses (one registry lookup per pinned dependency)")
        licenses_data = check_dependency_licenses(
            repo_path, on_progress=_license_progress_reporter(report)
        )
    else:
        licenses_data = {
            "checked": False,
            "reason": "skipped (--no-check-licenses)",
            "repo_license": {"category": "unknown", "detected_from": None},
            "findings": [],
        }

    if map_endpoints:
        report("Mapping API endpoints")
        api_endpoints_data = map_api_endpoints(repo_path)
    else:
        api_endpoints_data = {
            "checked": False,
            "reason": "skipped (--no-map-endpoints)",
            "endpoints": [],
        }

    report("Done")

    return {
        "aletheore_version": EVIDENCE_VERSION,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "repo_path": str(repo_path),
        "repository": {
            "languages": languages,
            "frameworks": frameworks,
            "ai_usage": ai_usage,
            "policy_docs": policy_docs,
            "build_tools": build_tools,
            "monorepo": monorepo,
            "modules": modules,
            "dependency_graph": dependency_graph,
            "unparseable_files": unparseable_files,
            "api_endpoints": api_endpoints_data,
        },
        "git": git_data,
        "security": {
            "secrets": secrets_data,
            "dependency_vulnerabilities": vulnerabilities_data,
            "dependency_licenses": licenses_data,
        },
        "architecture": {
            "clusters": clusters,
            "cross_cluster_edges": cross_cluster_edges,
            "layer_violations": layer_violations,
            "config_applied": architecture_config,
        },
    }


def write_evidence(evidence: dict, repo_path: Path) -> Path:
    aletheore_dir = repo_path / ".aletheore"
    aletheore_dir.mkdir(parents=True, exist_ok=True)
    output_path = aletheore_dir / "evidence.json"
    output_path.write_text(json.dumps(evidence, indent=2))

    # A second, TOON-encoded copy exists specifically for the audit command's
    # coding-agent adapter to read instead of the JSON one - the agent's own
    # token budget is what actually pays for reading this file, and evidence's
    # shape (uniform arrays of same-shaped objects almost everywhere) is
    # exactly TOON's best case. evidence.json stays the canonical machine-
    # readable copy (the dashboard's JS and any external tooling need real
    # JSON), so this is additive, not a replacement.
    (aletheore_dir / "evidence.toon").write_text(to_toon(evidence))

    return output_path
