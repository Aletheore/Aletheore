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


def _ambiguous_edges(modules: list[dict]) -> set[tuple[str, str]]:
    """(source, target) pairs whose resolution was genuinely uncertain - a C#
    type-reference edge kept despite more than one file declaring that type
    name (see scanner/graph.py's _csharp_type_reference_targets). Excluded
    from diagrams, not from the underlying evidence: a diagram asserts "this
    relationship exists" more strongly than a citable-but-uncertain graph
    edge should. "inferred" edges (a source-root/prefix tiebreak among
    multiple real candidates, still likely correct) are drawn as normal -
    only a name that could equally be any of several files is excluded.
    """
    pairs: set[tuple[str, str]] = set()
    for module in modules:
        confidence = module.get("import_confidence")
        if not confidence:
            continue
        source = module.get("path")
        for target, level in confidence.items():
            if level == "ambiguous":
                pairs.add((source, target))
    return pairs


def build_overview_diagram(evidence: dict, cluster_names: dict[int, str] | None = None) -> str:
    """One node per subsystem (cluster), edges for inter-cluster dependencies.

    Clusters with zero cross-cluster edges are omitted when at least one
    other cluster does have cross-cluster edges (they still show up as
    subsystem cards elsewhere) - a codebase with many small, independent
    modules can produce dozens or hundreds of clusters, and a single
    flowchart is not a useful "how do subsystems relate" view once most of
    its nodes are floating boxes with no connections at all. But if NO
    cluster has any cross-cluster edge (a single-cluster repo, or one
    where nothing happens to import across cluster boundaries yet), there
    is no meaningful subset to narrow down to - showing every cluster is
    strictly better than showing none.

    Ambiguous edges (see _ambiguous_edges) are excluded - see
    build_subsystem_diagram's own docstring for why.
    """
    cluster_names = cluster_names or {}
    clusters = evidence.get("architecture", {}).get("clusters", [])
    modules = evidence.get("repository", {}).get("modules", [])
    edges = evidence.get("repository", {}).get("dependency_graph", {}).get("edges", [])
    ambiguous = _ambiguous_edges(modules)

    file_to_cluster = _file_to_cluster_map(clusters)

    cluster_edges: set[tuple[int, int]] = set()
    for edge in edges:
        source_file, target_file = edge[0], edge[1]
        if (source_file, target_file) in ambiguous:
            continue
        source_cluster = file_to_cluster.get(source_file)
        target_cluster = file_to_cluster.get(target_file)
        if source_cluster is None or target_cluster is None or source_cluster == target_cluster:
            continue
        cluster_edges.add((source_cluster, target_cluster))

    connected_clusters = {cid for pair in cluster_edges for cid in pair}

    lines = ["flowchart TD"]
    for cluster in clusters:
        cid = cluster["id"]
        if connected_clusters and cid not in connected_clusters:
            continue
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

    Edges flagged "ambiguous" in import_confidence (currently only C#
    type-reference edges kept despite more than one file declaring that
    type name - see scanner/graph.py's _csharp_type_reference_targets) are
    excluded here even though they remain in the underlying evidence. A
    diagram is a stronger claim than a citable graph edge - "this file
    depends on that one" read as fact, not as "probably, among a few
    candidates" - and measured on a real C#-heavy corpus (AutoMapper),
    keeping instead of dropping these added roughly a third more raw edges
    than existed before. "inferred" edges (a source-root/prefix tiebreak
    among multiple real candidates, generally still correct) are drawn as
    normal; only genuine "which-of-several-files" uncertainty is excluded.
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
        confidence = module.get("import_confidence") or {}
        for imported in module.get("imports", []):
            if imported not in member_set:
                continue
            if confidence.get(imported) == "ambiguous":
                continue
            edge = (path, imported)
            if edge in drawn_edges:
                continue
            drawn_edges.add(edge)
            lines.append(f"    {node_ids[path]} --> {node_ids[imported]}")

    return "\n".join(lines)


# mermaid erDiagram forbids most punctuation in an entity name and has no
# escape syntax for it, unlike the quoted labels build_overview_diagram can
# use for clusters. Table names are already identifiers, so this only ever
# fires on a quoted identifier containing something exotic.
def _er_safe_name(name: str) -> str:
    cleaned = "".join(char if (char.isalnum() or char == "_") else "_" for char in name)
    return cleaned or "unnamed"


def _er_safe_type(type_name: str) -> str:
    # Same constraint on the attribute type token. `DOUBLE PRECISION` and
    # `NUMERIC(10,2)` both need flattening or the diagram fails to render.
    cleaned = "".join(char if (char.isalnum() or char == "_") else "_" for char in type_name)
    return cleaned or "unknown"


def build_schema_diagram(evidence: dict, max_tables: int = 40) -> str | None:
    """A mermaid erDiagram of the database schema in evidence.

    Returns None when the schema section was not checked (unentitled or
    --no-map-schema) or found no tables - the caller renders nothing rather
    than an empty diagram frame.

    Ordering is taken from the already-sorted AIR arrays and never recomputed
    here, so the same evidence always produces byte-identical mermaid.
    """
    schema = evidence.get("repository", {}).get("database", {}).get("schema", {})
    if not schema.get("checked") or not schema.get("tables"):
        return None

    tables = schema["tables"]
    relations = schema.get("relations", [])

    # Truncation keeps a 300-table monorepo from producing a diagram no
    # browser will lay out. Tables are kept by inbound-reference count so
    # the ones that survive are the hubs the schema is actually organised
    # around - on this repo that puts `installations` first, which 41 of 42
    # relations point at. Ties break on name to stay deterministic.
    if len(tables) > max_tables:
        inbound: dict[str, int] = {}
        for relation in relations:
            inbound[relation["to_table"]] = inbound.get(relation["to_table"], 0) + 1
        tables = sorted(
            sorted(tables, key=lambda t: t["name"]),
            key=lambda t: inbound.get(t["name"], 0),
            reverse=True,
        )[:max_tables]
        tables = sorted(tables, key=lambda t: t["name"])

    kept = {table["name"] for table in tables}
    lines = ["erDiagram"]

    for table in tables:
        lines.append(f"    {_er_safe_name(table['name'])} {{")
        for column in table["columns"]:
            key = ""
            if column.get("primary_key"):
                key = " PK"
            elif any(
                r["from_table"] == table["name"] and r["from_column"] == column["name"]
                for r in relations
            ):
                key = " FK"
            lines.append(f"        {_er_safe_type(column['type'])} {_er_safe_name(column['name'])}{key}")
        lines.append("    }")

    for relation in relations:
        if relation["from_table"] not in kept or relation["to_table"] not in kept:
            continue
        # ON DELETE CASCADE means the child cannot outlive its parent, which
        # is exactly mermaid's identifying relationship (||--||). Everything
        # else is non-identifying (||--o{): the FK may be null or orphaned.
        cardinality = "||--o{" if relation.get("on_delete") != "CASCADE" else "||--||"
        label = relation.get("on_delete") or relation["from_column"]
        lines.append(
            f"    {_er_safe_name(relation['to_table'])} {cardinality} "
            f"{_er_safe_name(relation['from_table'])} : \"{_er_safe_name(label)}\""
        )

    return "\n".join(lines)
