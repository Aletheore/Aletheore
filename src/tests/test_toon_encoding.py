import math
import random

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


def _sanitize_reference(data):
    # Independent re-implementation of _sanitize_for_toon, kept separate
    # (not imported) so the fuzz test below doesn't just validate to_toon's
    # internal logic against itself.
    if isinstance(data, float):
        if math.isnan(data) or math.isinf(data):
            return str(data)
        return data
    if isinstance(data, dict):
        return {key: _sanitize_reference(value) for key, value in data.items()}
    if isinstance(data, list):
        return [_sanitize_reference(item) for item in data]
    return data


def _random_shape(rng, depth=0, max_depth=4):
    choice = "scalar" if depth >= max_depth else rng.choice(
        ["dict", "list", "mixed_list", "scalar"]
    )
    if choice == "scalar":
        return rng.choice(
            [
                rng.randint(-1000, 1000),
                rng.uniform(-1000, 1000),
                True,
                False,
                None,
                "".join(rng.choices("abcXYZ 012,:\"'\n", k=rng.randint(0, 12))),
            ]
        )
    if choice == "dict":
        return {
            f"k{i}": _random_shape(rng, depth + 1, max_depth) for i in range(rng.randint(0, 4))
        }
    if choice == "list":
        return [_random_shape(rng, depth + 1, max_depth) for _ in range(rng.randint(0, 4))]
    # mixed_list deliberately targets the exact shape class that broke: a
    # bare list value sitting alongside non-list siblings in the same array
    # (this is what test_to_toon_catches_encode_success_decode_failure_asymmetry
    # hand-picked one instance of).
    return [
        [_random_shape(rng, depth + 1, max_depth) for _ in range(rng.randint(1, 3))]
        if rng.random() < 0.4
        else _random_shape(rng, depth + 1, max_depth)
        for _ in range(rng.randint(2, 4))
    ]


def test_to_toon_round_trips_or_cleanly_rejects_thousands_of_random_shapes():
    # No hypothesis dependency (not currently in pyproject.toml, and adding
    # one for a single test file isn't worth the new-dependency footprint
    # right after this module's own supply-chain posture was tightened
    # elsewhere) - a seeded, bounded, recursive shape generator gets most
    # of the real value instead: broad coverage plus a generator
    # deliberately weighted toward the "list value among non-list array
    # siblings" shape class that produced a real, previously-undetected
    # bug (see test_to_toon_catches_encode_success_decode_failure_asymmetry).
    # For every shape to_toon() doesn't reject, independently re-decode and
    # compare - not just trusting to_toon's own internal round-trip check,
    # since a bug in that check itself wouldn't be caught by relying on it.
    rng = random.Random(20260826)
    successes = 0
    for _ in range(3000):
        data = {"root": _random_shape(rng)}
        try:
            encoded = to_toon(data)
        except ToonEncodingError:
            # to_toon already verified this specific shape doesn't
            # round-trip and rejected it - that's the contract working,
            # not a test failure.
            continue
        assert toon.decode(encoded) == _sanitize_reference(data)
        successes += 1
    # Sanity check only - not a claim about real evidence data. The 40%
    # mixed_list weighting deliberately over-represents the shape class
    # that broke, so a large rejection rate here (measured: ~60% of the
    # 3000) is the round-trip check doing its job on adversarial input,
    # not evidence of a problem - this just confirms the generator isn't
    # producing exclusively-unencodable garbage.
    assert successes > 500
