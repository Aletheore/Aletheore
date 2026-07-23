from app_server.dashboard import MIN_CHECKS_FOR_STALE_CONFIDENCE, find_stale_endpoints


def _endpoint(method="GET", path="/api/legacy", file="routes.py", line=10):
    return {"method": method, "path": path, "file": file, "line": line}


def test_flags_endpoint_with_zero_successes_over_min_checks():
    endpoints = [_endpoint()]
    health_summary = {
        ("GET", "/api/legacy"): {
            "ever_reachable": False,
            "check_count": MIN_CHECKS_FOR_STALE_CONFIDENCE,
        }
    }

    result = find_stale_endpoints(endpoints, health_summary)

    assert result == [
        {
            "method": "GET",
            "path": "/api/legacy",
            "file": "routes.py",
            "line": 10,
            "check_count": MIN_CHECKS_FOR_STALE_CONFIDENCE,
        }
    ]


def test_does_not_flag_endpoint_that_has_ever_been_reachable():
    endpoints = [_endpoint()]
    health_summary = {("GET", "/api/legacy"): {"ever_reachable": True, "check_count": 10}}

    assert find_stale_endpoints(endpoints, health_summary) == []


def test_does_not_flag_endpoint_below_min_check_count():
    endpoints = [_endpoint()]
    health_summary = {
        ("GET", "/api/legacy"): {
            "ever_reachable": False,
            "check_count": MIN_CHECKS_FOR_STALE_CONFIDENCE - 1,
        }
    }

    assert find_stale_endpoints(endpoints, health_summary) == []


def test_does_not_flag_endpoint_with_no_health_history_at_all():
    endpoints = [_endpoint()]

    assert find_stale_endpoints(endpoints, {}) == []


def test_ignores_endpoints_missing_file_or_line():
    endpoints = [{"method": "GET", "path": "/api/legacy"}]
    health_summary = {
        ("GET", "/api/legacy"): {
            "ever_reachable": False,
            "check_count": MIN_CHECKS_FOR_STALE_CONFIDENCE,
        }
    }

    result = find_stale_endpoints(endpoints, health_summary)

    assert result == [
        {
            "method": "GET",
            "path": "/api/legacy",
            "file": None,
            "line": None,
            "check_count": MIN_CHECKS_FOR_STALE_CONFIDENCE,
        }
    ]
