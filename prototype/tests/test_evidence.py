import json
import subprocess
from pathlib import Path

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
    evidence = scan_repository(repo)
    assert evidence["git"] == {"available": False}


def test_write_evidence_creates_veridion_dir(tmp_path):
    repo = make_repo(tmp_path)
    evidence = scan_repository(repo)
    written_path = write_evidence(evidence, repo)

    assert written_path == repo / ".veridion" / "evidence.json"
    assert written_path.exists()
    loaded = json.loads(written_path.read_text())
    assert loaded["veridion_version"] == "0.1.0"
