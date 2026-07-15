import json
from datetime import datetime, timezone
from pathlib import Path

from veridion.architecture import build_clusters, detect_layer_violations
from veridion.git_intel.analyzer import analyze_git
from veridion.scanner.detect import (
    detect_ai_usage,
    detect_build_tools,
    detect_frameworks,
    detect_languages,
    detect_monorepo,
)
from veridion.scanner.graph import build_module_graph
from veridion.secrets import find_secrets
from veridion.vulnerabilities import check_vulnerabilities as check_dependency_vulnerabilities

EVIDENCE_VERSION = "0.1.0"


def scan_repository(repo_path: Path, check_vulnerabilities: bool = True) -> dict:
    repo_path = repo_path.resolve()

    languages = detect_languages(repo_path)
    frameworks = detect_frameworks(repo_path)
    ai_usage = detect_ai_usage(repo_path)
    build_tools = detect_build_tools(repo_path)
    monorepo = detect_monorepo(repo_path)
    modules, dependency_graph, unparseable_files = build_module_graph(repo_path)
    git_data = analyze_git(repo_path)
    secrets_data = find_secrets(repo_path)
    clusters, cross_cluster_edges = build_clusters(dependency_graph)
    layer_violations = detect_layer_violations(dependency_graph)

    if check_vulnerabilities:
        vulnerabilities_data = check_dependency_vulnerabilities(repo_path)
    else:
        vulnerabilities_data = {
            "checked": False,
            "reason": "skipped (--no-check-vulnerabilities)",
            "findings": [],
        }

    return {
        "veridion_version": EVIDENCE_VERSION,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "repo_path": str(repo_path),
        "repository": {
            "languages": languages,
            "frameworks": frameworks,
            "ai_usage": ai_usage,
            "build_tools": build_tools,
            "monorepo": monorepo,
            "modules": modules,
            "dependency_graph": dependency_graph,
            "unparseable_files": unparseable_files,
        },
        "git": git_data,
        "security": {
            "secrets": secrets_data,
            "dependency_vulnerabilities": vulnerabilities_data,
        },
        "architecture": {
            "clusters": clusters,
            "cross_cluster_edges": cross_cluster_edges,
            "layer_violations": layer_violations,
        },
    }


def write_evidence(evidence: dict, repo_path: Path) -> Path:
    veridion_dir = repo_path / ".veridion"
    veridion_dir.mkdir(parents=True, exist_ok=True)
    output_path = veridion_dir / "evidence.json"
    output_path.write_text(json.dumps(evidence, indent=2))
    return output_path
