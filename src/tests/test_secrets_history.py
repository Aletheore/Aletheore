import os
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import patch

from aletheore.secrets import find_secrets_in_history


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


def test_find_secrets_in_history_max_commits_limits_scanned_range(tmp_path):
    # Bounds `git log -p`'s cost - full diffs are far more expensive per
    # commit than the git-graph engine's --name-only walk, confirmed by
    # direct measurement (~2s / ~1.4MB of diff text per 1000 commits on
    # torvalds/linux), so a hosted scan can't walk unbounded history here
    # either.
    repo = init_repo(tmp_path)
    for i in range(5):
        (repo / "main.py").write_text(f"x = {i}\n")
        run(repo, "add", "main.py")
        commit(repo, f"change {i}", f"2026-06-0{i + 1}T00:00:00+00:00")

    result = find_secrets_in_history(repo, max_commits=2)

    assert result["history_scanned_commits"] == 2


def test_find_secrets_in_history_returns_zero_when_no_commits(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    run(repo, "init", "-b", "main")

    result = find_secrets_in_history(repo)

    assert result == {"history_scanned_commits": 0, "history_findings": []}


def test_find_secrets_in_history_marks_a_baselined_finding_as_accepted(tmp_path):
    repo = init_repo(tmp_path)

    (repo / "main.py").write_text("x = 1\n")
    run(repo, "add", "main.py")
    commit(repo, "first", "2026-06-01T00:00:00+00:00")

    (repo / "main.py").write_text('x = 1\nAWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')
    run(repo, "add", "main.py")
    commit(repo, "add key", "2026-06-02T00:00:00+00:00")

    preview = find_secrets_in_history(repo)["history_findings"][0]["match_preview"]
    baseline = [{"path": "main.py", "pattern": "aws_access_key_id", "match_preview": preview}]

    result = find_secrets_in_history(repo, baseline=baseline)

    assert result["history_findings"][0]["accepted"] is True


def test_find_secrets_in_history_survives_a_stalled_git_process(tmp_path):
    # Simulates git blocking mid-read (e.g. a network-backed filesystem
    # stalling on blob reads) rather than exiting - reproduced directly as
    # a 7+ minute hang at ~0% CPU. A real `git log -p` stuck like this
    # produces no further output until killed, so the fake stdout iterator
    # below models that: it yields a couple of real lines, then blocks
    # until the watchdog's kill() call unblocks it, standing in for the
    # pipe going to EOF once the process is actually killed.
    repo = init_repo(tmp_path)

    class _FakeProcess:
        def __init__(self):
            self.stdout = self
            self.returncode = None
            self._killed = threading.Event()

        def __iter__(self):
            yield "COMMIT_START\x1fabc123\x1f2026-06-01T00:00:00+00:00\n"
            yield "+++ b/main.py\n"
            while not self._killed.wait(timeout=0.01):
                pass

        def kill(self):
            self._killed.set()

        def close(self):
            pass

        def wait(self):
            self.returncode = -9
            return self.returncode

    with patch("aletheore.secrets.subprocess.Popen", return_value=_FakeProcess()):
        start = time.monotonic()
        result = find_secrets_in_history(repo, timeout_seconds=0.1)
        elapsed = time.monotonic() - start

    assert result["history_scan_timed_out"] is True
    assert result["history_scanned_commits"] == 1
    assert elapsed < 5, "watchdog should have killed the stalled process well within 5s"


def test_find_secrets_in_history_always_includes_accepted_key_defaulting_false(tmp_path):
    repo = init_repo(tmp_path)

    (repo / "main.py").write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')
    run(repo, "add", "main.py")
    commit(repo, "add key", "2026-06-01T00:00:00+00:00")

    result = find_secrets_in_history(repo)

    assert result["history_findings"][0]["accepted"] is False
