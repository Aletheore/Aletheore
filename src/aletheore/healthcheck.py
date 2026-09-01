import http.client
import json
import re
import ssl
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import certifi

from aletheore.history import _save_json_with_rotation

_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

# urllib's default opener retains a FileHandler regardless of which extra
# handlers build_opener() is given - a base_url of file:///etc/passwd or
# any local path is opened as a local file, not rejected as an invalid
# health-check target. Scoping to http(s) is deliberately the only
# restriction: localhost and private-network targets stay allowed, since a
# developer running this against their own local dev server (http://
# localhost:5000, matching this module's own tests) is the common case for
# both the CLI and the MCP tool.
_ALLOWED_SCHEMES = {"http", "https"}
MAX_ENDPOINT_CHECK_WORKERS = 8


def _require_http_scheme(base_url: str) -> None:
    scheme = urlsplit(base_url).scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"base_url must be http or https, got scheme {scheme!r} in {base_url!r}"
        )


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    # Health checks must never follow a redirect: the destination hasn't
    # been through _require_http_scheme the way base_url has, so chasing it
    # would let a monitored endpoint redirect this checker at a file:// or
    # other non-http(s) target. Returning None here makes the opener raise
    # HTTPError for the 3xx instead of following it - the redirect response
    # itself still proves the endpoint is up, which is all reachability
    # monitoring claims to answer.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# A module-local opener, not the process-wide default - installing this
# globally via urllib.request.install_opener() would also block the
# legitimate redirect-following that other callers (licenses.py querying
# PyPI, vulnerabilities.py querying OSV.dev) rely on.
_NO_REDIRECT_OPENER = urllib.request.build_opener(
    _NoRedirectHandler, urllib.request.HTTPSHandler(context=_SSL_CONTEXT)
)


def _pinned_connection_class(base_class: type, pinned_ip: str) -> type:
    # A caller that already resolved and validated a hostname (rejecting
    # private/internal IPs - see app_server/url_validation.py) still hands
    # this module a hostname, not an IP: http.client.HTTPConnection.connect()
    # calls self._create_connection((self.host, self.port), ...), which
    # re-resolves DNS independently of - and later than - that validation. A
    # DNS record that changes between the two lookups (a "rebind") would then
    # have this module connect somewhere the caller never actually validated.
    #
    # _create_connection is set as an INSTANCE attribute in HTTPConnection's
    # own __init__ (its own comment: "to allow unit tests to replace it with
    # a suitable mockup"), so overriding it as a class-level method would be
    # silently shadowed by every new instance re-setting it in __init__.
    # Wrapping it right after super().__init__() runs, as intended, closes
    # the gap to zero: resolution and connection become the same step
    # instead of two. HTTPS still verifies the certificate against the
    # original hostname (self.host, untouched) via SNI, so this only changes
    # which address is dialed, not what identity is trusted.
    class _Pinned(base_class):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            _real_create_connection = self._create_connection

            def _create_connection(address, *cc_args, **cc_kwargs):
                _hostname, port = address
                return _real_create_connection((pinned_ip, port), *cc_args, **cc_kwargs)

            self._create_connection = _create_connection

    _Pinned.__name__ = f"Pinned{base_class.__name__}"
    return _Pinned


def opener_for(pinned_ip: str | None) -> urllib.request.OpenerDirector:
    """A urllib opener that, when pinned_ip is given, connects every
    request to that literal IP instead of letting the connection re-
    resolve the URL's hostname itself - see _pinned_connection_class's
    docstring for why that closes a real DNS-rebinding window rather than
    just narrowing it. Public (not health-check-specific): any caller that
    already validated-and-pinned a URL via app_server.url_validation.
    validate_and_pin_https_url needs the exact same guarantee for its own
    request, not just run_healthcheck's (see scan_worker.slack.
    send_health_alert, which reuses this directly)."""
    if pinned_ip is None:
        return _NO_REDIRECT_OPENER
    pinned_https = _pinned_connection_class(http.client.HTTPSConnection, pinned_ip)
    pinned_http = _pinned_connection_class(http.client.HTTPConnection, pinned_ip)
    return urllib.request.build_opener(
        _NoRedirectHandler,
        _PinnedHTTPSHandler(pinned_https, context=_SSL_CONTEXT),
        _PinnedHTTPHandler(pinned_http),
    )


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, connection_class: type, **kwargs):
        super().__init__(**kwargs)
        self._connection_class = connection_class

    def https_open(self, req):
        return self.do_open(self._connection_class, req, context=self._context)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, connection_class: type):
        super().__init__()
        self._connection_class = connection_class

    def http_open(self, req):
        return self.do_open(self._connection_class, req)


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


def _check_endpoint(endpoint: dict, base_url: str, opener, timeout: float) -> dict:
    if endpoint.get("unresolved"):
        return {
            "method": endpoint.get("method"),
            "path": endpoint["path"],
            "skipped": True,
            "reason": "unresolved routing indirection (include/mount), not a concrete endpoint",
        }

    method = endpoint.get("method")
    # A non-GET endpoint (most commonly a webhook receiver) still gets
    # probed with a GET request - any HTTP response, even a 405, proves
    # the process behind it is up, which is the only thing reachability
    # monitoring claims to answer. Only a connection failure/timeout
    # means "down". The response body isn't meaningful for a method the
    # endpoint doesn't actually implement, so response_shape is skipped
    # for these rather than risking a misleading shape-change alert.
    reachability_only = method not in ("GET", "ANY")

    resolved_path, had_params = _substitute_path_params(endpoint["path"])
    url = base_url.rstrip("/") + "/" + resolved_path.lstrip("/")
    notes = []
    if had_params:
        notes.append("path contains parameters, tested with placeholder value(s)")
    if reachability_only:
        notes.append(
            f"endpoint's declared method is {method}; probed via GET for "
            "reachability only, not a full functional check"
        )
    entry = {
        "method": method if reachability_only else "GET",
        "path": endpoint["path"],
        "note": "; ".join(notes) if notes else None,
        "reachability_only": reachability_only,
    }

    start = time.monotonic()
    try:
        request = urllib.request.Request(url)
        with opener.open(request, timeout=timeout) as response:
            entry["status_code"] = response.status
            entry["reachable"] = True
            entry["response_shape"] = None if reachability_only else _response_shape(response)
    except urllib.error.HTTPError as exc:
        entry["status_code"] = exc.code
        entry["reachable"] = True
        entry["response_shape"] = None
    except (urllib.error.URLError, TimeoutError, OSError):
        entry["status_code"] = None
        entry["reachable"] = False
        entry["response_shape"] = None
    entry["latency_ms"] = round((time.monotonic() - start) * 1000, 1)
    return entry


def run_healthcheck(
    endpoints: list[dict], base_url: str, timeout: float = 5.0, *, pinned_ip: str | None = None
) -> dict:
    """pinned_ip: when given, every request connects to this literal IP
    instead of letting the connection resolve base_url's hostname itself -
    for callers (the hosted health-sweep) that already resolved and
    validated the hostname and need the actual request to hit the exact
    address that was validated, not a fresh, unvalidated resolution. Plain
    CLI/MCP callers pass nothing and keep today's normal DNS behavior."""
    _require_http_scheme(base_url)
    opener = opener_for(pinned_ip)
    results: list[dict]
    if not endpoints:
        results = []
    else:
        with ThreadPoolExecutor(
            max_workers=min(MAX_ENDPOINT_CHECK_WORKERS, len(endpoints))
        ) as pool:
            results = list(
                pool.map(
                    lambda endpoint: _check_endpoint(endpoint, base_url, opener, timeout),
                    endpoints,
                )
            )

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
