import os
import subprocess
from pathlib import Path

from veridion.secrets import find_secrets_in_history


def run(repo: Path, *args: str):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def commit(repo: Path, message: str, date: str):
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = date
    env["GIT_COMMITTER_DATE"] = date
    subprocess.run(
        ["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True, env=env
    )


def head_hash(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run(repo, "init", "-b", "main")
    run(repo, "config", "user.email", "a@example.com")
    run(repo, "config", "user.name", "Alice")
    return repo


def test_find_secrets_in_history_finds_a_secret_added_then_removed(tmp_path):
    repo = init_repo(tmp_path)

    (repo / "main.py").write_text("x = 1\n")
    run(repo, "add", "main.py")
    commit(repo, "first", "2026-06-01T00:00:00+00:00")

    (repo / "main.py").write_text('x = 1\nAWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')
    run(repo, "add", "main.py")
    commit(repo, "add key", "2026-06-02T00:00:00+00:00")
    add_key_commit = head_hash(repo)

    (repo / "main.py").write_text("x = 1\n")
    run(repo, "add", "main.py")
    commit(repo, "remove key", "2026-06-03T00:00:00+00:00")

    result = find_secrets_in_history(repo)

    assert len(result["history_findings"]) == 1
    finding = result["history_findings"][0]
    assert finding["commit"] == add_key_commit
    assert finding["path"] == "main.py"
    assert finding["pattern"] == "aws_access_key_id"
    assert "AKIAABCDEFGHIJKLMNOP" not in finding["match_preview"]
    assert finding["match_preview"].startswith("AKIA")
    assert finding["likely_placeholder"] is False
    assert result["history_scanned_commits"] == 3


def test_find_secrets_in_history_does_not_scan_merge_commit_diffs(tmp_path):
    repo = init_repo(tmp_path)

    (repo / "a.txt").write_text("base\n")
    run(repo, "add", "a.txt")
    commit(repo, "base", "2026-06-01T00:00:00+00:00")

    run(repo, "checkout", "-b", "feature")
    (repo / "secret.py").write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')
    run(repo, "add", "secret.py")
    commit(repo, "add secret on feature branch", "2026-06-02T00:00:00+00:00")

    run(repo, "checkout", "main")
    (repo / "other.py").write_text("y = 2\n")
    run(repo, "add", "other.py")
    commit(repo, "unrelated main work", "2026-06-03T00:00:00+00:00")

    run(repo, "merge", "feature", "-m", "merge feature", "--no-edit")

    result = find_secrets_in_history(repo)

    assert len(result["history_findings"]) == 1


def test_find_secrets_in_history_returns_zero_when_no_commits(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    run(repo, "init", "-b", "main")

    result = find_secrets_in_history(repo)

    assert result == {"history_scanned_commits": 0, "history_findings": []}
