from pathlib import Path

import networkx as nx
from networkx.algorithms.community import greedy_modularity_communities

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


def build_clusters(dependency_graph: dict) -> tuple[list[dict], list[dict]]:
    graph = nx.Graph()
    graph.add_nodes_from(dependency_graph["nodes"])
    graph.add_edges_from(dependency_graph["edges"])

    communities = list(greedy_modularity_communities(graph))

    cluster_of: dict[str, int] = {}
    clusters = []
    for cluster_id, community in enumerate(communities):
        modules = sorted(community)
        for module in modules:
            cluster_of[module] = cluster_id
        clusters.append({"id": cluster_id, "modules": modules, "internal_edges": 0})

    for a, b in dependency_graph["edges"]:
        if cluster_of.get(a) is not None and cluster_of.get(a) == cluster_of.get(b):
            clusters[cluster_of[a]]["internal_edges"] += 1

    cross_pairs: dict[tuple[int, int], list[list[str]]] = {}
    for a, b in dependency_graph["edges"]:
        ca, cb = cluster_of.get(a), cluster_of.get(b)
        if ca is None or cb is None or ca == cb:
            continue
        cross_pairs.setdefault((ca, cb), []).append([a, b])

    cross_cluster_edges = [
        {"from_cluster": ca, "to_cluster": cb, "count": len(edges), "edges": edges}
        for (ca, cb), edges in sorted(cross_pairs.items())
    ]

    return clusters, cross_cluster_edges


def _classify_module_rank(rel_path: str) -> tuple[str, int] | None:
    parts = Path(rel_path).parts
    for part in parts[:-1]:
        if part in LAYER_FOLDER_MARKERS:
            return part, LAYER_FOLDER_MARKERS[part]
    return None


def detect_layer_violations(dependency_graph: dict) -> dict:
    classifications: dict[str, tuple[str, int]] = {}
    for node in dependency_graph["nodes"]:
        result = _classify_module_rank(node)
        if result is not None:
            classifications[node] = result

    distinct_ranks = {rank for _, rank in classifications.values()}
    if len(distinct_ranks) < 2:
        return {"convention_detected": False, "layers": [], "violations": []}

    layer_folders: dict[str, set[str]] = {}
    for node, (name, _rank) in classifications.items():
        parts = Path(node).parts
        idx = parts.index(name)
        folder = str(Path(*parts[: idx + 1]))
        layer_folders.setdefault(name, set()).add(folder)

    layers = [
        {"name": name, "rank": LAYER_FOLDER_MARKERS[name], "folders": sorted(folders)}
        for name, folders in sorted(layer_folders.items())
    ]

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
