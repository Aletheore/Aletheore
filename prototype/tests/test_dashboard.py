import json
from pathlib import Path

from starlette.testclient import TestClient

from veridion.dashboard import (
    build_app,
    build_evidence_summary,
    build_graph_summary,
    build_history_summary,
)


def make_evidence(scanned_at: str, module_count: int = 2, secrets_count: int = 0) -> dict:
    return {
        "scanned_at": scanned_at,
        "repository": {
            "languages": [{"name": "python", "file_count": module_count}],
            "modules": [{"path": f"m{i}.py"} for i in range(module_count)],
            "monorepo": {"detected": False, "workspaces": []},
            "dependency_graph": {"nodes": [], "edges": []},
        },
        "git": {
            "total_commits": 10,
            "commit_cadence": {"weekly_counts": [1, 2, 3], "trend": "steady"},
            "ownership": [{"path": "m0.py", "top_author": "alice"}],
            "branches": [{"name": "main", "ahead_of_main": 0}],
        },
        "security": {
            "secrets": {
                "findings": [
                    {
                        "path": f"s{i}.py",
                        "pattern": "aws_access_key_id",
                        "match_preview": "AKIA...MNOP",
                        "likely_placeholder": i % 2 == 0,
                    }
                    for i in range(secrets_count)
                ],
                "history_findings": [],
            },
            "dependency_vulnerabilities": {"checked": True, "reason": None, "findings": []},
        },
        "architecture": {
            "clusters": [{"id": 0, "modules": ["m0.py"], "internal_edges": 0}],
            "layer_violations": {"convention_detected": True, "layers": [], "violations": []},
        },
    }


def test_build_evidence_summary_shape():
    evidence = make_evidence("2026-07-15T12:00:00+00:00", module_count=3, secrets_count=2)

    summary = build_evidence_summary(evidence)

    assert summary["scanned_at"] == "2026-07-15T12:00:00+00:00"
    assert summary["repo_overview"]["module_count"] == 3
    assert summary["repo_overview"]["languages"] == [{"name": "python", "file_count": 3}]
    assert summary["git_activity"]["total_commits"] == 10
    assert summary["git_activity"]["branches"] == [{"name": "main", "ahead_of_main": 0}]
    assert summary["security"]["secrets"]["total_findings"] == 2
    assert summary["security"]["secrets"]["real_findings"] == 1
    assert summary["architecture"]["cluster_count"] == 1
    assert summary["architecture"]["convention_detected"] is True


def test_build_history_summary_reads_all_snapshots(tmp_path):
    repo = tmp_path / "repo"
    history_dir = repo / ".veridion" / "history"
    history_dir.mkdir(parents=True)
    (history_dir / "2026-07-15T10-00-00.json").write_text(
        json.dumps(make_evidence("2026-07-15T10:00:00+00:00", module_count=2, secrets_count=0))
    )
    (history_dir / "2026-07-15T11-00-00.json").write_text(
        json.dumps(make_evidence("2026-07-15T11:00:00+00:00", module_count=3, secrets_count=1))
    )

    result = build_history_summary(repo)

    assert len(result) == 2
    assert result[0] == {
        "scanned_at": "2026-07-15T10:00:00+00:00",
        "module_count": 2,
        "secrets_findings": 0,
        "vulnerability_findings": 0,
    }
    assert result[1]["module_count"] == 3
    assert result[1]["secrets_findings"] == 1


def test_build_history_summary_skips_corrupt_snapshots(tmp_path):
    repo = tmp_path / "repo"
    history_dir = repo / ".veridion" / "history"
    history_dir.mkdir(parents=True)
    (history_dir / "2026-07-15T10-00-00.json").write_text("{not valid json")
    (history_dir / "2026-07-15T11-00-00.json").write_text(
        json.dumps(make_evidence("2026-07-15T11:00:00+00:00"))
    )

    result = build_history_summary(repo)

    assert len(result) == 1
    assert result[0]["scanned_at"] == "2026-07-15T11:00:00+00:00"


def test_build_history_summary_empty_when_no_history(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    assert build_history_summary(repo) == []


def test_build_graph_summary_annotates_nodes_with_cluster_id():
    evidence = {
        "repository": {
            "dependency_graph": {
                "nodes": ["a.py", "b.py", "c.py"],
                "edges": [["a.py", "b.py"], ["b.py", "c.py"]],
            }
        },
        "architecture": {
            "clusters": [
                {"id": 0, "modules": ["a.py", "b.py"], "internal_edges": 1},
                {"id": 1, "modules": ["c.py"], "internal_edges": 0},
            ]
        },
    }

    result = build_graph_summary(evidence)

    assert result["nodes"] == [
        {"id": "a.py", "cluster": 0},
        {"id": "b.py", "cluster": 0},
        {"id": "c.py", "cluster": 1},
    ]
    assert result["edges"] == [
        {"source": "a.py", "target": "b.py"},
        {"source": "b.py", "target": "c.py"},
    ]
    assert result["clusters"] == evidence["architecture"]["clusters"]


def test_build_graph_summary_handles_unclustered_node():
    evidence = {
        "repository": {"dependency_graph": {"nodes": ["orphan.py"], "edges": []}},
        "architecture": {"clusters": []},
    }

    result = build_graph_summary(evidence)

    assert result["nodes"] == [{"id": "orphan.py", "cluster": None}]


def make_repo_with_evidence(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    veridion_dir = repo / ".veridion"
    veridion_dir.mkdir(parents=True)
    (veridion_dir / "evidence.json").write_text(json.dumps(make_evidence("2026-07-15T12:00:00+00:00")))
    return repo


def test_root_serves_html_page(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    app = build_app(repo)
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert 'id="app"' in response.text


def test_api_evidence_returns_summary(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    app = build_app(repo)
    client = TestClient(app)

    response = client.get("/api/evidence")

    assert response.status_code == 200
    body = response.json()
    assert body["scanned_at"] == "2026-07-15T12:00:00+00:00"
    assert "repo_overview" in body


def test_api_history_returns_list(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    app = build_app(repo)
    client = TestClient(app)

    response = client.get("/api/history")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_api_graph_returns_shape(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    app = build_app(repo)
    client = TestClient(app)

    response = client.get("/api/graph")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"nodes", "edges", "clusters"}


def test_api_mcp_tools_returns_14_tools(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    app = build_app(repo)
    client = TestClient(app)

    response = client.get("/api/mcp-tools")

    assert response.status_code == 200
    tools = response.json()
    assert len(tools) == 14
    names = {t["name"] for t in tools}
    assert "veridion_scan" in names
    assert "veridion_search" in names


def test_logo_route_serves_the_bundled_png(tmp_path):
    repo = make_repo_with_evidence(tmp_path)
    app = build_app(repo)
    client = TestClient(app)

    response = client.get("/logo.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
