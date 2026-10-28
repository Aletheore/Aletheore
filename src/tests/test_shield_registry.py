import io
import json
import socket
import urllib.error
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
