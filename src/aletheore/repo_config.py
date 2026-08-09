import fnmatch
import json
from pathlib import Path

DISABLEABLE_CHECKS = {"vulnerabilities", "licenses", "endpoints", "secrets_history"}
SEVERITY_LEVELS = ("critical", "high", "medium", "low")

DEFAULT_CONFIG = {
    "layer_markers": {},
    "cluster_resolution": 1.0,
    "dead_code_entry_points": [],
    "accepted_secrets": [],
    "ignored_paths": [],
    "disabled_checks": [],
    "severity_threshold": None,
}


def load_repo_config(repo_path: Path) -> dict:
    """Reads .aletheore.json if present, returns DEFAULT_CONFIG merged with
    whatever valid keys/types it contains. Never raises - malformed JSON, a
    missing file, or a wrong-typed value for a key all fall back to that
    key's default rather than surfacing an error mid-scan.
    """
    result = dict(DEFAULT_CONFIG)
    config_file = repo_path / ".aletheore.json"
    if not config_file.exists():
        return result

    try:
        data = json.loads(config_file.read_text(encoding="utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return result
    if not isinstance(data, dict):
        return result

    layer_markers = data.get("layer_markers", {})
    if isinstance(layer_markers, dict):
        result["layer_markers"] = layer_markers

    cluster_resolution = data.get("cluster_resolution", 1.0)
    if isinstance(cluster_resolution, (int, float)) and not isinstance(cluster_resolution, bool):
        result["cluster_resolution"] = float(cluster_resolution)

    dead_code_entry_points = data.get("dead_code_entry_points", [])
    if isinstance(dead_code_entry_points, list):
        result["dead_code_entry_points"] = [p for p in dead_code_entry_points if isinstance(p, str)]

    accepted_secrets = data.get("accepted_secrets", [])
    if isinstance(accepted_secrets, list):
        result["accepted_secrets"] = [e for e in accepted_secrets if isinstance(e, dict)]

    ignored_paths = data.get("ignored_paths", [])
    if isinstance(ignored_paths, list):
        result["ignored_paths"] = [p for p in ignored_paths if isinstance(p, str)]

    disabled_checks = data.get("disabled_checks", [])
    if isinstance(disabled_checks, list):
        result["disabled_checks"] = [c for c in disabled_checks if c in DISABLEABLE_CHECKS]

    severity_threshold = data.get("severity_threshold")
    if severity_threshold in SEVERITY_LEVELS:
        result["severity_threshold"] = severity_threshold

    return result


def is_ignored(rel_path: str, patterns: list[str]) -> bool:
    """rel_path is repo-root-relative, forward-slash separated (as_posix()).
    A pattern matches if it matches the whole path, OR if it matches any
    parent-directory prefix of the path - so "vendor/**" (or even plain
    "vendor") excludes everything under vendor/ without every file inside
    needing to match the pattern individually.
    """
    if not patterns:
        return False
    parts = rel_path.split("/")
    candidates = [rel_path] + ["/".join(parts[: i + 1]) for i in range(len(parts) - 1)]
    return any(fnmatch.fnmatch(candidate, pattern) for candidate in candidates for pattern in patterns)
