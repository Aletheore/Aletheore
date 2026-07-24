import urllib.error
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

    with patch("aletheore.healthcheck.urllib.request.urlopen", return_value=_mock_response(200)):
        result = run_healthcheck(endpoints, "http://localhost:5000")

    assert result["base_url"] == "http://localhost:5000"
    assert len(result["results"]) == 1
    entry = result["results"][0]
    assert entry["status_code"] == 200
    assert entry["reachable"] is True
    assert entry["note"] is None


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
        "aletheore.healthcheck.urllib.request.urlopen", return_value=_mock_response(404)
    ) as mock_urlopen:
        result = run_healthcheck(endpoints, "http://localhost:5000")

    called_url = mock_urlopen.call_args[0][0].full_url
    assert called_url == "http://localhost:5000/users/1"
    assert result["results"][0]["note"] == (
        "path contains parameters, tested with placeholder value(s)"
    )


def test_run_healthcheck_never_sends_non_get_methods():
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

    with patch("aletheore.healthcheck.urllib.request.urlopen") as mock_urlopen:
        result = run_healthcheck(endpoints, "http://localhost:5000")

    mock_urlopen.assert_not_called()
    assert result["results"][0]["skipped"] is True
    assert result["results"][0]["reason"] == "only GET is health-checked"


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

    with patch("aletheore.healthcheck.urllib.request.urlopen", return_value=_mock_response(200)):
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

    with patch("aletheore.healthcheck.urllib.request.urlopen") as mock_urlopen:
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
        "aletheore.healthcheck.urllib.request.urlopen",
        side_effect=urllib.error.HTTPError("url", 404, "not found", {}, None),
    ):
        result = run_healthcheck(endpoints, "http://localhost:5000")

    assert result["results"][0]["status_code"] == 404
    assert result["results"][0]["reachable"] is True


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
        "aletheore.healthcheck.urllib.request.urlopen",
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

    with patch("aletheore.healthcheck.urllib.request.urlopen", return_value=response):
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

    with patch("aletheore.healthcheck.urllib.request.urlopen", return_value=response):
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

    with patch("aletheore.healthcheck.urllib.request.urlopen", return_value=response):
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

    with patch("aletheore.healthcheck.urllib.request.urlopen", return_value=response):
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
        "aletheore.healthcheck.urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        result = run_healthcheck(endpoints, "http://localhost:9999")

    assert result["results"][0]["response_shape"] is None


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
