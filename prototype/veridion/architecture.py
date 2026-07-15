import networkx as nx
from networkx.algorithms.community import greedy_modularity_communities


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
