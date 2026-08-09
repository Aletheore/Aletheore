import json
from pathlib import Path

from aletheore.history import compute_diff, list_snapshots, save_snapshot, to_sarif


def make_evidence(scanned_at: str) -> dict:
    return {"aletheore_version": "0.1.0", "scanned_at": scanned_at, "repo_path": "/tmp/repo"}


def test_save_snapshot_creates_history_dir_if_absent(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    save_snapshot(make_evidence("2026-07-15T10:00:00.000000+00:00"), repo)

    assert (repo / ".aletheore" / "history").is_dir()


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
            "api_endpoints": {
                "checked": True,
                "endpoints": [
                    {
                        "method": "GET",
                        "path": "/users",
                        "framework": "flask",
                        "file": "app.py",
                        "line": 1,
                        "handler": "list_users",
                        "unresolved": False,
                    }
                ],
            },
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
    assert diff["endpoints"] == {"new": [], "resolved": []}
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


def test_compute_diff_filters_new_vulnerabilities_by_severity_threshold(tmp_path):
    (tmp_path / ".aletheore.json").write_text(json.dumps({"severity_threshold": "high"}))

    old = base_evidence()
    old["repo_path"] = str(tmp_path)
    old["security"]["dependency_vulnerabilities"]["findings"] = []
    new = base_evidence()
    new["repo_path"] = str(tmp_path)
    # log4shell-shaped CVSS vector: base score 10.0, buckets "critical".
    new["security"]["dependency_vulnerabilities"]["findings"] = [
        {
            "ecosystem": "Maven",
            "package": "log4j-core",
            "installed_version": "2.14.1",
            "advisory_id": "GHSA-critical",
            "summary": "critical rce",
            "severity": [
                {"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H"}
            ],
        },
        {
            "ecosystem": "PyPI",
            "package": "some-lib",
            "installed_version": "1.0.0",
            "advisory_id": "GHSA-low",
            "summary": "low severity issue",
            "severity": [
                {"type": "CVSS_V3", "score": "CVSS:3.1/AV:L/AC:H/PR:H/UI:R/S:U/C:L/I:N/A:N"}
            ],
        },
    ]

    diff = compute_diff(old, new)

    new_advisory_ids = {f["advisory_id"] for f in diff["vulnerabilities"]["new"]}
    assert new_advisory_ids == {"GHSA-critical"}


def test_compute_diff_severity_threshold_never_touches_evidence_findings(tmp_path):
    # The filter only affects the diff's "new"/"resolved" lists - the raw
    # evidence.json findings list (what's actually persisted to disk by a
    # real scan) is a completely separate object and is never mutated.
    (tmp_path / ".aletheore.json").write_text(json.dumps({"severity_threshold": "critical"}))

    old = base_evidence()
    old["repo_path"] = str(tmp_path)
    old["security"]["dependency_vulnerabilities"]["findings"] = []
    new = base_evidence()
    new["repo_path"] = str(tmp_path)
    low_finding = {
        "ecosystem": "PyPI",
        "package": "some-lib",
        "installed_version": "1.0.0",
        "advisory_id": "GHSA-low",
        "summary": "low severity issue",
        "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:L/AC:H/PR:H/UI:R/S:U/C:L/I:N/A:N"}],
    }
    new["security"]["dependency_vulnerabilities"]["findings"] = [low_finding]

    compute_diff(old, new)

    assert new["security"]["dependency_vulnerabilities"]["findings"] == [low_finding]


def test_compute_diff_detects_a_new_layer_violation():
    old = base_evidence()
    new = base_evidence()
    new["architecture"]["layer_violations"]["violations"].append(
        {"from": "app/routers/x.py", "to": "app/domain/y.py", "reason": "y"}
    )

    diff = compute_diff(old, new)

    assert len(diff["layer_violations"]["new"]) == 1


def test_compute_diff_detects_a_new_endpoint():
    old = base_evidence()
    new = base_evidence()
    new["repository"]["api_endpoints"]["endpoints"].append(
        {
            "method": "POST",
            "path": "/users",
            "framework": "flask",
            "file": "app.py",
            "line": 5,
            "handler": "create_user",
            "unresolved": False,
        }
    )

    diff = compute_diff(old, new)

    assert len(diff["endpoints"]["new"]) == 1
    assert diff["endpoints"]["new"][0]["path"] == "/users"
    assert diff["endpoints"]["new"][0]["method"] == "POST"
    assert diff["endpoints"]["resolved"] == []


def test_compute_diff_detects_a_resolved_endpoint():
    old = base_evidence()
    new = base_evidence()
    new["repository"]["api_endpoints"]["endpoints"] = []

    diff = compute_diff(old, new)

    assert len(diff["endpoints"]["resolved"]) == 1
    assert diff["endpoints"]["new"] == []


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


def test_compute_diff_caveat_fires_when_endpoint_mapping_toggled():
    old = base_evidence()
    old["repository"]["api_endpoints"]["checked"] = False
    old["repository"]["api_endpoints"]["endpoints"] = []
    new = base_evidence()

    diff = compute_diff(old, new)

    assert "caveats" in diff
    assert any("endpoint" in c for c in diff["caveats"])


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


def test_to_sarif_has_valid_top_level_shape_with_no_findings():
    sarif = to_sarif({})

    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["tool"]["driver"]["name"] == "aletheore"
    assert sarif["runs"][0]["results"] == []


def test_to_sarif_renders_a_real_secret_with_error_level_and_location():
    curated = {
        "secrets": {
            "new": [
                {
                    "path": "config.py",
                    "line": 3,
                    "pattern": "aws_access_key_id",
                    "match_preview": "AKIA...MNOP",
                    "likely_placeholder": False,
                    "accepted": False,
                }
            ]
        }
    }

    results = to_sarif(curated)["runs"][0]["results"]

    assert len(results) == 1
    result = results[0]
    assert result["ruleId"] == "aletheore/secret"
    assert result["level"] == "error"
    assert "aws_access_key_id" in result["message"]["text"]
    assert result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "config.py"
    assert result["locations"][0]["physicalLocation"]["region"]["startLine"] == 3


def test_to_sarif_renders_a_placeholder_secret_at_note_level():
    curated = {
        "secrets": {
            "new": [
                {
                    "path": "tests/fixture.py",
                    "line": 1,
                    "pattern": "aws_access_key_id",
                    "match_preview": "AKIA...MPLE",
                    "likely_placeholder": True,
                    "accepted": False,
                }
            ]
        }
    }

    result = to_sarif(curated)["runs"][0]["results"][0]

    assert result["level"] == "note"


def test_to_sarif_history_secret_has_no_line_region():
    curated = {
        "history_secrets": {
            "new": [
                {
                    "commit": "abcdef1234567890",
                    "path": "old.py",
                    "pattern": "github_token",
                    "match_preview": "ghp_****...7890",
                    "likely_placeholder": False,
                    "accepted": False,
                }
            ]
        }
    }

    result = to_sarif(curated)["runs"][0]["results"][0]

    assert result["ruleId"] == "aletheore/secret-history"
    assert "abcdef123456" in result["message"]["text"]
    assert result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "old.py"
    assert "region" not in result["locations"][0]["physicalLocation"]


def test_to_sarif_renders_a_vulnerability_with_no_location():
    curated = {
        "vulnerabilities": {
            "new": [
                {
                    "ecosystem": "pip",
                    "package": "requests",
                    "advisory_id": "GHSA-xxxx",
                    "summary": "Improper certificate validation",
                }
            ]
        }
    }

    result = to_sarif(curated)["runs"][0]["results"][0]

    assert result["ruleId"] == "aletheore/dependency-vulnerability"
    assert "pip/requests" in result["message"]["text"]
    assert "GHSA-xxxx" in result["message"]["text"]
    assert "locations" not in result


def test_to_sarif_renders_a_layer_violation():
    curated = {
        "layer_violations": {
            "new": [
                {"from": "app/db.py", "to": "app/routes/billing.py", "reason": "inner layer imports outer layer"}
            ]
        }
    }

    result = to_sarif(curated)["runs"][0]["results"][0]

    assert result["ruleId"] == "aletheore/layer-violation"
    assert result["message"]["text"] == "inner layer imports outer layer"
    assert result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "app/db.py"


def test_to_sarif_ignores_resolved_findings():
    curated = {
        "secrets": {
            "new": [],
            "resolved": [
                {"path": "config.py", "line": 3, "pattern": "aws_access_key_id", "match_preview": "AKIA...MNOP"}
            ],
        }
    }

    assert to_sarif(curated)["runs"][0]["results"] == []
