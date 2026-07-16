from aletheore.toon_encoding import to_toon


def test_to_toon_encodes_a_uniform_array_of_objects():
    data = {
        "endpoints": [
            {"method": "GET", "path": "/users", "unresolved": False},
            {"method": "POST", "path": "/users", "unresolved": False},
        ]
    }

    result = to_toon(data)

    assert "endpoints" in result
    assert "GET" in result
    assert "POST" in result
    assert "/users" in result


def test_to_toon_is_more_compact_than_json_for_uniform_arrays():
    import json

    data = {
        "endpoints": [
            {
                "method": "GET",
                "path": "/users",
                "framework": "flask",
                "file": "app.py",
                "line": 8,
                "handler": "users",
                "unresolved": False,
                "note": None,
            }
            for _ in range(5)
        ]
    }

    toon_result = to_toon(data)
    json_result = json.dumps(data, indent=2)

    assert len(toon_result) < len(json_result)


def test_to_toon_handles_empty_list():
    result = to_toon({"endpoints": []})
    assert "endpoints" in result
