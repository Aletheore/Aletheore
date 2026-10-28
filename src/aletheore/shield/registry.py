"""Does this package name exist on PyPI / npm, and since when?

Only the package NAME is ever sent (never a version, repo name or source), with a
fixed User-Agent. Anything that is not a definite HTTP 404 yields exists=None
("could not check"); this module never reports a package as missing on a guess.
"""

import json
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import certifi

from aletheore.user_paths import user_home

DEFAULT_REGISTRY_CACHE_PATH = user_home() / ".cache" / "aletheore" / "shield-registry-cache.json"
CACHE_TTL = timedelta(hours=24)
USER_AGENT = "aletheore-shield"
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


@dataclass(frozen=True)
class RegistryInfo:
    exists: bool | None  # None: could not be checked
    first_published: datetime | None
    reason: str | None


def _norm(ecosystem: str, name: str) -> str:
    if ecosystem == "PyPI":
        return re.sub(r"[-_.]+", "-", name).lower()
    return name.lower()


def _url(ecosystem: str, name: str) -> str:
    if ecosystem == "PyPI":
        return f"https://pypi.org/pypi/{urllib.parse.quote(name, safe='')}/json"
    # Scoped names keep the leading "@" but encode the slash: @a/b -> @a%2Fb.
    return f"https://registry.npmjs.org/{urllib.parse.quote(name, safe='@')}"


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _first_published(ecosystem: str, data: dict) -> datetime | None:
    if ecosystem == "npm":
        time_map = data.get("time")
        return _parse_time(time_map.get("created")) if isinstance(time_map, dict) else None
    releases = data.get("releases")
    if not isinstance(releases, dict):
        return None
    stamps = []
    for files in releases.values():
        for entry in files if isinstance(files, list) else []:
            parsed = _parse_time(entry.get("upload_time_iso_8601")) if isinstance(entry, dict) else None
            if parsed:
                stamps.append(parsed)
    return min(stamps) if stamps else None


def _read_cache(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _from_cache(entry, now: datetime) -> RegistryInfo | None:
    try:
        cached_at = _parse_time(entry["cached_at"])
        exists = entry["exists"]
        if cached_at is None or not isinstance(exists, bool) or not (timedelta(0) <= now - cached_at < CACHE_TTL):
            return None
        first = entry.get("first_published")
        parsed_first = _parse_time(first) if first else None
    except (KeyError, TypeError):
        return None
    return RegistryInfo(exists, parsed_first, None)


def _write_cache(path: Path, key: str, info: RegistryInfo, now: datetime) -> None:
    cache = _read_cache(path)
    cache[key] = {
        "cached_at": now.isoformat(),
        "exists": info.exists,
        "first_published": info.first_published.isoformat() if info.first_published else None,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(cache), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass  # caching is best effort


def _default_opener(request, timeout=None):
    return urllib.request.urlopen(request, timeout=timeout, context=_SSL_CONTEXT)


def lookup_package(
    ecosystem: str,
    name: str,
    *,
    timeout: float = 3.0,
    cache_path: Path | None = None,
    opener: Callable | None = None,
    now: datetime | None = None,
) -> RegistryInfo:
    now = now or datetime.now(timezone.utc)
    path = cache_path if cache_path is not None else DEFAULT_REGISTRY_CACHE_PATH
    key = f"{ecosystem}:{_norm(ecosystem, name)}"
    hit = _from_cache(_read_cache(path).get(key), now)
    if hit is not None:
        return hit

    request = urllib.request.Request(_url(ecosystem, name), headers={"User-Agent": USER_AGENT})
    try:
        with (opener or _default_opener)(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        if error.code == 404:
            info = RegistryInfo(False, None, None)
            _write_cache(path, key, info, now)
            return info
        if error.code == 429:
            return RegistryInfo(None, None, "rate limited")
        return RegistryInfo(None, None, f"registry error (HTTP {error.code})")
    except (OSError, socket.timeout):  # URLError, timeouts, resets
        return RegistryInfo(None, None, "offline or timed out")

    try:
        data = json.loads(raw)
    except ValueError:
        return RegistryInfo(None, None, "malformed registry response")
    if not isinstance(data, dict):
        return RegistryInfo(None, None, "malformed registry response")
    info = RegistryInfo(True, _first_published(ecosystem, data), None)
    _write_cache(path, key, info, now)
    return info
