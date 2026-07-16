import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from veridion.evidence import scan_repository, write_evidence


def run(repo: Path, *args: str):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("def hello():\n    return 1\n")
    (repo / "requirements.txt").write_text("fastapi==0.110.0\n")
    run(repo, "init", "-b", "main")
    run(repo, "config", "user.email", "a@example.com")
    run(repo, "config", "user.name", "Alice")
    run(repo, "add", ".")
    run(repo, "commit", "-m", "init")
    return repo


def test_scan_repository_produces_full_schema(tmp_path):
    repo = make_repo(tmp_path)
    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False)

    assert evidence["veridion_version"] == "0.1.0"
    assert "scanned_at" in evidence
    assert evidence["repo_path"] == str(repo)

    assert any(entry["name"] == "python" for entry in evidence["repository"]["languages"])
    assert any(entry["name"] == "fastapi" for entry in evidence["repository"]["frameworks"])
    assert evidence["repository"]["modules"][0]["path"] == "main.py"

    assert evidence["git"]["available"] is True
    assert evidence["git"]["total_commits"] == 1


def test_scan_repository_handles_no_git_history(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")
    evidence = scan_repository(repo, check_vulnerabilities=False, check_licenses=False)
    assert evidence["git"] == {"available": False}


def test_write_evidence_creates_veridion_dir(tmp_path):
    repo = make_repo(tmp_path)
    evidence = scan_repository(repo, check_vulnerabilities=False, check_licenses=False)
    written_path = write_evidence(evidence, repo)

    assert written_path == repo / ".veridion" / "evidence.json"
    assert written_path.exists()
    loaded = json.loads(written_path.read_text())
    assert loaded["veridion_version"] == "0.1.0"


def test_scan_repository_includes_security_block(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")

    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False)

    assert "security" in evidence
    assert "secrets" in evidence["security"]
    assert evidence["security"]["secrets"]["scanned_files"] >= 1
    assert evidence["security"]["dependency_vulnerabilities"]["checked"] is True
    mock_check.assert_called_once()


def test_scan_repository_skips_vulnerability_check_when_disabled(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")

    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_check:
        evidence = scan_repository(repo, check_vulnerabilities=False, check_licenses=False)

    mock_check.assert_not_called()
    assert evidence["security"]["dependency_vulnerabilities"] == {
        "checked": False,
        "reason": "skipped (--no-check-vulnerabilities)",
        "findings": [],
    }


def test_scan_repository_includes_architecture_block(tmp_path):
    repo = tmp_path / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "__init__.py").write_text("")
    (repo / "app" / "a.py").write_text("from app import b\n")
    (repo / "app" / "b.py").write_text("x = 1\n")

    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False)

    assert "architecture" in evidence
    assert "clusters" in evidence["architecture"]
    assert "cross_cluster_edges" in evidence["architecture"]
    assert "layer_violations" in evidence["architecture"]
    assert evidence["architecture"]["layer_violations"]["convention_detected"] is False


def test_scan_repository_includes_ai_usage_in_repository_block(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("openai==1.30.0\n")
    (repo / "main.py").write_text("x = 1\n")

    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False)

    assert "ai_usage" in evidence["repository"]
    names = {p["name"] for p in evidence["repository"]["ai_usage"]["providers"]}
    assert "openai" in names


def test_scan_repository_includes_policy_docs_in_repository_block(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "LICENSE").write_text("MIT")
    (repo / "main.py").write_text("x = 1\n")

    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False)

    names = {d["name"] for d in evidence["repository"]["policy_docs"]}
    assert "license" in names


def test_scan_repository_includes_history_findings_in_secrets_block(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    (repo / "main.py").write_text("x = 1\n")

    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False)

    secrets = evidence["security"]["secrets"]
    assert "history_scanned_commits" in secrets
    assert "history_findings" in secrets


def test_scan_repository_skips_history_scan_when_disabled(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")

    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        with patch("veridion.evidence.find_secrets_in_history") as mock_history:
            evidence = scan_repository(repo, scan_git_history=False, check_licenses=False)

    mock_history.assert_not_called()
    secrets = evidence["security"]["secrets"]
    assert secrets["history_scanned_commits"] == 0
    assert secrets["history_findings"] == []


def test_scan_repository_applies_veridion_json_config(tmp_path):
    repo = tmp_path / "repo"
    (repo / "app" / "biz").mkdir(parents=True)
    (repo / "app" / "routers").mkdir(parents=True)
    (repo / "app" / "biz" / "order.py").write_text("x = 1\n")
    (repo / "app" / "routers" / "orders.py").write_text("from app.biz import order\n")
    (repo / ".veridion.json").write_text('{"layer_markers": {"biz": 1}}')

    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, scan_git_history=False, check_licenses=False)

    assert evidence["architecture"]["config_applied"] == {
        "layer_markers": {"biz": 1},
        "cluster_resolution": 1.0,
    }
    assert evidence["architecture"]["layer_violations"]["convention_detected"] is True


def test_scan_repository_config_applied_is_none_without_veridion_json(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")

    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, scan_git_history=False, check_licenses=False)

    assert evidence["architecture"]["config_applied"] is None


def test_scan_repository_applies_a_secrets_baseline_end_to_end(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.py").write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')

    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        first_scan = scan_repository(repo, scan_git_history=False, check_licenses=False)

    finding = first_scan["security"]["secrets"]["findings"][0]
    assert finding["accepted"] is False

    (repo / ".veridion.json").write_text(
        json.dumps(
            {
                "accepted_secrets": [
                    {
                        "path": finding["path"],
                        "pattern": finding["pattern"],
                        "match_preview": finding["match_preview"],
                    }
                ]
            }
        )
    )

    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_check:
        mock_check.return_value = {"checked": True, "reason": None, "findings": []}
        second_scan = scan_repository(repo, scan_git_history=False, check_licenses=False)

    assert second_scan["security"]["secrets"]["findings"][0]["accepted"] is True


def test_scan_repository_includes_dependency_licenses_block(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")

    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_vuln:
        mock_vuln.return_value = {"checked": True, "reason": None, "findings": []}
        with patch("veridion.evidence.check_dependency_licenses") as mock_licenses:
            mock_licenses.return_value = {
                "checked": True,
                "reason": None,
                "repo_license": {"category": "permissive", "detected_from": "LICENSE text match"},
                "findings": [],
            }
            evidence = scan_repository(repo)

    mock_licenses.assert_called_once()
    assert evidence["security"]["dependency_licenses"]["checked"] is True
    assert evidence["security"]["dependency_licenses"]["repo_license"]["category"] == "permissive"


def test_scan_repository_skips_license_check_when_disabled(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("x = 1\n")

    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_vuln:
        mock_vuln.return_value = {"checked": True, "reason": None, "findings": []}
        with patch("veridion.evidence.check_dependency_licenses") as mock_licenses:
            evidence = scan_repository(repo, check_licenses=False)

    mock_licenses.assert_not_called()
    assert evidence["security"]["dependency_licenses"] == {
        "checked": False,
        "reason": "skipped (--no-check-licenses)",
        "repo_license": {"category": "unknown", "detected_from": None},
        "findings": [],
    }


def test_scan_repository_includes_api_endpoints_block(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text('@app.route("/users")\ndef list_users():\n    pass\n')

    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_vuln:
        mock_vuln.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False)

    assert evidence["repository"]["api_endpoints"]["checked"] is True
    paths = {e["path"] for e in evidence["repository"]["api_endpoints"]["endpoints"]}
    assert "/users" in paths


def test_scan_repository_skips_endpoint_mapping_when_disabled(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text('@app.route("/users")\ndef list_users():\n    pass\n')

    with patch("veridion.evidence.check_dependency_vulnerabilities") as mock_vuln:
        mock_vuln.return_value = {"checked": True, "reason": None, "findings": []}
        evidence = scan_repository(repo, check_licenses=False, map_endpoints=False)

    assert evidence["repository"]["api_endpoints"] == {
        "checked": False,
        "reason": "skipped (--no-map-endpoints)",
        "endpoints": [],
    }
