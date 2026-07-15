import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from veridion.git_intel.analyzer import analyze_git


def run(repo: Path, *args: str):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def commit(repo: Path, message: str, date: str):
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = date
    env["GIT_COMMITTER_DATE"] = date
    subprocess.run(
        ["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True, env=env
    )


def make_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run(repo, "init", "-b", "main")
    run(repo, "config", "user.email", "a@example.com")
    run(repo, "config", "user.name", "Alice")

    (repo / "a.txt").write_text("1")
    run(repo, "add", "a.txt")
    commit(repo, "first", "2026-06-01T00:00:00+00:00")

    (repo / "a.txt").write_text("2")
    run(repo, "add", "a.txt")
    commit(repo, "second", "2026-06-15T00:00:00+00:00")

    run(repo, "checkout", "-b", "feature/old")
    (repo / "b.txt").write_text("1")
    run(repo, "add", "b.txt")
    commit(repo, "feature work", "2026-06-16T00:00:00+00:00")
    run(repo, "checkout", "main")

    run(repo, "config", "user.name", "Bob")
    run(repo, "config", "user.email", "b@example.com")
    (repo / "a.txt").write_text("3")
    run(repo, "add", "a.txt")
    commit(repo, "third", "2026-07-01T00:00:00+00:00")

    return repo


def test_analyze_git_no_history_returns_unavailable(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    run(repo, "init", "-b", "main")
    result = analyze_git(repo)
    assert result == {"available": False}


def test_analyze_git_not_a_repo_returns_unavailable(tmp_path):
    repo = tmp_path / "not_a_repo"
    repo.mkdir()
    result = analyze_git(repo)
    assert result == {"available": False}


def test_analyze_git_branches_and_staleness(tmp_path):
    repo = make_git_repo(tmp_path)
    now = datetime(2026, 7, 14, tzinfo=timezone.utc)
    result = analyze_git(repo, now=now)
    assert result["available"] is True

    by_name = {b["name"]: b for b in result["branches"]}
    assert "main" in by_name
    assert by_name["main"]["type"] == "local"
    assert by_name["main"]["stale_days"] == 13

    assert "feature/old" in by_name
    assert by_name["feature/old"]["stale_days"] == 28


def test_analyze_git_ownership(tmp_path):
    repo = make_git_repo(tmp_path)
    result = analyze_git(repo, now=datetime(2026, 7, 14, tzinfo=timezone.utc))
    by_email = {o["email"]: o for o in result["ownership"]}
    assert by_email["a@example.com"]["commit_count"] == 2
    assert by_email["a@example.com"]["names"] == ["Alice"]
    assert by_email["b@example.com"]["commit_count"] == 1
    assert by_email["a@example.com"]["percent"] == 0.6667


def test_analyze_git_ownership_merges_same_email_different_names(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run(repo, "init", "-b", "main")
    run(repo, "config", "user.email", "person@example.com")
    run(repo, "config", "user.name", "Nick")
    (repo / "a.txt").write_text("1")
    run(repo, "add", "a.txt")
    commit(repo, "first", "2026-06-01T00:00:00+00:00")

    run(repo, "config", "user.name", "Nicholas Smith")
    (repo / "a.txt").write_text("2")
    run(repo, "add", "a.txt")
    commit(repo, "second", "2026-06-02T00:00:00+00:00")

    result = analyze_git(repo, now=datetime(2026, 7, 14, tzinfo=timezone.utc))
    assert len(result["ownership"]) == 1
    entry = result["ownership"][0]
    assert entry["email"] == "person@example.com"
    assert entry["names"] == ["Nicholas Smith", "Nick"]
    assert entry["commit_count"] == 2


def test_analyze_git_ownership_merges_same_email_different_case(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run(repo, "init", "-b", "main")
    run(repo, "config", "user.email", "Person@Example.com")
    run(repo, "config", "user.name", "Nick")
    (repo / "a.txt").write_text("1")
    run(repo, "add", "a.txt")
    commit(repo, "first", "2026-06-01T00:00:00+00:00")

    run(repo, "config", "user.email", "person@example.com")
    (repo / "a.txt").write_text("2")
    run(repo, "add", "a.txt")
    commit(repo, "second", "2026-06-02T00:00:00+00:00")

    result = analyze_git(repo, now=datetime(2026, 7, 14, tzinfo=timezone.utc))
    assert len(result["ownership"]) == 1
    assert result["ownership"][0]["commit_count"] == 2


def test_analyze_git_ahead_behind_main(tmp_path):
    repo = make_git_repo(tmp_path)
    result = analyze_git(repo, now=datetime(2026, 7, 14, tzinfo=timezone.utc))
    by_name = {b["name"]: b for b in result["branches"]}
    assert by_name["main"]["ahead_of_main"] == 0
    assert by_name["main"]["behind_main"] == 0
    assert by_name["feature/old"]["ahead_of_main"] == 1
    assert by_name["feature/old"]["behind_main"] == 1


def test_analyze_git_commit_cadence_partial_week_flag(tmp_path):
    repo = make_git_repo(tmp_path)
    result_partial = analyze_git(repo, now=datetime(2026, 7, 4, tzinfo=timezone.utc))
    assert result_partial["commit_cadence"]["most_recent_week_partial"] is True

    result_complete = analyze_git(repo, now=datetime(2026, 7, 20, tzinfo=timezone.utc))
    assert result_complete["commit_cadence"]["most_recent_week_partial"] is False


def test_analyze_git_totals(tmp_path):
    repo = make_git_repo(tmp_path)
    result = analyze_git(repo, now=datetime(2026, 7, 14, tzinfo=timezone.utc))
    assert result["total_commits"] == 3
    assert result["repo_age_days"] == 43


def test_analyze_git_ignores_remote_head_symbolic_ref(tmp_path):
    repo = make_git_repo(tmp_path)
    remote = tmp_path / "remote.git"
    run(remote.parent, "init", "--bare", remote.name)
    run(repo, "remote", "add", "origin", str(remote))
    run(repo, "push", "-u", "origin", "main")
    run(repo, "remote", "set-head", "origin", "main")

    result = analyze_git(repo, now=datetime(2026, 7, 14, tzinfo=timezone.utc))
    branch_names = {branch["name"] for branch in result["branches"]}

    assert "origin/main" in branch_names
    assert "origin" not in branch_names
    assert "origin/HEAD" not in branch_names
