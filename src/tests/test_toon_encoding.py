import math

import pytest
import toon

from aletheore.toon_encoding import ToonEncodingError, to_toon


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


@pytest.mark.parametrize(
    "data",
    [
        {"endpoints": [{"method": "GET", "path": "/users", "note": "returns: users, sorted"}]},
        {"note": 'a "quoted" value, with comma'},
        {"note": "multi\nline\nvalue"},
        {"items": [{"a": 1, "b": 2}, {"a": 1, "c": 3}, {"x": 9}]},  # non-uniform shapes
        {"items": [{"a": None, "b": 1}, {"a": "x", "b": None}]},
        {"items": [{"code": "007"}, {"code": "0.10"}, {"code": True}, {"code": "true"}]},
        {"unicode": "café ☃ \U0001f600"},
        {"empty_string": "", "nested": {"deep": {"deeper": [1, 2, 3]}}},
    ],
)
def test_to_toon_round_trips_losslessly(data):
    # "Lossless" was asserted (CHANGELOG 0.3.0) but never actually verified
    # by decoding the output back - this closes that gap for the shapes
    # most likely to appear in real evidence data.
    encoded = to_toon(data)
    assert toon.decode(encoded) == data


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_to_toon_sanitizes_non_finite_floats_instead_of_silently_nulling_them(value):
    # Confirmed bug in the underlying library: toon.encode({"v": float("inf")})
    # silently produces 'v: null', indistinguishable on decode from a real
    # null. air.json (json.dumps, which permits Infinity/NaN) keeps the real
    # value, so an unsanitized TOON copy would silently diverge. The
    # sanitized encoding must not decode back to None.
    encoded = to_toon({"v": value})
    decoded = toon.decode(encoded)
    assert decoded["v"] is not None
    assert math.isnan(value) or math.isinf(value)  # sanity on the parametrize itself


def test_to_toon_sanitizes_non_finite_floats_nested_in_arrays():
    data = {"scores": [{"confidence": 0.5}, {"confidence": float("nan")}]}
    encoded = to_toon(data)
    decoded = toon.decode(encoded)
    assert decoded["scores"][0]["confidence"] == 0.5
    assert decoded["scores"][1]["confidence"] is not None


def test_to_toon_raises_toon_encoding_error_on_failure(monkeypatch):
    def _boom(_data):
        raise RecursionError("simulated pathological nesting")

    monkeypatch.setattr("aletheore.toon_encoding.toon.encode", _boom)

    with pytest.raises(ToonEncodingError):
        to_toon({"x": 1})


def test_to_toon_catches_encode_success_decode_failure_asymmetry():
    # Real, reproducible bug in the underlying library (not hypothetical):
    # toon.encode({"nested": [[1, [2, 3]], [4, 5]]}) succeeds and returns
    # output that toon.decode() then rejects with
    # ToonDecodeError("Expected 2 items, but got 0") - a heterogeneous
    # nested-list shape where encode() itself never raises. Without a
    # self-verifying round trip inside to_toon(), this class of corruption
    # would slip past every try/except in the codebase (they all catch
    # ToonEncodingError, which encode() alone never produces here) and sit
    # silently in a written air.toon/MCP result until something else
    # eventually calls decode() on it.
    data = {"nested": [[1, [2, 3]], [4, 5]]}
    with pytest.raises(ToonEncodingError):
        to_toon(data)
