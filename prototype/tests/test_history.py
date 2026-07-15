import json
from pathlib import Path

from veridion.history import compute_diff, list_snapshots, save_snapshot


def make_evidence(scanned_at: str) -> dict:
    return {"veridion_version": "0.1.0", "scanned_at": scanned_at, "repo_path": "/tmp/repo"}


def test_save_snapshot_creates_history_dir_if_absent(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    save_snapshot(make_evidence("2026-07-15T10:00:00.000000+00:00"), repo)

    assert (repo / ".veridion" / "history").is_dir()


def test_save_snapshot_writes_readable_json(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    path = save_snapshot(make_evidence("2026-07-15T10:00:00.000000+00:00"), repo)

    assert json.loads(path.read_text())["scanned_at"] == "2026-07-15T10:00:00.000000+00:00"


def test_list_snapshots_returns_empty_list_when_no_history_dir(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    assert list_snapshots(repo) == []


def test_list_snapshots_returns_chronological_order(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    save_snapshot(make_evidence("2026-07-15T10:00:00.000000+00:00"), repo)
    save_snapshot(make_evidence("2026-07-15T09:00:00.000000+00:00"), repo)
    save_snapshot(make_evidence("2026-07-15T11:00:00.000000+00:00"), repo)

    snapshots = list_snapshots(repo)
    scanned_ats = [json.loads(p.read_text())["scanned_at"] for p in snapshots]
    assert scanned_ats == [
        "2026-07-15T09:00:00.000000+00:00",
        "2026-07-15T10:00:00.000000+00:00",
        "2026-07-15T11:00:00.000000+00:00",
    ]


def test_save_snapshot_rotates_at_21st_save_keeping_the_20_newest(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    for hour in range(21):
        save_snapshot(make_evidence(f"2026-07-15T{hour:02d}:00:00.000000+00:00"), repo)

    snapshots = list_snapshots(repo)
    assert len(snapshots) == 20
    scanned_ats = [json.loads(p.read_text())["scanned_at"] for p in snapshots]
    assert scanned_ats[0] == "2026-07-15T01:00:00.000000+00:00"
    assert scanned_ats[-1] == "2026-07-15T20:00:00.000000+00:00"


def test_save_snapshot_handles_same_timestamp_collision_without_losing_data(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    save_snapshot(make_evidence("2026-07-15T10:00:00.000000+00:00"), repo)
    save_snapshot(make_evidence("2026-07-15T10:00:00.000000+00:00"), repo)

    snapshots = list_snapshots(repo)
    assert len(snapshots) == 2


def base_evidence() -> dict:
    return {
        "repository": {
            "modules": [{"path": "a.py"}, {"path": "b.py"}],
            "dependency_graph": {"nodes": ["a.py", "b.py"], "edges": [["a.py", "b.py"]]},
        },
        "git": {"total_commits": 10},
        "security": {
            "secrets": {
                "findings": [
                    {
                        "path": "a.py",
                        "pattern": "aws_access_key_id",
                        "match_preview": "AKIA...MNOP",
                        "likely_placeholder": False,
                    }
                ],
                "history_scanned_commits": 5,
                "history_findings": [],
            },
            "dependency_vulnerabilities": {
                "checked": True,
                "reason": None,
                "findings": [
                    {
                        "ecosystem": "PyPI",
                        "package": "requests",
                        "installed_version": "2.0.0",
                        "advisory_id": "GHSA-1",
                        "summary": "x",
                        "severity": [],
                    }
                ],
            },
        },
        "architecture": {
            "layer_violations": {
                "violations": [
                    {"from": "app/routers/a.py", "to": "app/domain/b.py", "reason": "x"}
                ]
            }
        },
    }


def test_compute_diff_reports_no_new_or_resolved_when_identical():
    evidence = base_evidence()
    diff = compute_diff(evidence, evidence)

    assert diff["secrets"] == {"new": [], "resolved": []}
    assert diff["vulnerabilities"] == {"new": [], "resolved": []}
    assert diff["layer_violations"] == {"new": [], "resolved": []}
    assert diff["aggregate_deltas"] == {
        "module_count": 0,
        "dependency_graph_edge_count": 0,
        "total_commits": 0,
    }
    assert "caveats" not in diff


def test_compute_diff_detects_a_new_secret_finding():
    old = base_evidence()
    new = base_evidence()
    new["security"]["secrets"]["findings"].append(
        {
            "path": "c.py",
            "pattern": "generic_credential_assignment",
            "match_preview": "test****...cret",
            "likely_placeholder": True,
        }
    )

    diff = compute_diff(old, new)

    assert len(diff["secrets"]["new"]) == 1
    assert diff["secrets"]["new"][0]["path"] == "c.py"
    assert diff["secrets"]["resolved"] == []


def test_compute_diff_detects_a_resolved_vulnerability():
    old = base_evidence()
    new = base_evidence()
    new["security"]["dependency_vulnerabilities"]["findings"] = []

    diff = compute_diff(old, new)

    assert diff["vulnerabilities"]["new"] == []
    assert len(diff["vulnerabilities"]["resolved"]) == 1
    assert diff["vulnerabilities"]["resolved"][0]["advisory_id"] == "GHSA-1"


def test_compute_diff_detects_a_new_layer_violation():
    old = base_evidence()
    new = base_evidence()
    new["architecture"]["layer_violations"]["violations"].append(
        {"from": "app/routers/x.py", "to": "app/domain/y.py", "reason": "y"}
    )

    diff = compute_diff(old, new)

    assert len(diff["layer_violations"]["new"]) == 1


def test_compute_diff_aggregate_deltas_reflect_real_changes():
    old = base_evidence()
    new = base_evidence()
    new["repository"]["modules"].append({"path": "c.py"})
    new["git"]["total_commits"] = 13

    diff = compute_diff(old, new)

    assert diff["aggregate_deltas"]["module_count"] == 1
    assert diff["aggregate_deltas"]["total_commits"] == 3


def test_compute_diff_caveat_fires_when_vulnerability_checking_toggled():
    old = base_evidence()
    old["security"]["dependency_vulnerabilities"]["checked"] = False
    old["security"]["dependency_vulnerabilities"]["findings"] = []
    new = base_evidence()

    diff = compute_diff(old, new)

    assert "caveats" in diff
    assert any("vulnerability" in c for c in diff["caveats"])


def test_compute_diff_caveat_fires_when_history_scanning_toggled():
    old = base_evidence()
    old["security"]["secrets"]["history_scanned_commits"] = 0
    new = base_evidence()

    diff = compute_diff(old, new)

    assert "caveats" in diff
    assert any("history" in c for c in diff["caveats"])


def test_compute_diff_no_caveat_when_configuration_unchanged():
    evidence = base_evidence()

    diff = compute_diff(evidence, evidence)

    assert "caveats" not in diff


def test_compute_diff_full_mode_shows_added_removed_changed():
    old = {"a": 1, "b": {"c": 2}, "d": [1, 2]}
    new = {"a": 1, "b": {"c": 3}, "e": "new"}

    diff = compute_diff(old, new, full=True)

    assert {"path": "e", "value": "new"} in diff["added"]
    assert {"path": "d[0]", "value": 1} in diff["removed"]
    assert {"path": "d[1]", "value": 2} in diff["removed"]
    assert {"path": "b.c", "old_value": 2, "new_value": 3} in diff["changed"]


def test_compute_diff_is_deterministic():
    old = base_evidence()
    new = base_evidence()
    new["security"]["secrets"]["findings"].append(
        {
            "path": "c.py",
            "pattern": "generic_credential_assignment",
            "match_preview": "test****...cret",
            "likely_placeholder": True,
        }
    )

    first = compute_diff(old, new)
    second = compute_diff(old, new)

    assert first == second
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
