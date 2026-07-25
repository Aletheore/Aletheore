import toon

from aletheore.evidence_packet import build_evidence_packet
from aletheore.toon_encoding import to_toon


def _evidence():
    return {
        "repository": {
            "name": "cache-org/repo",
            "modules": [
                {
                    "path": "auth/login.py",
                    "imports": ["auth.tokens"],
                    "symbols": {
                        "functions": [{"name": "do_login", "start_line": 10, "end_line": 20}],
                        "classes": [],
                    },
                },
            ],
        },
        "architecture": {
            "clusters": [{"id": 0, "modules": ["auth/login.py"], "internal_edges": 0}]
        },
    }


def _cluster():
    return {"id": 0, "modules": ["auth/login.py"], "internal_edges": 0}


def _brief():
    return {
        "cluster_id": 0,
        "files": [
            {
                "path": "auth/login.py",
                "key_symbols": [{"name": "do_login", "start_line": 10}],
            }
        ],
    }


def test_build_evidence_packet_populates_changed_files_and_symbols():
    packet = build_evidence_packet(_evidence(), _cluster(), _brief(), "indie tier: deepseek-v4-pro")

    assert packet["changed_files"] == ["auth/login.py"]
    assert packet["changed_symbols"] == ["do_login"]
    assert packet["model_routing_reason"] == "indie tier: deepseek-v4-pro"


def test_build_evidence_packet_test_coverage_always_none():
    packet = build_evidence_packet(_evidence(), _cluster(), _brief(), "indie tier: deepseek-v4-pro")

    assert packet["test_coverage"] is None


def test_build_evidence_packet_cache_eligible_defaults_false():
    packet = build_evidence_packet(_evidence(), _cluster(), _brief(), "indie tier: deepseek-v4-pro")

    assert packet["cache_eligible"] is False


def test_build_evidence_packet_cache_eligible_can_be_set():
    packet = build_evidence_packet(
        _evidence(), _cluster(), _brief(), "indie tier: deepseek-v4-pro", cache_eligible=True
    )

    assert packet["cache_eligible"] is True


def test_build_evidence_packet_evidence_locations_reuse_evidence_resolution_shape():
    packet = build_evidence_packet(_evidence(), _cluster(), _brief(), "indie tier: deepseek-v4-pro")

    assert len(packet["evidence_locations"]) == 1
    location = packet["evidence_locations"][0]
    assert location["file"] == "auth/login.py"
    assert location["symbol"] == "do_login"
    assert location["line"] == 10
    assert location["confidence"] == "exact"


def test_evidence_packet_toon_round_trips():
    packet = build_evidence_packet(_evidence(), _cluster(), _brief(), "indie tier: deepseek-v4-pro")

    encoded = to_toon(packet)
    decoded = toon.decode(encoded)

    assert decoded["changed_files"] == packet["changed_files"]
    assert decoded["changed_symbols"] == packet["changed_symbols"]
    assert decoded["model_routing_reason"] == packet["model_routing_reason"]
