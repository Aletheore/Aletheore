import json

from veridion.architecture import build_clusters, detect_layer_violations, load_architecture_config


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


def test_load_architecture_config_reads_a_valid_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".veridion.json").write_text(
        json.dumps({"layer_markers": {"biz": 1}, "cluster_resolution": 1.5})
    )

    result = load_architecture_config(repo)

    assert result == {"layer_markers": {"biz": 1}, "cluster_resolution": 1.5}


def test_load_architecture_config_returns_none_when_file_missing(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    assert load_architecture_config(repo) is None


def test_load_architecture_config_returns_none_on_malformed_json(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".veridion.json").write_text("{not valid json")

    assert load_architecture_config(repo) is None


def test_load_architecture_config_fills_defaults_when_only_one_key_present(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".veridion.json").write_text(json.dumps({"layer_markers": {"biz": 1}}))

    result = load_architecture_config(repo)

    assert result == {"layer_markers": {"biz": 1}, "cluster_resolution": 1.0}


def test_build_clusters_resolution_parameter_changes_cluster_count():
    dependency_graph = {
        "nodes": ["a", "b", "c", "d", "e", "f"],
        "edges": [
            ["a", "b"], ["a", "c"], ["b", "c"],
            ["d", "e"], ["d", "f"], ["e", "f"],
            ["c", "d"],
        ],
    }

    clusters_default, _ = build_clusters(dependency_graph)
    clusters_high_resolution, _ = build_clusters(dependency_graph, resolution=5.0)

    assert len(clusters_default) == 2
    assert len(clusters_high_resolution) == 6


def test_detect_layer_violations_custom_marker_enables_detection():
    dependency_graph = {
        "nodes": ["app/biz/order.py", "app/routers/orders.py"],
        "edges": [["app/routers/orders.py", "app/biz/order.py"]],
    }

    without_custom = detect_layer_violations(dependency_graph)
    assert without_custom["convention_detected"] is False

    with_custom = detect_layer_violations(dependency_graph, custom_markers={"biz": 1})
    assert with_custom["convention_detected"] is True
    layer_names = {layer["name"] for layer in with_custom["layers"]}
    assert layer_names == {"biz", "routers"}


def test_detect_layer_violations_custom_marker_overrides_built_in_rank():
    dependency_graph = {
        "nodes": ["app/domain/user.py", "app/services/auth.py"],
        "edges": [["app/domain/user.py", "app/services/auth.py"]],
    }

    default_result = detect_layer_violations(dependency_graph)
    assert len(default_result["violations"]) == 1

    overridden_result = detect_layer_violations(dependency_graph, custom_markers={"services": 0})
    assert overridden_result["violations"] == []
    services_layer = next(l for l in overridden_result["layers"] if l["name"] == "services")
    assert services_layer["rank"] == 0


def test_detect_layer_violations_custom_marker_matching_nothing_does_not_force_detection():
    dependency_graph = {
        "nodes": ["app/domain/user.py", "app/domain/order.py"],
        "edges": [],
    }

    result = detect_layer_violations(dependency_graph, custom_markers={"nonexistent_folder": 5})

    assert result["convention_detected"] is False
    assert result["layers"] == []


def test_detect_layer_violations_empty_custom_markers_does_not_force_detection():
    dependency_graph = {
        "nodes": ["app/domain/user.py", "app/domain/order.py"],
        "edges": [],
    }

    result = detect_layer_violations(dependency_graph, custom_markers={})

    assert result["convention_detected"] is False
    assert result["layers"] == []
