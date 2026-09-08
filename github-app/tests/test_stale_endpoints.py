from app_server.dashboard import MIN_CHECKS_FOR_STALE_CONFIDENCE, find_stale_endpoints


def _endpoint(method="GET", path="/api/legacy", file="routes.py", line=10):
    return {"method": method, "path": path, "file": file, "line": line}


def test_flags_endpoint_with_zero_successes_over_min_checks():
    endpoints = [_endpoint()]
    health_summary = {
        (1, "GET", "/api/legacy"): {
            "ever_reachable": False,
            "check_count": MIN_CHECKS_FOR_STALE_CONFIDENCE,
            "target_label": "Production",
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
            "target_id": 1,
            "target_label": "Production",
        }
    ]


def test_does_not_flag_endpoint_that_has_ever_been_reachable():
    endpoints = [_endpoint()]
    health_summary = {
        (1, "GET", "/api/legacy"): {"ever_reachable": True, "check_count": 10, "target_label": None}
    }

    assert find_stale_endpoints(endpoints, health_summary) == []


def test_does_not_flag_endpoint_below_min_check_count():
    endpoints = [_endpoint()]
    health_summary = {
        (1, "GET", "/api/legacy"): {
            "ever_reachable": False,
            "check_count": MIN_CHECKS_FOR_STALE_CONFIDENCE - 1,
            "target_label": None,
        }
    }

    assert find_stale_endpoints(endpoints, health_summary) == []


def test_does_not_flag_endpoint_with_no_health_history_at_all():
    endpoints = [_endpoint()]

    assert find_stale_endpoints(endpoints, {}) == []


def test_ignores_endpoints_missing_file_or_line():
    endpoints = [{"method": "GET", "path": "/api/legacy"}]
    health_summary = {
        (1, "GET", "/api/legacy"): {
            "ever_reachable": False,
            "check_count": MIN_CHECKS_FOR_STALE_CONFIDENCE,
            "target_label": None,
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
            "target_id": 1,
            "target_label": None,
        }
    ]


def test_a_permanently_broken_target_is_flagged_even_when_a_sibling_target_is_healthy():
    # Real bug found via audit: get_endpoint_health_summary_since used to
    # GROUP BY endpoint_method, endpoint_path alone, so bool_or(reachable)
    # blended a permanently-broken production target together with a
    # healthy staging target checking the same endpoint - "ever_reachable"
    # came back True (from staging), so the dead production target never
    # got flagged here at all. Now that the summary is grouped per
    # target_id, each target's own staleness is visible independently.
    endpoints = [_endpoint()]
    health_summary = {
        (1, "GET", "/api/legacy"): {
            "ever_reachable": False,
            "check_count": MIN_CHECKS_FOR_STALE_CONFIDENCE,
            "target_label": "Production",
        },
        (2, "GET", "/api/legacy"): {
            "ever_reachable": True,
            "check_count": MIN_CHECKS_FOR_STALE_CONFIDENCE,
            "target_label": "Staging",
        },
    }

    result = find_stale_endpoints(endpoints, health_summary)

    assert result == [
        {
            "method": "GET",
            "path": "/api/legacy",
            "file": "routes.py",
            "line": 10,
            "check_count": MIN_CHECKS_FOR_STALE_CONFIDENCE,
            "target_id": 1,
            "target_label": "Production",
        }
    ]
