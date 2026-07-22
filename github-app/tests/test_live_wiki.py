import json
from unittest.mock import MagicMock

from scan_worker.live_wiki import (
    build_subsystem_record,
    generate_overview,
    generate_subsystems,
    propose_cluster_names,
)


def make_evidence() -> dict:
    return {
        "repository": {
            "modules": [
                {
                    "path": "auth/login.py",
                    "language": "python",
                    "imports": [],
                    "symbols": {
                        "functions": [{"name": "do_login", "start_line": 10, "end_line": 20}],
                        "classes": [],
                    },
                },
                {
                    "path": "auth/tokens.py",
                    "language": "python",
                    "imports": [],
                    "symbols": {"functions": [], "classes": []},
                },
            ],
            "dependency_graph": {"nodes": [], "edges": []},
        },
        "architecture": {
            "clusters": [{"id": 0, "modules": ["auth/login.py", "auth/tokens.py"], "internal_edges": 0}]
        },
    }


def _adapter(response_text: str) -> MagicMock:
    adapter = MagicMock()
    adapter.simple_completion.return_value = response_text
    return adapter


def test_propose_cluster_names_uses_model_response():
    briefs = [{"cluster_id": 0, "files": [{"path": "auth/login.py"}], "fallback_name": "auth"}]
    adapter = _adapter(json.dumps({"0": "Authentication"}))

    names = propose_cluster_names(briefs, adapter)

    assert names == {0: "Authentication"}


def test_propose_cluster_names_falls_back_on_missing_entry():
    briefs = [{"cluster_id": 0, "files": [], "fallback_name": "auth"}]
    adapter = _adapter(json.dumps({}))

    assert propose_cluster_names(briefs, adapter) == {0: "auth"}


def test_propose_cluster_names_falls_back_on_malformed_json():
    briefs = [{"cluster_id": 0, "files": [], "fallback_name": "auth"}]
    adapter = _adapter("not json at all")

    assert propose_cluster_names(briefs, adapter) == {0: "auth"}


def test_propose_cluster_names_returns_empty_for_no_briefs():
    adapter = MagicMock()
    assert propose_cluster_names([], adapter) == {}
    adapter.simple_completion.assert_not_called()


def _brief_for(evidence: dict) -> dict:
    from aletheore.wiki_mapping import build_cluster_briefs

    return build_cluster_briefs(evidence)[0]


def test_build_subsystem_record_happy_path():
    evidence = make_evidence()
    cluster = evidence["architecture"]["clusters"][0]
    brief = _brief_for(evidence)
    adapter = _adapter(
        json.dumps(
            {
                "description": "Handles user login and token issuance.",
                "files": [
                    {
                        "path": "auth/login.py",
                        "role": "Entry point for user login.",
                        "key_symbols": [
                            {"name": "do_login", "line": 10, "explanation": "Authenticates a user."}
                        ],
                    }
                ],
            }
        )
    )

    record = build_subsystem_record(evidence, cluster, brief, "Authentication", adapter)

    assert record["subsystem_id"] == "0"
    assert record["name"] == "Authentication"
    assert record["description"] == "Handles user login and token issuance."
    assert record["files"][0]["path"] == "auth/login.py"
    assert record["files"][0]["key_symbols"][0]["name"] == "do_login"
    assert "flowchart TD" in record["diagram_mermaid"]


def test_build_subsystem_record_drops_hallucinated_file():
    evidence = make_evidence()
    cluster = evidence["architecture"]["clusters"][0]
    brief = _brief_for(evidence)
    adapter = _adapter(
        json.dumps(
            {
                "description": "Handles login.",
                "files": [
                    {"path": "auth/login.py", "role": "Real file.", "key_symbols": []},
                    {"path": "totally/made/up.py", "role": "Fabricated file.", "key_symbols": []},
                ],
            }
        )
    )

    record = build_subsystem_record(evidence, cluster, brief, "Authentication", adapter)

    paths = {f["path"] for f in record["files"]}
    assert paths == {"auth/login.py"}


def test_build_subsystem_record_drops_hallucinated_symbol():
    evidence = make_evidence()
    cluster = evidence["architecture"]["clusters"][0]
    brief = _brief_for(evidence)
    adapter = _adapter(
        json.dumps(
            {
                "description": "Handles login.",
                "files": [
                    {
                        "path": "auth/login.py",
                        "role": "Real file.",
                        "key_symbols": [
                            {"name": "do_login", "line": 10, "explanation": "real"},
                            {"name": "fake_fn", "line": 999, "explanation": "fabricated"},
                        ],
                    }
                ],
            }
        )
    )

    record = build_subsystem_record(evidence, cluster, brief, "Authentication", adapter)

    names = {s["name"] for s in record["files"][0]["key_symbols"]}
    assert names == {"do_login"}


def test_build_subsystem_record_returns_none_for_malformed_json():
    evidence = make_evidence()
    cluster = evidence["architecture"]["clusters"][0]
    brief = _brief_for(evidence)
    adapter = _adapter("not valid json")

    assert build_subsystem_record(evidence, cluster, brief, "Authentication", adapter) is None


def test_build_subsystem_record_rejects_description_with_hallucinated_citation():
    evidence = make_evidence()
    cluster = evidence["architecture"]["clusters"][0]
    brief = _brief_for(evidence)
    adapter = _adapter(
        json.dumps({"description": "See `totally/fake/path.py:42` for details.", "files": []})
    )

    assert build_subsystem_record(evidence, cluster, brief, "Authentication", adapter) is None


def test_generate_subsystems_full_build_covers_every_cluster():
    evidence = make_evidence()
    naming_adapter = _adapter(json.dumps({"0": "Authentication"}))
    writing_adapter = _adapter(json.dumps({"description": "Auth stuff.", "files": []}))

    records = generate_subsystems(evidence, naming_adapter, writing_adapter)

    assert len(records) == 1
    assert records[0]["name"] == "Authentication"


def test_generate_subsystems_incremental_filters_to_given_clusters():
    evidence = make_evidence()
    naming_adapter = _adapter(json.dumps({"0": "Authentication"}))
    writing_adapter = _adapter(json.dumps({"description": "Auth stuff.", "files": []}))

    records = generate_subsystems(evidence, naming_adapter, writing_adapter, cluster_ids={99})

    assert records == []
    naming_adapter.simple_completion.assert_not_called()


def test_generate_overview_happy_path():
    evidence = make_evidence()
    subsystem_records = [{"subsystem_id": "0", "name": "Authentication", "description": "Handles login."}]
    adapter = _adapter(json.dumps({"description": "This system handles authentication."}))

    overview = generate_overview(evidence, subsystem_records, adapter)

    assert overview["description"] == "This system handles authentication."
    assert "flowchart TD" in overview["diagram_mermaid"]
    assert "Authentication" in overview["diagram_mermaid"]


def test_generate_overview_falls_back_on_malformed_response():
    evidence = make_evidence()
    subsystem_records = [{"subsystem_id": "0", "name": "Authentication", "description": "Handles login."}]
    adapter = _adapter("not json")

    overview = generate_overview(evidence, subsystem_records, adapter)

    assert overview["description"] == "Overview description unavailable."


def test_generate_overview_falls_back_on_hallucinated_citation():
    evidence = make_evidence()
    subsystem_records = [{"subsystem_id": "0", "name": "Authentication", "description": "Handles login."}]
    adapter = _adapter(json.dumps({"description": "See `fake/path.py:1` for the entry point."}))

    overview = generate_overview(evidence, subsystem_records, adapter)

    assert overview["description"] == "Overview description unavailable."
