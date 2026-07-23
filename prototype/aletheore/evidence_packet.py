"""Canonical model-neutral evidence packet for downstream model calls.

The packet reshapes deterministic scanner evidence into a compact, shared
schema that can be TOON-encoded before a higher-cost model sees it. It
does not invent new scan results.
"""

from aletheore.evidence_resolution import normalize_resolution


def build_evidence_packet(
    evidence: dict,
    cluster: dict,
    brief: dict,
    model_routing_reason: str,
    cache_eligible: bool = False,
) -> dict:
    modules_by_path = {m["path"]: m for m in evidence.get("repository", {}).get("modules", [])}
    changed_files = list(cluster.get("modules", []))

    changed_symbols: list[str] = []
    evidence_locations: list[dict] = []
    changed_dependencies: set[str] = set()

    for file_path in changed_files:
        module = modules_by_path.get(file_path)
        if module is None:
            continue
        changed_dependencies.update(module.get("imports", []))
        symbols = module.get("symbols", {})
        for group in ("functions", "classes"):
            for entry in symbols.get(group, []):
                name = entry.get("name")
                if not name:
                    continue
                changed_symbols.append(name)
                evidence_locations.append(
                    normalize_resolution(
                        kind="symbol",
                        file=file_path,
                        line=entry.get("start_line"),
                        end_line=entry.get("end_line"),
                        symbol=name,
                        confidence="exact",
                    )
                )

    return {
        "repository": evidence.get("repository", {}).get("name"),
        "base_commit": None,
        "head_commit": None,
        "changed_files": changed_files,
        "changed_symbols": changed_symbols,
        "changed_routes": [],
        "changed_dependencies": sorted(changed_dependencies),
        "owners": [],
        "evidence_locations": evidence_locations,
        "risk_classification": [],
        "graph_edges_before": None,
        "graph_edges_after": None,
        "endpoint_telemetry": None,
        "historical_failures": None,
        "test_coverage": None,
        "model_routing_reason": model_routing_reason,
        "cache_eligible": cache_eligible,
    }
