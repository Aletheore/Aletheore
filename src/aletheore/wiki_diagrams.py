"""Deterministic mermaid diagram generation for the Live Wiki.

Diagram structure (which nodes exist, which edges connect them) is derived
entirely from the scanner's own dependency graph and cluster data - never
from an LLM. This guarantees a diagram can never show a relationship that
doesn't actually exist in the code. Human-readable labels (subsystem names)
are supplied by the caller once a naming pass has run; without them, nodes
fall back to a generic "Cluster N" label so this module is independently
testable and usable before naming happens.
"""


def _mermaid_safe_label(text: str) -> str:
    return text.replace('"', "'")


def _file_to_cluster_map(clusters: list[dict]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for cluster in clusters:
        for file_path in cluster.get("modules", []):
            mapping[file_path] = cluster["id"]
    return mapping


def build_overview_diagram(evidence: dict, cluster_names: dict[int, str] | None = None) -> str:
    """One node per subsystem (cluster), edges for inter-cluster dependencies."""
    cluster_names = cluster_names or {}
    clusters = evidence.get("architecture", {}).get("clusters", [])
    edges = evidence.get("repository", {}).get("dependency_graph", {}).get("edges", [])

    file_to_cluster = _file_to_cluster_map(clusters)

    cluster_edges: set[tuple[int, int]] = set()
    for edge in edges:
        source_file, target_file = edge[0], edge[1]
        source_cluster = file_to_cluster.get(source_file)
        target_cluster = file_to_cluster.get(target_file)
        if source_cluster is None or target_cluster is None or source_cluster == target_cluster:
            continue
        cluster_edges.add((source_cluster, target_cluster))

    lines = ["flowchart TD"]
    for cluster in clusters:
        cid = cluster["id"]
        label = _mermaid_safe_label(cluster_names.get(cid, f"Cluster {cid}"))
        lines.append(f'    C{cid}["{label}"]')
    for source_id, target_id in sorted(cluster_edges):
        lines.append(f"    C{source_id} --> C{target_id}")
    return "\n".join(lines)


def build_subsystem_diagram(evidence: dict, cluster: dict) -> str:
    """One node per file in this cluster, edges for imports within it.

    Imports pointing outside the cluster (to another subsystem, or to a
    file the scanner doesn't track, e.g. a third-party package) are not
    drawn - this diagram is intentionally scoped to the subsystem's own
    internal structure, not the whole repo.
    """
    member_files = cluster.get("modules", [])
    member_set = set(member_files)
    modules_by_path = {m["path"]: m for m in evidence.get("repository", {}).get("modules", [])}

    node_ids = {path: f"N{i}" for i, path in enumerate(member_files)}

    lines = ["flowchart TD"]
    for path in member_files:
        lines.append(f'    {node_ids[path]}["{_mermaid_safe_label(path)}"]')

    drawn_edges: set[tuple[str, str]] = set()
    for path in member_files:
        module = modules_by_path.get(path)
        if module is None:
            continue
        for imported in module.get("imports", []):
            if imported not in member_set:
                continue
            edge = (path, imported)
            if edge in drawn_edges:
                continue
            drawn_edges.add(edge)
            lines.append(f"    {node_ids[path]} --> {node_ids[imported]}")

    return "\n".join(lines)
