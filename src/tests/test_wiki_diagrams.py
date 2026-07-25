from aletheore.wiki_diagrams import build_overview_diagram, build_subsystem_diagram


def make_evidence() -> dict:
    return {
        "repository": {
            "modules": [
                {"path": "auth/login.py", "imports": ["auth/tokens.py", "db/session.py"]},
                {"path": "auth/tokens.py", "imports": []},
                {"path": "db/session.py", "imports": []},
                {"path": "db/models.py", "imports": []},
            ],
            "dependency_graph": {
                "nodes": ["auth/login.py", "auth/tokens.py", "db/session.py", "db/models.py"],
                "edges": [
                    ["auth/login.py", "auth/tokens.py"],
                    ["auth/login.py", "db/session.py"],
                ],
            },
        },
        "architecture": {
            "clusters": [
                {"id": 0, "modules": ["auth/login.py", "auth/tokens.py"], "internal_edges": 1},
                {"id": 1, "modules": ["db/session.py", "db/models.py"], "internal_edges": 0},
            ]
        },
    }


def test_build_overview_diagram_has_one_node_per_cluster():
    diagram = build_overview_diagram(make_evidence())
    assert diagram.startswith("flowchart TD")
    assert 'C0["Cluster 0"]' in diagram
    assert 'C1["Cluster 1"]' in diagram


def test_build_overview_diagram_draws_inter_cluster_edge_not_intra_cluster():
    diagram = build_overview_diagram(make_evidence())
    # auth/login.py -> db/session.py crosses cluster 0 -> cluster 1
    assert "C0 --> C1" in diagram
    # auth/login.py -> auth/tokens.py is within cluster 0 - not drawn at this level
    assert diagram.count("-->") == 1


def test_build_overview_diagram_uses_provided_names():
    diagram = build_overview_diagram(make_evidence(), cluster_names={0: "Authentication", 1: "Database"})
    assert 'C0["Authentication"]' in diagram
    assert 'C1["Database"]' in diagram


def test_build_overview_diagram_escapes_quotes_in_names():
    diagram = build_overview_diagram(make_evidence(), cluster_names={0: 'The "Auth" layer', 1: "Database"})
    assert '\\"' not in diagram  # no broken escaping
    assert '"' in diagram
    assert "The 'Auth' layer" in diagram


def test_build_subsystem_diagram_has_one_node_per_member_file():
    evidence = make_evidence()
    cluster = evidence["architecture"]["clusters"][0]
    diagram = build_subsystem_diagram(evidence, cluster)
    assert 'N0["auth/login.py"]' in diagram
    assert 'N1["auth/tokens.py"]' in diagram


def test_build_subsystem_diagram_only_draws_edges_within_the_cluster():
    evidence = make_evidence()
    cluster = evidence["architecture"]["clusters"][0]
    diagram = build_subsystem_diagram(evidence, cluster)
    # login.py imports tokens.py (in-cluster, drawn) and db/session.py (out
    # of cluster, must not be drawn or referenced at all)
    assert "N0 --> N1" in diagram
    assert "db/session.py" not in diagram


def test_build_subsystem_diagram_handles_empty_cluster():
    diagram = build_subsystem_diagram(make_evidence(), {"id": 5, "modules": []})
    assert diagram == "flowchart TD"
