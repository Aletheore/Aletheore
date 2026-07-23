from scan_worker.jobs import find_touched_incident_endpoints


def _endpoint(method="GET", path="/api/users", file="routes/users.py", line=10):
    return {"method": method, "path": path, "file": file, "line": line}


def _incident(method="GET", path="/api/users", count=3):
    return {
        "endpoint_method": method,
        "endpoint_path": path,
        "incident_count": count,
        "last_incident_at": "2026-07-20T00:00:00Z",
    }


def test_flags_endpoint_touched_by_changed_files_with_an_incident():
    evidence = {"repository": {"api_endpoints": {"endpoints": [_endpoint()]}}}
    incidents = [_incident()]

    result = find_touched_incident_endpoints(["routes/users.py"], evidence, incidents)

    assert result == [
        {
            "method": "GET",
            "path": "/api/users",
            "file": "routes/users.py",
            "line": 10,
            "incident_count": 3,
            "last_incident_at": "2026-07-20T00:00:00Z",
        }
    ]


def test_ignores_endpoint_in_a_file_not_in_the_diff():
    evidence = {"repository": {"api_endpoints": {"endpoints": [_endpoint()]}}}
    incidents = [_incident()]

    assert find_touched_incident_endpoints(["some/other/file.py"], evidence, incidents) == []


def test_ignores_touched_endpoint_with_no_incident_history():
    evidence = {"repository": {"api_endpoints": {"endpoints": [_endpoint()]}}}

    assert find_touched_incident_endpoints(["routes/users.py"], evidence, []) == []
