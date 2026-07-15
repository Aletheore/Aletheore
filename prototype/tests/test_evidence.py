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
        evidence = scan_repository(repo)

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
    evidence = scan_repository(repo, check_vulnerabilities=False)
    assert evidence["git"] == {"available": False}


def test_write_evidence_creates_veridion_dir(tmp_path):
    repo = make_repo(tmp_path)
    evidence = scan_repository(repo, check_vulnerabilities=False)
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
        evidence = scan_repository(repo)

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
        evidence = scan_repository(repo, check_vulnerabilities=False)

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
        evidence = scan_repository(repo)

    assert "architecture" in evidence
    assert "clusters" in evidence["architecture"]
    assert "cross_cluster_edges" in evidence["architecture"]
    assert "layer_violations" in evidence["architecture"]
    assert evidence["architecture"]["layer_violations"]["convention_detected"] is False
