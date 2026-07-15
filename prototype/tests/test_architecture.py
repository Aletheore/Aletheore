from veridion.architecture import build_clusters, detect_layer_violations


def test_build_clusters_finds_two_clusters_with_a_thin_bridge():
    dependency_graph = {
        "nodes": ["a.py", "b.py", "c.py", "x.py", "y.py", "z.py"],
        "edges": [
            ["a.py", "b.py"],
            ["b.py", "a.py"],
            ["a.py", "c.py"],
            ["c.py", "b.py"],
            ["x.py", "y.py"],
            ["y.py", "x.py"],
            ["x.py", "z.py"],
            ["z.py", "y.py"],
            ["a.py", "x.py"],
        ],
    }

    clusters, cross_cluster_edges = build_clusters(dependency_graph)

    cluster_by_module = {}
    for cluster in clusters:
        for module in cluster["modules"]:
            cluster_by_module[module] = cluster["id"]

    assert cluster_by_module["a.py"] == cluster_by_module["b.py"] == cluster_by_module["c.py"]
    assert cluster_by_module["x.py"] == cluster_by_module["y.py"] == cluster_by_module["z.py"]
    assert cluster_by_module["a.py"] != cluster_by_module["x.py"]

    abc_cluster = next(c for c in clusters if "a.py" in c["modules"])
    assert abc_cluster["internal_edges"] == 4

    assert len(cross_cluster_edges) == 1
    bridge = cross_cluster_edges[0]
    assert bridge["count"] == 1
    assert bridge["edges"] == [["a.py", "x.py"]]


def test_build_clusters_handles_isolated_nodes_without_crashing():
    dependency_graph = {"nodes": ["a.py", "b.py", "c.py"], "edges": []}

    clusters, cross_cluster_edges = build_clusters(dependency_graph)

    all_modules = sorted(m for c in clusters for m in c["modules"])
    assert all_modules == ["a.py", "b.py", "c.py"]
    assert cross_cluster_edges == []


def test_build_clusters_handles_empty_graph():
    clusters, cross_cluster_edges = build_clusters({"nodes": [], "edges": []})

    assert clusters == []
    assert cross_cluster_edges == []


def test_build_clusters_is_deterministic_across_runs():
    dependency_graph = {
        "nodes": ["a.py", "b.py", "c.py", "x.py", "y.py", "z.py"],
        "edges": [
            ["a.py", "b.py"],
            ["b.py", "a.py"],
            ["a.py", "c.py"],
            ["c.py", "b.py"],
            ["x.py", "y.py"],
            ["y.py", "x.py"],
            ["x.py", "z.py"],
            ["z.py", "y.py"],
            ["a.py", "x.py"],
        ],
    }

    first = build_clusters(dependency_graph)
    second = build_clusters(dependency_graph)

    assert first == second


def test_detect_layer_violations_finds_a_real_violation():
    dependency_graph = {
        "nodes": ["app/domain/user.py", "app/infrastructure/db.py", "app/services/auth.py"],
        "edges": [
            ["app/domain/user.py", "app/infrastructure/db.py"],
            ["app/services/auth.py", "app/domain/user.py"],
        ],
    }

    result = detect_layer_violations(dependency_graph)

    assert result["convention_detected"] is True
    assert len(result["violations"]) == 1
    violation = result["violations"][0]
    assert violation["from"] == "app/domain/user.py"
    assert violation["to"] == "app/infrastructure/db.py"
    assert "domain" in violation["reason"]
    assert "infrastructure" in violation["reason"]

    layer_names = {layer["name"] for layer in result["layers"]}
    assert layer_names == {"domain", "infrastructure", "services"}


def test_detect_layer_violations_clean_case_no_violations():
    dependency_graph = {
        "nodes": ["app/domain/user.py", "app/infrastructure/db.py"],
        "edges": [["app/infrastructure/db.py", "app/domain/user.py"]],
    }

    result = detect_layer_violations(dependency_graph)

    assert result["convention_detected"] is True
    assert result["violations"] == []


def test_detect_layer_violations_no_convention_when_only_one_rank_present():
    dependency_graph = {
        "nodes": ["app/domain/a.py", "app/domain/b.py"],
        "edges": [["app/domain/a.py", "app/domain/b.py"]],
    }

    result = detect_layer_violations(dependency_graph)

    assert result == {"convention_detected": False, "layers": [], "violations": []}


def test_detect_layer_violations_no_convention_when_no_layer_folders_at_all():
    dependency_graph = {
        "nodes": ["app/routes.py", "app/helpers.py"],
        "edges": [["app/routes.py", "app/helpers.py"]],
    }

    result = detect_layer_violations(dependency_graph)

    assert result == {"convention_detected": False, "layers": [], "violations": []}


def test_detect_layer_violations_recognizes_infra_abbreviation():
    dependency_graph = {
        "nodes": ["app/domain/user.py", "app/infra/db.py"],
        "edges": [["app/domain/user.py", "app/infra/db.py"]],
    }

    result = detect_layer_violations(dependency_graph)

    assert result["convention_detected"] is True
    assert len(result["violations"]) == 1
