import http.server
import threading
import time
import urllib.error

import pytest
from unittest.mock import MagicMock, patch

from aletheore.healthcheck import run_healthcheck, save_healthcheck


def _mock_response(status: int, headers: dict | None = None, body: bytes = b""):
    mock = MagicMock()
    mock.status = status
    mock.headers = headers or {}
    mock.read.return_value = body
    mock.__enter__.return_value = mock
    mock.__exit__.return_value = False
    return mock


def test_run_healthcheck_reports_reachable_get_endpoint():
    endpoints = [
        {
            "method": "GET",
            "path": "/health",
            "framework": "flask",
            "file": "app.py",
            "line": 1,
            "handler": "health",
            "unresolved": False,
        }
    ]

    with patch("aletheore.healthcheck._NO_REDIRECT_OPENER.open", return_value=_mock_response(200)):
        result = run_healthcheck(endpoints, "http://localhost:5000")

    assert result["base_url"] == "http://localhost:5000"
    assert len(result["results"]) == 1
    entry = result["results"][0]
    assert entry["status_code"] == 200
    assert entry["reachable"] is True
    assert entry["note"] is None


def test_run_healthcheck_checks_endpoints_concurrently():
    endpoints = [
        {"method": "GET", "path": f"/health/{index}", "unresolved": False}
        for index in range(8)
    ]

    def slow_open(*args, **kwargs):
        time.sleep(0.05)
        return _mock_response(200)

    start = time.monotonic()
    with patch("aletheore.healthcheck._NO_REDIRECT_OPENER.open", side_effect=slow_open):
        result = run_healthcheck(endpoints, "http://localhost:5000")
    elapsed = time.monotonic() - start

    assert len(result["results"]) == len(endpoints)
    assert all(entry["reachable"] is True for entry in result["results"])
    assert elapsed < 0.25


def test_run_healthcheck_substitutes_path_params_and_notes_it():
    endpoints = [
        {
            "method": "GET",
            "path": "/users/<int:id>",
            "framework": "flask",
            "file": "app.py",
            "line": 1,
            "handler": "get_user",
            "unresolved": False,
        }
    ]

    with patch(
        "aletheore.healthcheck._NO_REDIRECT_OPENER.open", return_value=_mock_response(404)
    ) as mock_urlopen:
        result = run_healthcheck(endpoints, "http://localhost:5000")

    called_url = mock_urlopen.call_args[0][0].full_url
    assert called_url == "http://localhost:5000/users/1"
    assert result["results"][0]["note"] == (
        "path contains parameters, tested with placeholder value(s)"
    )


def test_run_healthcheck_probes_non_get_methods_via_get_for_reachability_only():
    endpoints = [
        {
            "method": "POST",
            "path": "/users",
            "framework": "flask",
            "file": "app.py",
            "line": 1,
            "handler": "create_user",
            "unresolved": False,
        }
    ]

    with patch(
        "aletheore.healthcheck._NO_REDIRECT_OPENER.open",
        side_effect=urllib.error.HTTPError("url", 405, "method not allowed", {}, None),
    ) as mock_urlopen:
        result = run_healthcheck(endpoints, "http://localhost:5000")

    mock_urlopen.assert_called_once()
    entry = result["results"][0]
    assert entry.get("skipped") is not True
    assert entry["method"] == "POST"
    assert entry["reachable"] is True
    assert entry["status_code"] == 405
    assert entry["reachability_only"] is True
    assert entry["response_shape"] is None
    assert "probed via GET for reachability only" in entry["note"]


def test_run_healthcheck_reports_non_get_endpoint_unreachable_on_connection_error():
    endpoints = [
        {
            "method": "POST",
            "path": "/webhook",
            "framework": "flask",
            "file": "app.py",
            "line": 1,
            "handler": "webhook",
            "unresolved": False,
        }
    ]

    with patch(
        "aletheore.healthcheck._NO_REDIRECT_OPENER.open",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        result = run_healthcheck(endpoints, "http://localhost:5000")

    entry = result["results"][0]
    assert entry["reachable"] is False
    assert entry["reachability_only"] is True


def test_run_healthcheck_get_endpoint_is_not_marked_reachability_only():
    endpoints = [
        {
            "method": "GET",
            "path": "/health",
            "framework": "flask",
            "file": "app.py",
            "line": 1,
            "handler": "health",
            "unresolved": False,
        }
    ]

    with patch("aletheore.healthcheck._NO_REDIRECT_OPENER.open", return_value=_mock_response(200)):
        result = run_healthcheck(endpoints, "http://localhost:5000")

    assert result["results"][0]["reachability_only"] is False


def test_run_healthcheck_treats_any_method_as_get_checkable():
    endpoints = [
        {
            "method": "ANY",
            "path": "/items",
            "framework": "django",
            "file": "urls.py",
            "line": 1,
            "handler": "views.items",
            "unresolved": False,
        }
    ]

    with patch("aletheore.healthcheck._NO_REDIRECT_OPENER.open", return_value=_mock_response(200)):
        result = run_healthcheck(endpoints, "http://localhost:8000")

    assert result["results"][0].get("skipped") is not True
    assert result["results"][0]["reachable"] is True


def test_run_healthcheck_skips_unresolved_indirection_entries():
    endpoints = [
        {
            "method": None,
            "path": "myapp.urls",
            "framework": "django",
            "file": "urls.py",
            "line": 1,
            "handler": "include(...)",
            "unresolved": True,
        }
    ]

    with patch("aletheore.healthcheck._NO_REDIRECT_OPENER.open") as mock_urlopen:
        result = run_healthcheck(endpoints, "http://localhost:8000")

    mock_urlopen.assert_not_called()
    assert result["results"][0]["skipped"] is True
    assert "unresolved" in result["results"][0]["reason"]


def test_run_healthcheck_reports_http_error_status_as_reachable():
    endpoints = [
        {
            "method": "GET",
            "path": "/missing",
            "framework": "flask",
            "file": "app.py",
            "line": 1,
            "handler": "x",
            "unresolved": False,
        }
    ]

    with patch(
        "aletheore.healthcheck._NO_REDIRECT_OPENER.open",
        side_effect=urllib.error.HTTPError("url", 404, "not found", {}, None),
    ):
        result = run_healthcheck(endpoints, "http://localhost:5000")

    assert result["results"][0]["status_code"] == 404
    assert result["results"][0]["reachable"] is True


def test_no_redirect_handler_refuses_to_build_a_redirect_request():
    from aletheore.healthcheck import _NoRedirectHandler

    handler = _NoRedirectHandler()
    # Returning None tells urllib's opener not to follow the redirect - it
    # raises HTTPError for the 3xx instead. This is a direct check on that
    # contract, independent of how the opener surfaces it.
    assert (
        handler.redirect_request(
            MagicMock(), MagicMock(), 307, "temporary redirect", {}, "https://internal.example/secret"
        )
        is None
    )


def test_run_healthcheck_reports_redirect_as_reachable_without_following_it():
    endpoints = [
        {
            "method": "GET",
            "path": "/auth/login",
            "framework": "fastapi",
            "file": "auth.py",
            "line": 1,
            "handler": "login",
            "unresolved": False,
        }
    ]

    # A no-redirect opener raises HTTPError for a 3xx rather than chasing
    # it - this is what stands between a monitored endpoint's redirect and
    # this checker being made to probe wherever that redirect points.
    with patch(
        "aletheore.healthcheck._NO_REDIRECT_OPENER.open",
        side_effect=urllib.error.HTTPError("url", 307, "temporary redirect", {}, None),
    ) as mock_open:
        result = run_healthcheck(endpoints, "http://localhost:5000")

    mock_open.assert_called_once()
    entry = result["results"][0]
    assert entry["status_code"] == 307
    assert entry["reachable"] is True


def test_run_healthcheck_reports_unreachable_on_connection_error():
    endpoints = [
        {
            "method": "GET",
            "path": "/x",
            "framework": "flask",
            "file": "app.py",
            "line": 1,
            "handler": "x",
            "unresolved": False,
        }
    ]

    with patch(
        "aletheore.healthcheck._NO_REDIRECT_OPENER.open",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        result = run_healthcheck(endpoints, "http://localhost:9999")

    assert result["results"][0]["reachable"] is False
    assert result["results"][0]["status_code"] is None


def test_run_healthcheck_captures_response_shape_for_json_object():
    endpoints = [
        {
            "method": "GET",
            "path": "/users/1",
            "framework": "flask",
            "file": "app.py",
            "line": 1,
            "handler": "get_user",
            "unresolved": False,
        }
    ]

    response = _mock_response(
        200,
        headers={"Content-Type": "application/json"},
        body=b'{"id": 1, "name": "Ada", "email": "ada@example.com"}',
    )

    with patch("aletheore.healthcheck._NO_REDIRECT_OPENER.open", return_value=response):
        result = run_healthcheck(endpoints, "http://localhost:5000")

    assert result["results"][0]["response_shape"] == ["email", "id", "name"]


def test_run_healthcheck_captures_response_shape_for_json_list_of_objects():
    endpoints = [
        {
            "method": "GET",
            "path": "/users",
            "framework": "flask",
            "file": "app.py",
            "line": 1,
            "handler": "x",
            "unresolved": False,
        }
    ]

    response = _mock_response(
        200,
        headers={"Content-Type": "application/json"},
        body=b'[{"id": 1, "name": "Ada"}, {"id": 2, "name": "Bea"}]',
    )

    with patch("aletheore.healthcheck._NO_REDIRECT_OPENER.open", return_value=response):
        result = run_healthcheck(endpoints, "http://localhost:5000")

    assert result["results"][0]["response_shape"] == ["id", "name"]


def test_run_healthcheck_response_shape_is_none_for_non_json_content_type():
    endpoints = [
        {
            "method": "GET",
            "path": "/health",
            "framework": "flask",
            "file": "app.py",
            "line": 1,
            "handler": "x",
            "unresolved": False,
        }
    ]

    response = _mock_response(200, headers={"Content-Type": "text/plain"}, body=b"OK")

    with patch("aletheore.healthcheck._NO_REDIRECT_OPENER.open", return_value=response):
        result = run_healthcheck(endpoints, "http://localhost:5000")

    assert result["results"][0]["response_shape"] is None


def test_run_healthcheck_response_shape_is_none_for_malformed_json():
    endpoints = [
        {
            "method": "GET",
            "path": "/broken",
            "framework": "flask",
            "file": "app.py",
            "line": 1,
            "handler": "x",
            "unresolved": False,
        }
    ]

    response = _mock_response(
        200,
        headers={"Content-Type": "application/json"},
        body=b"not actually json",
    )

    with patch("aletheore.healthcheck._NO_REDIRECT_OPENER.open", return_value=response):
        result = run_healthcheck(endpoints, "http://localhost:5000")

    assert result["results"][0]["response_shape"] is None


def test_run_healthcheck_response_shape_is_none_on_unreachable():
    endpoints = [
        {
            "method": "GET",
            "path": "/x",
            "framework": "flask",
            "file": "app.py",
            "line": 1,
            "handler": "x",
            "unresolved": False,
        }
    ]

    with patch(
        "aletheore.healthcheck._NO_REDIRECT_OPENER.open",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        result = run_healthcheck(endpoints, "http://localhost:9999")

    assert result["results"][0]["response_shape"] is None


def test_run_healthcheck_rejects_file_scheme(tmp_path):
    # urllib's default opener retains a FileHandler regardless of which
    # extra handlers build_opener() is given - without this check, a
    # file:// base_url is opened as a local file rather than rejected.
    secret = tmp_path / "secret.json"
    secret.write_text('{"password": "hunter2"}')

    with pytest.raises(ValueError, match="http or https"):
        run_healthcheck([], f"file://{secret}")


def test_run_healthcheck_rejects_ftp_scheme():
    with pytest.raises(ValueError, match="http or https"):
        run_healthcheck([], "ftp://example.com")


def test_run_healthcheck_rejects_missing_scheme():
    with pytest.raises(ValueError, match="http or https"):
        run_healthcheck([], "example.com")


def test_run_healthcheck_accepts_https():
    with patch("aletheore.healthcheck._NO_REDIRECT_OPENER.open", return_value=_mock_response(200)):
        result = run_healthcheck([], "https://example.com")
    assert result["base_url"] == "https://example.com"


class _OKHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):  # quiet test output
        pass


@pytest.fixture
def local_http_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _OKHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        thread.join()


# ".invalid" is reserved by RFC 2606 to never resolve - the strongest
# available guarantee that a real network DNS lookup for this hostname
# fails, which is exactly what these tests need to prove: with pinning, the
# request must succeed anyway (proving the connection never asked the
# resolver about the hostname at all); without it, the request must fail
# with the resolver's own error (proving these aren't accidentally passing
# for some unrelated reason).
_UNRESOLVABLE_HOSTNAME = "this-host-does-not-exist.invalid"


def test_run_healthcheck_with_pinned_ip_never_resolves_the_hostname(local_http_server):
    endpoints = [{"method": "GET", "path": "/health", "unresolved": False}]
    base_url = f"http://{_UNRESOLVABLE_HOSTNAME}:{local_http_server}"

    result = run_healthcheck(endpoints, base_url, pinned_ip="127.0.0.1")

    entry = result["results"][0]
    assert entry["reachable"] is True
    assert entry["status_code"] == 200


def test_run_healthcheck_without_pinning_still_resolves_the_hostname_normally(local_http_server):
    # Baseline for the test above: without pinned_ip, the same unresolvable
    # hostname fails exactly as a real DNS-dependent connection should -
    # proving the prior test's success came from pinning, not from
    # something incidental (e.g. the request never actually going out).
    endpoints = [{"method": "GET", "path": "/health", "unresolved": False}]
    base_url = f"http://{_UNRESOLVABLE_HOSTNAME}:{local_http_server}"

    result = run_healthcheck(endpoints, base_url)

    entry = result["results"][0]
    assert entry["reachable"] is False


def test_save_healthcheck_rotates_at_21st_save_keeping_20_newest(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    for hour in range(21):
        save_healthcheck(
            {
                "base_url": "x",
                "checked_at": f"2026-07-16T{hour:02d}:00:00+00:00",
                "results": [],
            },
            repo,
        )

    healthchecks_dir = repo / ".aletheore" / "healthchecks"
    files = sorted(healthchecks_dir.glob("*.json"))
    assert len(files) == 20
