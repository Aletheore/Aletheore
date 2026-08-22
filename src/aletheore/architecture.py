import json
from pathlib import Path

import networkx as nx
from networkx.algorithms.community import greedy_modularity_communities

from aletheore.repo_config import load_repo_config
from aletheore.search_index import _is_test_path


def load_architecture_config(repo_path: Path) -> dict | None:
    # None (not a config with empty defaults) means "no usable config" - no
    # file, or a file that isn't valid JSON. load_repo_config itself is
    # deliberately never-erroring (falls back to defaults on bad JSON, since
    # its other callers need a config for every scan regardless), so the
    # malformed-JSON case is checked here first to preserve this function's
    # existing None-on-malformed contract.
    config_file = repo_path / ".aletheore.json"
    if not config_file.exists():
        return None
    try:
        data = json.loads(config_file.read_text(encoding="utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    config = load_repo_config(repo_path)
    result = {
        "layer_markers": config["layer_markers"],
        "cluster_resolution": config["cluster_resolution"],
    }
    if config["dead_code_entry_points"]:
        result["dead_code_entry_points"] = config["dead_code_entry_points"]
    return result


LAYER_FOLDER_MARKERS = {
    "domain": 0,
    "core": 0,
    "entities": 0,
    "application": 1,
    "services": 1,
    "use_cases": 1,
    "infrastructure": 2,
    "infra": 2,
    "adapters": 2,
    "api": 2,
    "routers": 2,
    "web": 2,
    "controllers": 2,
}


def build_clusters(dependency_graph: dict, resolution: float = 1.0) -> tuple[list[dict], list[dict]]:
    """Group dependency_graph's modules into communities by import density.

    Test files are excluded before clustering, not after: they pollute
    architecture grouping the same way they polluted retrieval accuracy
    (see search_index._is_test_path's own measurement). Reproduced directly
    on AutoMapper/AutoMapper - 420 of 513 dependency-graph nodes (82%) are
    test files, and clustering them alongside real source produced 119
    subsystems for 512 files instead of a handful of meaningful ones,
    because modularity clustering has no notion of "this node doesn't count
    toward the architecture" - every test file becomes its own small
    community or drags a real one apart. Every consumer of these clusters
    (AIRview subsystems, the dashboard's dependency graph, aletheore_cluster)
    wants architecture, not a test suite's own internal structure.
    """
    nodes = [n for n in dependency_graph["nodes"] if not _is_test_path(n)]
    kept = set(nodes)
    edges = [
        (a, b) for a, b in dependency_graph["edges"] if a in kept and b in kept
    ]

    graph = nx.Graph()
    graph.add_nodes_from(nodes)
    graph.add_edges_from(edges)

    communities = list(greedy_modularity_communities(graph, resolution=resolution))

    cluster_of: dict[str, int] = {}
    clusters = []
    for cluster_id, community in enumerate(communities):
        modules = sorted(community)
        for module in modules:
            cluster_of[module] = cluster_id
        clusters.append({"id": cluster_id, "modules": modules, "internal_edges": 0})

    for a, b in edges:
        if cluster_of.get(a) is not None and cluster_of.get(a) == cluster_of.get(b):
            clusters[cluster_of[a]]["internal_edges"] += 1

    cross_pairs: dict[tuple[int, int], list[list[str]]] = {}
    for a, b in edges:
        ca, cb = cluster_of.get(a), cluster_of.get(b)
        if ca is None or cb is None or ca == cb:
            continue
        cross_pairs.setdefault((ca, cb), []).append([a, b])

    cross_cluster_edges = [
        {"from_cluster": ca, "to_cluster": cb, "count": len(edges), "edges": edges}
        for (ca, cb), edges in sorted(cross_pairs.items())
    ]

    return clusters, cross_cluster_edges


def _classify_module_rank(rel_path: str, markers: dict[str, int]) -> tuple[str, int] | None:
    parts = Path(rel_path).parts
    for part in parts[:-1]:
        if part in markers:
            return part, markers[part]
    return None


def detect_layer_violations(
    dependency_graph: dict, custom_markers: dict[str, int] | None = None
) -> dict:
    effective_markers = {**LAYER_FOLDER_MARKERS, **(custom_markers or {})}

    classifications: dict[str, tuple[str, int]] = {}
    for node in dependency_graph["nodes"]:
        result = _classify_module_rank(node, effective_markers)
        if result is not None:
            classifications[node] = result

    distinct_ranks = {rank for _, rank in classifications.values()}

    layer_folders: dict[str, set[str]] = {}
    for node, (name, _rank) in classifications.items():
        parts = Path(node).parts
        idx = parts.index(name)
        folder = str(Path(*parts[: idx + 1]))
        layer_folders.setdefault(name, set()).add(folder)

    layers = [
        {"name": name, "rank": effective_markers[name], "folders": sorted(folders)}
        for name, folders in sorted(layer_folders.items())
    ]

    custom_markers_contributed = bool(custom_markers) and any(
        name in custom_markers for name in layer_folders
    )
    if len(distinct_ranks) < 2 and not custom_markers_contributed:
        return {"convention_detected": False, "layers": [], "violations": []}

    violations = []
    for from_node, to_node in dependency_graph["edges"]:
        from_info = classifications.get(from_node)
        to_info = classifications.get(to_node)
        if from_info is None or to_info is None:
            continue
        from_name, from_rank = from_info
        to_name, to_rank = to_info
        if from_rank < to_rank:
            violations.append(
                {
                    "from": from_node,
                    "to": to_node,
                    "reason": f"inner layer '{from_name}' imports outer layer '{to_name}'",
                }
            )

    return {"convention_detected": True, "layers": layers, "violations": violations}
