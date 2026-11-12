import http.client
import io
import json
import os
import socket
import urllib.error
import urllib.request

import pytest
from datetime import datetime, timedelta, timezone

from aletheore.network_plan import shield_network_plan
from aletheore.shield import nearmiss
from aletheore.shield.registry import lookup_package

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


class _Response:
    def __init__(self, body):
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def read(self, *args):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(url, code):
    return urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))


class _Opener:
    """Records requests; returns/raises whatever it was given."""

    def __init__(self, result):
        self.result = result
        self.requests = []
        self.timeouts = []

    def __call__(self, request, timeout=None, **kwargs):
        self.requests.append(request)
        self.timeouts.append(timeout)
        if isinstance(self.result, BaseException):
            raise self.result
        return _Response(self.result)


def _lookup(tmp_path, eco, name, result, **kwargs):
    opener = _Opener(result)
    info = lookup_package(eco, name, cache_path=tmp_path / "reg.json", opener=opener, now=NOW, **kwargs)
    return info, opener


def test_pypi_404_means_not_exists(tmp_path):
    info, opener = _lookup(tmp_path, "PyPI", "no-such-pkg", _http_error("u", 404))
    assert info.exists is False
    assert opener.requests[0].full_url == "https://pypi.org/pypi/no-such-pkg/json"
    assert opener.requests[0].get_header("User-agent") == "aletheore-shield"


def test_pypi_200_returns_first_published(tmp_path):
    body = {
        "releases": {
            "2.0": [{"upload_time_iso_8601": "2020-05-01T10:00:00.000000Z"}],
            "1.0": [
                {"upload_time_iso_8601": "2019-01-02T03:04:05.123456Z"},
                {"upload_time_iso_8601": "2019-01-03T00:00:00Z"},
            ],
            "0.0": [],
        }
    }
    info, _ = _lookup(tmp_path, "PyPI", "demo", body)
    assert info.exists is True
    assert info.first_published == datetime(2019, 1, 2, 3, 4, 5, 123456, tzinfo=timezone.utc)
    assert info.reason is None


def test_npm_404_and_200(tmp_path):
    info, _ = _lookup(tmp_path, "npm", "gone-pkg", _http_error("u", 404))
    assert info.exists is False

    info, opener = _lookup(
        tmp_path, "npm", "@scope/pkg", {"time": {"created": "2021-03-04T05:06:07.000Z", "1.0.0": "2021-04-01T00:00:00.000Z"}}
    )
    assert info.exists is True
    assert info.first_published == datetime(2021, 3, 4, 5, 6, 7, tzinfo=timezone.utc)
    assert opener.requests[0].full_url == "https://registry.npmjs.org/@scope%2Fpkg"


def test_timeout_and_network_error_mean_not_checked(tmp_path):
    for error in (socket.timeout("timed out"), urllib.error.URLError("no route"), ConnectionResetError()):
        info, _ = _lookup(tmp_path, "PyPI", "demo", error)
        assert info.exists is None
        assert "offline" in info.reason or "timed out" in info.reason


def test_rate_limited_429_means_not_checked(tmp_path):
    info, _ = _lookup(tmp_path, "npm", "demo", _http_error("u", 429))
    assert info.exists is None
    assert "rate limited" in info.reason


def test_server_errors_and_malformed_json_mean_not_checked(tmp_path):
    for result in (_http_error("u", 503), _http_error("u", 403), b"<html>nope", b"[1, 2]"):
        info, _ = _lookup(tmp_path, "PyPI", "demo", result)
        assert info.exists is None
        assert info.reason


def test_cache_hit_makes_no_request_and_errors_are_not_cached(tmp_path):
    first, opener = _lookup(tmp_path, "PyPI", "Demo_Pkg", {"releases": {"1": [{"upload_time_iso_8601": "2020-01-01T00:00:00Z"}]}})
    assert len(opener.requests) == 1
    again = _Opener(RuntimeError("must not be called"))
    cached = lookup_package(
        "PyPI", "demo-pkg", cache_path=tmp_path / "reg.json", opener=again, now=NOW + timedelta(hours=23)
    )
    assert again.requests == []
    assert cached == first

    expired = _Opener({"releases": {}})
    lookup_package("PyPI", "demo-pkg", cache_path=tmp_path / "reg.json", opener=expired, now=NOW + timedelta(hours=25))
    assert len(expired.requests) == 1

    err_path = tmp_path / "err.json"
    lookup_package("npm", "x-pkg", cache_path=err_path, opener=_Opener(_http_error("u", 500)), now=NOW)
    retry = _Opener({"time": {}})
    assert lookup_package("npm", "x-pkg", cache_path=err_path, opener=retry, now=NOW).exists is True
    assert len(retry.requests) == 1


def test_definite_404_is_cached_and_corrupt_cache_is_ignored(tmp_path):
    path = tmp_path / "c.json"
    lookup_package("npm", "ghost", cache_path=path, opener=_Opener(_http_error("u", 404)), now=NOW)
    again = _Opener(RuntimeError("no"))
    assert lookup_package("npm", "ghost", cache_path=path, opener=again, now=NOW).exists is False
    assert again.requests == []

    path.write_text("{not json")
    fresh = _Opener({"time": {}})
    assert lookup_package("npm", "ghost", cache_path=path, opener=fresh, now=NOW).exists is True
    assert len(fresh.requests) == 1


def test_default_timeout_is_three_seconds(tmp_path):
    _, opener = _lookup(tmp_path, "npm", "demo", {"time": {}})
    assert opener.timeouts == [3.0]


def test_near_miss_flags_reqeusts_but_not_requests():
    assert nearmiss.near_miss("PyPI", "reqeusts") == "requests"
    assert nearmiss.near_miss("PyPI", "requestss") == "requests"
    assert nearmiss.near_miss("PyPI", "Reque_sts") == "requests"  # separator insertion is one edit
    assert nearmiss.near_miss("PyPI", "reqXXsts") is None
    assert nearmiss.near_miss("PyPI", "requests") is None
    assert nearmiss.near_miss("PyPI", "Requests") is None
    assert nearmiss.near_miss("PyPI", "reqs") is None  # shorter than 5 characters
    assert nearmiss.near_miss("npm", "expres") == "express"
    assert nearmiss.near_miss("npm", "react") is None


def test_popular_list_loads_offline():
    data = nearmiss.load_popular()
    assert "requests" in data["pypi"]
    assert "react" in data["npm"]
    assert data["source"] and data["generated"]


def test_shield_network_plan_lists_only_package_names_and_the_off_switch():
    plan = shield_network_plan(registry_lookups=True)
    assert [entry["service"] for entry in plan] == ["PyPI registry", "npm registry"]
    for entry in plan:
        assert set(entry) == {"service", "enabled", "sends", "disable"}
        assert entry["enabled"] is True
        assert "package name" in entry["sends"]
        assert "version" in entry["sends"] and "never" in entry["sends"].lower()
        assert "ALETHEORE_SHIELD_OFFLINE=1" in entry["disable"]
        assert "registry_lookups" in entry["disable"]
    assert all(not e["enabled"] for e in shield_network_plan(registry_lookups=False))


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("test tried to reach the network")

    monkeypatch.setattr(urllib.request, "urlopen", boom)


class _BrokenBody:
    def __init__(self, error):
        self.error = error

    def read(self, *args):
        raise self.error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_http_client_errors_mean_not_checked(tmp_path):
    for error in (http.client.IncompleteRead(b"x"), http.client.BadStatusLine("x"), http.client.InvalidURL("x"), ValueError("x")):
        info = lookup_package(
            "PyPI", "demo", cache_path=tmp_path / "c.json", opener=lambda r, timeout=None, e=error: _BrokenBody(e), now=NOW
        )
        assert info.exists is None
        assert info.reason == "registry connection failed"


@pytest.mark.parametrize(
    "eco,name",
    [("npm", ""), ("npm", "."), ("npm", ".."), ("npm", "a\x00b"), ("npm", "a\nb"), ("npm", "a\rb"), ("npm", "a\\b"),
     ("npm", "a b"), ("npm", "a/b"), ("npm", "@a/b/c"), ("npm", "@/b"), ("npm", "@a/"), ("PyPI", "@a/b"), ("PyPI", "a/b")],
)
def test_invalid_names_make_no_request(tmp_path, eco, name):
    opener = _Opener(RuntimeError("must not be called"))
    info = lookup_package(eco, name, cache_path=tmp_path / "c.json", opener=opener, now=NOW)
    assert (info.exists, info.reason) == (None, "invalid package name")
    assert opener.requests == []


def test_oversized_response_is_not_checked(tmp_path):
    from aletheore.shield import registry

    info, _ = _lookup(tmp_path, "npm", "big", b"x" * (registry.MAX_BODY_BYTES + 1))
    assert (info.exists, info.reason) == (None, "response too large")


def test_npm_cache_key_preserves_case_and_expired_entries_are_pruned(tmp_path):
    path = tmp_path / "c.json"
    lookup_package("npm", "JSONStream", cache_path=path, opener=_Opener({"time": {}}), now=NOW)
    lookup_package("npm", "jsonstream", cache_path=path, opener=_Opener(_http_error("u", 404)), now=NOW)
    cache = json.loads(path.read_text())
    assert cache["npm:JSONStream"]["exists"] is True and cache["npm:jsonstream"]["exists"] is False
    lookup_package("npm", "other", cache_path=path, opener=_Opener({"time": {}}), now=NOW + timedelta(hours=30))
    assert set(json.loads(path.read_text())) == {"npm:other"}
    assert [p.name for p in tmp_path.iterdir() if p.name not in ("c.json", "c.lock")] == []


def _fresh_entry(minutes_ago):
    return {"cached_at": (NOW - timedelta(minutes=minutes_ago)).isoformat(), "exists": True, "first_published": None}


def test_registry_cache_stays_bounded_and_keeps_the_newest_entries(tmp_path):
    from aletheore.shield import registry

    cache = tmp_path / "reg.json"
    many = registry.MAX_CACHE_ENTRIES + 500
    cache.write_text(json.dumps({f"PyPI:p{i}": _fresh_entry(i % 600) for i in range(many)}), encoding="utf-8")
    _lookup(tmp_path, "PyPI", "newname", _http_error("u", 404))
    data = json.loads(cache.read_text(encoding="utf-8"))
    assert len(data) == registry.MAX_CACHE_ENTRIES
    assert "PyPI:newname" in data
    kept_ages = sorted(NOW - datetime.fromisoformat(v["cached_at"]) for k, v in data.items() if k != "PyPI:newname")
    dropped_youngest = min(
        NOW - datetime.fromisoformat(_fresh_entry(i % 600)["cached_at"])
        for i in range(many) if f"PyPI:p{i}" not in data
    )
    assert kept_ages[-1] <= dropped_youngest  # the oldest entries went first


@pytest.mark.parametrize(
    "content",
    ["", "{", "[1, 2]", "null", json.dumps({"PyPI:x": [1]}), json.dumps({"PyPI:x": {"cached_at": 5, "exists": 1}})],
)
def test_corrupt_registry_cache_is_ignored_and_replaced(tmp_path, content):
    cache = tmp_path / "reg.json"
    cache.write_text(content, encoding="utf-8")
    info, opener = _lookup(tmp_path, "PyPI", "x", _http_error("u", 404))
    assert info.exists is False and len(opener.requests) == 1
    assert set(json.loads(cache.read_text(encoding="utf-8"))) == {"PyPI:x"}


def test_registry_cache_write_failure_leaves_no_temporary_file(tmp_path, monkeypatch):
    import os

    def refuse(*a, **k):
        raise PermissionError("in use")

    monkeypatch.setattr(os, "replace", refuse)
    info, _ = _lookup(tmp_path, "PyPI", "x", _http_error("u", 404))
    assert info.exists is False
    assert [p.name for p in tmp_path.iterdir() if p.name != "reg.lock"] == []  # the OS lock file stays, empty


def test_concurrent_registry_cache_writers_keep_every_entry(tmp_path, monkeypatch):
    import threading
    import time

    from aletheore.shield import registry

    real = registry._read_cache

    def slow(path):
        data = real(path)
        time.sleep(0.03)
        return data

    monkeypatch.setattr(registry, "_read_cache", slow)
    # The cache lock is best effort (0.25 s, then no caching); a slow CI runner can exceed that with four writers
    # queued, so wait longer here: this test is about every writer's entry surviving the merge, not lock timing.
    from aletheore.shield import locking

    monkeypatch.setattr(registry, "file_lock", lambda path, timeout=0.25: locking.file_lock(path, timeout=10.0))
    barrier = threading.Barrier(4)

    def go(index):
        barrier.wait()
        _lookup(tmp_path, "PyPI", f"name{index}", _http_error("u", 404))

    threads = [threading.Thread(target=go, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert set(json.loads((tmp_path / "reg.json").read_text(encoding="utf-8"))) == {f"PyPI:name{i}" for i in range(4)}


def test_near_miss_compares_only_within_the_names_own_ecosystem():
    assert nearmiss.near_miss("PyPI", "expres") is None  # npm's "express" is not a PyPI near miss
    assert nearmiss.near_miss("npm", "reqeusts") is None  # PyPI's "requests" is not an npm near miss
    for ecosystem in ("crates.io", "Go", "RubyGems", "pypi", "NPM", "", "Maven"):
        assert nearmiss.near_miss(ecosystem, "expres") is None
        assert nearmiss.near_miss(ecosystem, "reqeusts") is None


def test_popular_packages_file_is_in_the_form_its_regeneration_script_writes():
    # .github/scripts/regenerate_popular_packages.py rebuilds this file byte for byte from pinned upstream commits
    # (`--check` verifies it); a hand edit would break that form or the 1000-per-ecosystem shape.
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "aletheore" / "data" / "popular_packages.json"
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert text == json.dumps(data, indent=0) + "\n"
    assert set(data) == {"source", "generated", "pypi", "npm"}
    for ecosystem in ("pypi", "npm"):
        assert len(data[ecosystem]) == 1000 == len(set(data[ecosystem]))


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_registry_cache_file_is_private(tmp_path):
    import stat

    _lookup(tmp_path, "PyPI", "no-such-pkg", _http_error("u", 404))
    assert stat.S_IMODE((tmp_path / "reg.json").stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes; on Windows the folder's ACL applies")
def test_an_older_world_readable_registry_cache_is_tightened_on_the_next_write(tmp_path):
    import stat

    cache = tmp_path / "reg.json"
    cache.write_text("{}", encoding="utf-8")
    cache.chmod(0o644)
    _lookup(tmp_path, "PyPI", "no-such-pkg", _http_error("u", 404))
    assert stat.S_IMODE(cache.stat().st_mode) == 0o600


def test_importing_the_registry_does_not_load_the_certificate_bundle():
    """Hooks import this module on every call, and the post-edit hook looks nothing up: the TLS context is built when a
    lookup first needs it, not at import (it cost 0.6 s of a 2 s hook limit on a Windows machine)."""
    import subprocess
    import sys

    code = "import aletheore.shield.registry as r, sys; print(r._ssl_context is None, 'certifi' in sys.modules)"
    done = subprocess.run([sys.executable, "-P", "-c", code], capture_output=True, text=True, timeout=60)
    assert done.stdout.split() == ["True", "False"], done.stdout + done.stderr


def test_the_tls_context_is_built_once_on_first_use():
    from aletheore.shield import registry

    first = registry._tls_context()
    assert registry._tls_context() is first


def test_importing_the_vulnerability_scanner_does_not_load_the_certificate_bundle():
    """Shield's guard imports it when a manifest changes, and the post-edit hook has a 2 s limit."""
    import subprocess
    import sys

    code = "import aletheore.vulnerabilities as v, sys; print(v._ssl_context is None, 'certifi' in sys.modules)"
    done = subprocess.run([sys.executable, "-P", "-c", code], capture_output=True, text=True, timeout=60)
    assert done.stdout.split() == ["True", "False"], done.stdout + done.stderr
