import re
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import certifi

_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

_PATH_PARAM_PATTERNS = (
    re.compile(r"<[^>]+>"),
    re.compile(r"\{[^}]+\}"),
    re.compile(r":[A-Za-z_][A-Za-z0-9_]*"),
)


def _substitute_path_params(path: str) -> tuple[str, bool]:
    substituted = path
    had_params = False
    for pattern in _PATH_PARAM_PATTERNS:
        if pattern.search(substituted):
            had_params = True
            substituted = pattern.sub("1", substituted)
    return substituted, had_params


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
        except urllib.error.HTTPError as exc:
            entry["status_code"] = exc.code
            entry["reachable"] = True
        except (urllib.error.URLError, TimeoutError, OSError):
            entry["status_code"] = None
            entry["reachable"] = False
        entry["latency_ms"] = round((time.monotonic() - start) * 1000, 1)
        results.append(entry)

    return {
        "base_url": base_url,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
