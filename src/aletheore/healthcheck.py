import json
import re
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import certifi

from aletheore.history import _save_json_with_rotation

_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

_PATH_PARAM_PATTERNS = (
    re.compile(r"<[^>]+>"),
    re.compile(r"\{[^}]+\}"),
    re.compile(r":[A-Za-z_][A-Za-z0-9_]*"),
)
MAX_BODY_BYTES_FOR_SHAPE = 65_536


def _substitute_path_params(path: str) -> tuple[str, bool]:
    substituted = path
    had_params = False
    for pattern in _PATH_PARAM_PATTERNS:
        if pattern.search(substituted):
            had_params = True
            substituted = pattern.sub("1", substituted)
    return substituted, had_params


def _response_shape(response) -> list[str] | None:
    content_type = response.headers.get("Content-Type", "")
    if "application/json" not in content_type:
        return None
    try:
        raw = response.read(MAX_BODY_BYTES_FOR_SHAPE)
        data = json.loads(raw)
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
    if isinstance(data, dict):
        return sorted(data.keys())
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return sorted(data[0].keys())
    return None


def run_healthcheck(endpoints: list[dict], base_url: str, timeout: float = 5.0) -> dict:
    results: list[dict] = []

    for endpoint in endpoints:
        if endpoint.get("unresolved"):
            results.append(
                {
                    "method": endpoint.get("method"),
                    "path": endpoint["path"],
                    "skipped": True,
                    "reason": "unresolved routing indirection (include/mount), not a concrete endpoint",
                }
            )
            continue

        method = endpoint.get("method")
        if method not in ("GET", "ANY"):
            results.append(
                {
                    "method": method,
                    "path": endpoint["path"],
                    "skipped": True,
                    "reason": "only GET is health-checked",
                }
            )
            continue

        resolved_path, had_params = _substitute_path_params(endpoint["path"])
        url = base_url.rstrip("/") + "/" + resolved_path.lstrip("/")
        entry = {
            "method": "GET",
            "path": endpoint["path"],
            "note": (
                "path contains parameters, tested with placeholder value(s)"
                if had_params
                else None
            ),
        }

        start = time.monotonic()
        try:
            request = urllib.request.Request(url)
            with urllib.request.urlopen(
                request, timeout=timeout, context=_SSL_CONTEXT
            ) as response:
                entry["status_code"] = response.status
                entry["reachable"] = True
                entry["response_shape"] = _response_shape(response)
        except urllib.error.HTTPError as exc:
            entry["status_code"] = exc.code
            entry["reachable"] = True
            entry["response_shape"] = None
        except (urllib.error.URLError, TimeoutError, OSError):
            entry["status_code"] = None
            entry["reachable"] = False
            entry["response_shape"] = None
        entry["latency_ms"] = round((time.monotonic() - start) * 1000, 1)
        results.append(entry)

    return {
        "base_url": base_url,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }


def _healthchecks_dir(repo_path: Path) -> Path:
    return repo_path / ".aletheore" / "healthchecks"


def save_healthcheck(result: dict, repo_path: Path, keep: int = 20) -> Path:
    return _save_json_with_rotation(
        result, _healthchecks_dir(repo_path), result["checked_at"], keep
    )
