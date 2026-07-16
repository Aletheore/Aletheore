import json
from pathlib import Path


def _history_dir(repo_path: Path) -> Path:
    return repo_path / ".veridion" / "history"


def _rotate(history_dir: Path, keep: int) -> None:
    snapshots = sorted(history_dir.glob("*.json"))
    excess = len(snapshots) - keep
    if excess <= 0:
        return
    for path in snapshots[:excess]:
        path.unlink()


def _save_json_with_rotation(data: dict, directory: Path, timestamp: str, keep: int) -> Path:
    directory.mkdir(parents=True, exist_ok=True)

    safe_name = timestamp.replace(":", "-")
    snapshot_path = directory / f"{safe_name}.json"
    suffix = 1
    while snapshot_path.exists():
        snapshot_path = directory / f"{safe_name}-{suffix}.json"
        suffix += 1

    snapshot_path.write_text(json.dumps(data, indent=2))
    _rotate(directory, keep)
    return snapshot_path


def save_snapshot(evidence: dict, repo_path: Path, keep: int = 20) -> Path:
    return _save_json_with_rotation(evidence, _history_dir(repo_path), evidence["scanned_at"], keep)


def list_snapshots(repo_path: Path) -> list[Path]:
    history_dir = _history_dir(repo_path)
    if not history_dir.exists():
        return []
    return sorted(history_dir.glob("*.json"))


def _identity_key(finding: dict, fields: tuple[str, ...]) -> tuple:
    return tuple(finding.get(field) for field in fields)


def _new_and_resolved(
    old_findings: list[dict], new_findings: list[dict], fields: tuple[str, ...]
) -> tuple[list[dict], list[dict]]:
    old_keys = {_identity_key(f, fields) for f in old_findings}
    new_keys = {_identity_key(f, fields) for f in new_findings}
    new_only = [f for f in new_findings if _identity_key(f, fields) not in old_keys]
    resolved = [f for f in old_findings if _identity_key(f, fields) not in new_keys]
    return new_only, resolved


def _endpoint_block(evidence: dict) -> dict:
    return evidence["repository"].get(
        "api_endpoints", {"checked": False, "reason": "not present in older evidence", "endpoints": []}
    )


def _compute_curated_diff(old: dict, new: dict) -> dict:
    result: dict = {}
    caveats = []

    old_vuln_checked = old["security"]["dependency_vulnerabilities"]["checked"]
    new_vuln_checked = new["security"]["dependency_vulnerabilities"]["checked"]
    if old_vuln_checked != new_vuln_checked:
        caveats.append(
            "dependency-vulnerability checking state changed between scans "
            f"(was checked={old_vuln_checked}, now checked={new_vuln_checked}) - "
            "new/resolved vulnerability findings below may reflect checking being "
            "toggled on/off, not necessarily real changes"
        )

    old_history_scanned = old["security"]["secrets"]["history_scanned_commits"] > 0
    new_history_scanned = new["security"]["secrets"]["history_scanned_commits"] > 0
    if old_history_scanned != new_history_scanned:
        caveats.append(
            "git-history secret scanning state changed between scans "
            f"(was scanned={old_history_scanned}, now scanned={new_history_scanned}) - "
            "new/resolved history secret findings below may reflect scanning being "
            "toggled on/off, not necessarily real changes"
        )

    old_api_endpoints = _endpoint_block(old)
    new_api_endpoints = _endpoint_block(new)
    old_endpoints_checked = old_api_endpoints["checked"]
    new_endpoints_checked = new_api_endpoints["checked"]
    if old_endpoints_checked != new_endpoints_checked:
        caveats.append(
            "API endpoint mapping state changed between scans "
            f"(was checked={old_endpoints_checked}, now checked={new_endpoints_checked}) - "
            "new/resolved endpoint findings below may reflect mapping being toggled on/off, "
            "not necessarily real changes"
        )

    if caveats:
        result["caveats"] = caveats

    new_secrets, resolved_secrets = _new_and_resolved(
        old["security"]["secrets"]["findings"],
        new["security"]["secrets"]["findings"],
        ("path", "pattern", "match_preview"),
    )
    result["secrets"] = {"new": new_secrets, "resolved": resolved_secrets}

    new_history_secrets, resolved_history_secrets = _new_and_resolved(
        old["security"]["secrets"]["history_findings"],
        new["security"]["secrets"]["history_findings"],
        ("commit", "path", "pattern"),
    )
    result["history_secrets"] = {"new": new_history_secrets, "resolved": resolved_history_secrets}

    new_vulns, resolved_vulns = _new_and_resolved(
        old["security"]["dependency_vulnerabilities"]["findings"],
        new["security"]["dependency_vulnerabilities"]["findings"],
        ("ecosystem", "package", "advisory_id"),
    )
    result["vulnerabilities"] = {"new": new_vulns, "resolved": resolved_vulns}

    new_violations, resolved_violations = _new_and_resolved(
        old["architecture"]["layer_violations"]["violations"],
        new["architecture"]["layer_violations"]["violations"],
        ("from", "to"),
    )
    result["layer_violations"] = {"new": new_violations, "resolved": resolved_violations}

    new_endpoints, resolved_endpoints = _new_and_resolved(
        old_api_endpoints["endpoints"],
        new_api_endpoints["endpoints"],
        ("method", "path"),
    )
    result["endpoints"] = {"new": new_endpoints, "resolved": resolved_endpoints}

    result["aggregate_deltas"] = {
        "module_count": len(new["repository"]["modules"]) - len(old["repository"]["modules"]),
        "dependency_graph_edge_count": (
            len(new["repository"]["dependency_graph"]["edges"])
            - len(old["repository"]["dependency_graph"]["edges"])
        ),
        "total_commits": new["git"].get("total_commits", 0) - old["git"].get("total_commits", 0),
    }

    return result


def _flatten(obj, prefix: str = "") -> dict:
    flat: dict = {}
    if isinstance(obj, dict):
        for key, val in obj.items():
            new_prefix = f"{prefix}.{key}" if prefix else key
            flat.update(_flatten(val, new_prefix))
    elif isinstance(obj, list):
        for idx, val in enumerate(obj):
            flat.update(_flatten(val, f"{prefix}[{idx}]"))
    else:
        flat[prefix] = obj
    return flat


def _compute_full_diff(old: dict, new: dict) -> dict:
    old_flat = _flatten(old)
    new_flat = _flatten(new)

    added = [
        {"path": path, "value": value}
        for path, value in sorted(new_flat.items())
        if path not in old_flat
    ]
    removed = [
        {"path": path, "value": value}
        for path, value in sorted(old_flat.items())
        if path not in new_flat
    ]
    changed = [
        {"path": path, "old_value": old_flat[path], "new_value": new_flat[path]}
        for path in sorted(old_flat.keys() & new_flat.keys())
        if old_flat[path] != new_flat[path]
    ]

    return {"added": added, "removed": removed, "changed": changed}


def compute_diff(old: dict, new: dict, full: bool = False) -> dict:
    if full:
        return _compute_full_diff(old, new)
    return _compute_curated_diff(old, new)
