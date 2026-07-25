import os
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from aletheore.git_intel.graph_store import CommitTouch, GraphSnapshot
from aletheore.git_intel.incremental import (
    RECENT_COMMITS_PER_FILE,
    GitLogStreamError,
    compute_repo_key,
    fold,
    stream_commit_touches,
)


def run(repo: Path, *args: str):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def commit(repo: Path, message: str, date_str: str):
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = date_str
    env["GIT_COMMITTER_DATE"] = date_str
    subprocess.run(["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True, env=env)


def head_sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run(repo, "init", "-b", "main")
    run(repo, "config", "user.email", "a@example.com")
    run(repo, "config", "user.name", "Alice")
    return repo


def _touch(sha, name, email, date_str, files):
    return CommitTouch(sha, name, email, datetime.fromisoformat(date_str), files)


# --- stream_commit_touches: reads real git output, never buffers it whole ---


def test_stream_commit_touches_reads_sha_author_date_and_files(tmp_path):
    repo = init_repo(tmp_path)
    (repo / "a.txt").write_text("1")
    run(repo, "add", "a.txt")
    commit(repo, "first", "2026-06-01T00:00:00+00:00")

    touches = list(stream_commit_touches(repo, "HEAD"))
    assert len(touches) == 1
    touch = touches[0]
    assert touch.sha == head_sha(repo)
    assert touch.author_name == "Alice"
    assert touch.author_email == "a@example.com"
    assert touch.committed_at == datetime.fromisoformat("2026-06-01T00:00:00+00:00")
    assert touch.files == ("a.txt",)


def test_stream_commit_touches_orders_newest_first(tmp_path):
    repo = init_repo(tmp_path)
    (repo / "a.txt").write_text("1")
    run(repo, "add", "a.txt")
    commit(repo, "first", "2026-06-01T00:00:00+00:00")
    (repo / "a.txt").write_text("2")
    run(repo, "add", "a.txt")
    commit(repo, "second", "2026-06-02T00:00:00+00:00")

    touches = list(stream_commit_touches(repo, "HEAD"))
    assert len(touches) == 2
    assert touches[0].committed_at > touches[1].committed_at


def test_stream_commit_touches_respects_rev_range(tmp_path):
    repo = init_repo(tmp_path)
    (repo / "a.txt").write_text("1")
    run(repo, "add", "a.txt")
    commit(repo, "first", "2026-06-01T00:00:00+00:00")
    first_sha = head_sha(repo)
    (repo / "b.txt").write_text("1")
    run(repo, "add", "b.txt")
    commit(repo, "second", "2026-06-02T00:00:00+00:00")

    touches = list(stream_commit_touches(repo, f"{first_sha}..HEAD"))
    assert len(touches) == 1
    assert touches[0].files == ("b.txt",)


def test_stream_commit_touches_respects_max_commits(tmp_path):
    repo = init_repo(tmp_path)
    for i in range(5):
        (repo / "a.txt").write_text(str(i))
        run(repo, "add", "a.txt")
        commit(repo, f"commit {i}", f"2026-06-0{i + 1}T00:00:00+00:00")

    touches = list(stream_commit_touches(repo, "HEAD", max_commits=2))
    assert len(touches) == 2
    # -n limits to the newest N, matching git's own semantics.
    assert touches[0].committed_at > touches[1].committed_at


def test_stream_commit_touches_raises_on_bad_rev_range(tmp_path):
    repo = init_repo(tmp_path)
    (repo / "a.txt").write_text("1")
    run(repo, "add", "a.txt")
    commit(repo, "first", "2026-06-01T00:00:00+00:00")

    with pytest.raises(GitLogStreamError):
        list(stream_commit_touches(repo, "not-a-real-ref"))


def test_stream_commit_touches_handles_merge_commit_with_no_file_changes(tmp_path):
    # A --no-ff merge that isn't resolving any conflict produces zero
    # --name-only lines for that commit - the parser must not silently drop
    # it or merge its (absent) files into the next commit's list.
    repo = init_repo(tmp_path)
    (repo / "a.txt").write_text("1")
    run(repo, "add", "a.txt")
    commit(repo, "first", "2026-06-01T00:00:00+00:00")
    run(repo, "checkout", "-b", "feature")
    (repo / "b.txt").write_text("1")
    run(repo, "add", "b.txt")
    commit(repo, "feature commit", "2026-06-02T00:00:00+00:00")
    run(repo, "checkout", "main")
    run(repo, "merge", "feature", "--no-ff", "--no-edit")

    touches = list(stream_commit_touches(repo, "HEAD"))
    assert len(touches) == 3


# --- fold: pure aggregation, must be additive for incremental correctness ---


def test_fold_aggregates_ownership_case_insensitively():
    commits = [
        _touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00+00:00", ("a.txt",)),
        _touch("s2", "Alice", "A@EXAMPLE.COM", "2026-06-02T00:00:00+00:00", ("a.txt",)),
        _touch("s3", "Bob", "b@example.com", "2026-06-03T00:00:00+00:00", ("b.txt",)),
    ]
    result = fold(GraphSnapshot.empty(), commits)
    assert result.ownership["a@example.com"].commit_count == 2
    assert result.ownership["a@example.com"].names == {"Alice"}
    assert result.ownership["b@example.com"].commit_count == 1


def test_fold_tracks_file_churn_and_co_change():
    commits = [
        _touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00+00:00", ("a.txt", "b.txt")),
        _touch("s2", "Alice", "a@example.com", "2026-06-02T00:00:00+00:00", ("a.txt",)),
    ]
    result = fold(GraphSnapshot.empty(), commits)
    assert result.file_churn["a.txt"].churn_count == 2
    assert result.file_churn["b.txt"].churn_count == 1
    assert result.file_churn["a.txt"].co_change_counts == {"b.txt": 1}
    assert result.file_churn["b.txt"].co_change_counts == {"a.txt": 1}


def test_fold_skips_co_change_for_mass_commits():
    files = tuple(f"f{i}.txt" for i in range(60))  # over MASS_COMMIT_FILE_THRESHOLD (50)
    commits = [_touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00+00:00", files)]
    result = fold(GraphSnapshot.empty(), commits)
    assert result.file_churn["f0.txt"].churn_count == 1
    assert result.file_churn["f0.txt"].co_change_counts == {}


def test_fold_caps_recent_commits_per_file_newest_first():
    commits = [
        _touch(f"s{i}", "Alice", "a@example.com", f"2026-06-{i + 1:02d}T00:00:00+00:00", ("a.txt",))
        for i in range(15)
    ]
    result = fold(GraphSnapshot.empty(), commits)
    recent = result.file_churn["a.txt"].recent_commits
    assert len(recent) == RECENT_COMMITS_PER_FILE
    assert recent[0].sha == "s14"
    assert result.file_churn["a.txt"].churn_count == 15  # the count isn't capped, only the list


def test_fold_buckets_cadence_by_calendar_week():
    day1 = datetime(2026, 6, 1)
    day2 = day1 + timedelta(days=2)  # same ISO week
    day3 = day1 + timedelta(days=9)  # next ISO week
    commits = [
        _touch("s1", "Alice", "a@example.com", day1.isoformat(), ("a.txt",)),
        _touch("s2", "Alice", "a@example.com", day2.isoformat(), ("a.txt",)),
        _touch("s3", "Alice", "a@example.com", day3.isoformat(), ("a.txt",)),
    ]
    result = fold(GraphSnapshot.empty(), commits)
    week1_start = date(2026, 6, 1) - timedelta(days=date(2026, 6, 1).weekday())
    week2_start = week1_start + timedelta(days=7)
    assert result.cadence_weekly_counts[week1_start] == 2
    assert result.cadence_weekly_counts[week2_start] == 1


def test_fold_is_additive_across_batches():
    # The property that makes incremental scanning correct: folding two
    # separate batches (baseline, then a later delta) must equal folding
    # everything in one pass, or a delta scan would silently drift from
    # what a full rescan would have found.
    commits = [
        _touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00+00:00", ("a.txt", "b.txt")),
        _touch("s2", "Bob", "b@example.com", "2026-06-02T00:00:00+00:00", ("a.txt",)),
        _touch("s3", "Alice", "a@example.com", "2026-06-15T00:00:00+00:00", ("c.txt",)),
    ]
    combined = fold(GraphSnapshot.empty(), commits)
    incremental = fold(fold(GraphSnapshot.empty(), commits[:2]), commits[2:])

    assert combined.ownership.keys() == incremental.ownership.keys()
    for email in combined.ownership:
        assert combined.ownership[email].commit_count == incremental.ownership[email].commit_count
        assert combined.ownership[email].names == incremental.ownership[email].names

    assert combined.cadence_weekly_counts == incremental.cadence_weekly_counts

    assert combined.file_churn.keys() == incremental.file_churn.keys()
    for path in combined.file_churn:
        assert combined.file_churn[path].churn_count == incremental.file_churn[path].churn_count
        assert combined.file_churn[path].co_change_counts == incremental.file_churn[path].co_change_counts


def test_fold_does_not_mutate_the_input_snapshot():
    # Callers may hold a reference to the pre-fold snapshot (e.g. to compare
    # before/after) - fold() must return a new object, never edit in place.
    original = fold(GraphSnapshot.empty(), [_touch("s1", "Alice", "a@example.com", "2026-06-01T00:00:00+00:00", ("a.txt",))])
    fold(original, [_touch("s2", "Bob", "b@example.com", "2026-06-02T00:00:00+00:00", ("b.txt",))])

    assert original.ownership.keys() == {"a@example.com"}
    assert original.file_churn.keys() == {"a.txt"}


# --- compute_repo_key: stable identity, independent of clone directory ---


def test_compute_repo_key_stable_for_same_repo(tmp_path):
    repo = init_repo(tmp_path)
    (repo / "a.txt").write_text("1")
    run(repo, "add", "a.txt")
    commit(repo, "first", "2026-06-01T00:00:00+00:00")

    assert compute_repo_key(repo) == compute_repo_key(repo)


def test_compute_repo_key_differs_for_different_repos(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    repo_a = init_repo(tmp_path / "a")
    (repo_a / "f.txt").write_text("1")
    run(repo_a, "add", "f.txt")
    commit(repo_a, "first", "2026-06-01T00:00:00+00:00")

    repo_b = init_repo(tmp_path / "b")
    (repo_b / "f.txt").write_text("1")
    run(repo_b, "add", "f.txt")
    commit(repo_b, "first", "2026-06-01T00:00:00+00:00")

    assert compute_repo_key(repo_a) != compute_repo_key(repo_b)


def test_compute_repo_key_uses_remote_when_present(tmp_path):
    repo = init_repo(tmp_path)
    (repo / "a.txt").write_text("1")
    run(repo, "add", "a.txt")
    commit(repo, "first", "2026-06-01T00:00:00+00:00")

    key_without_remote = compute_repo_key(repo)
    run(repo, "remote", "add", "origin", "https://github.com/example/repo.git")
    key_with_remote = compute_repo_key(repo)

    assert key_without_remote != key_with_remote
    assert "https://github.com/example/repo.git" in key_with_remote
