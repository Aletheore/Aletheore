import urllib.error
from unittest.mock import MagicMock, patch

from veridion.healthcheck import run_healthcheck


def _mock_response(status: int):
    mock = MagicMock()
    mock.status = status
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

    with patch("veridion.healthcheck.urllib.request.urlopen", return_value=_mock_response(200)):
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
        "veridion.healthcheck.urllib.request.urlopen", return_value=_mock_response(404)
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

    with patch("veridion.healthcheck.urllib.request.urlopen") as mock_urlopen:
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

    with patch("veridion.healthcheck.urllib.request.urlopen", return_value=_mock_response(200)):
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

    with patch("veridion.healthcheck.urllib.request.urlopen") as mock_urlopen:
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
        "veridion.healthcheck.urllib.request.urlopen",
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
        "veridion.healthcheck.urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        result = run_healthcheck(endpoints, "http://localhost:9999")

    assert result["results"][0]["reachable"] is False
    assert result["results"][0]["status_code"] is None
