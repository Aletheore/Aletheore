"""The base-commit checkout of a PR scan borrows objects the persistent checkout already has.

Before, every PR scan fetched the base commit's full history over the network into an empty directory
(the scan walks all of it, so it cannot be shallow), then checked it out. The head side already keeps a
persistent checkout per repo, so the base side can reuse its object store through git alternates and skip
the fetch. Real git repositories are used throughout: the behaviour that matters is what git does.
"""
import os
import subprocess
from pathlib import Path

import pytest

from scan_worker.jobs import _checkout_from_local_objects, _prepare_base_checkout

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
}


def _git(*args, cwd) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=_GIT_ENV).stdout.strip()


@pytest.fixture
def source_repo(tmp_path) -> tuple[Path, str, str]:
    """A repo with two commits; returns (path, first_sha, second_sha)."""
    repo = tmp_path / "persistent"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    (repo / "a.txt").write_text("one")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "first", cwd=repo)
    first = _git("rev-parse", "HEAD", cwd=repo)
    (repo / "a.txt").write_text("two")
    (repo / "b.txt").write_text("new file")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "second", cwd=repo)
    return repo, first, _git("rev-parse", "HEAD", cwd=repo)


def test_checkout_from_local_objects_checks_out_the_commit_without_fetching(tmp_path, source_repo):
    repo, first, _second = source_repo
    dest = tmp_path / "job" / "base"

    assert _checkout_from_local_objects(repo, first, dest) is True

    assert (dest / "a.txt").read_text() == "one"
    assert not (dest / "b.txt").exists()
    alternates = (dest / ".git" / "objects" / "info" / "alternates").read_text().strip()
    assert alternates == str(repo / ".git" / "objects")
    assert _git("remote", cwd=dest) == "", "nothing to fetch from, and no URL for a token to be written into"


def test_the_borrowed_checkout_sees_only_the_base_commits_ancestry(tmp_path, source_repo):
    # The scan walks `git log --all`-style history for ownership and secrets. The old base checkout held exactly
    # one ref's ancestry; a clone of the persistent repo would also carry every branch and the newer commit,
    # and the base evidence would silently include them. Borrowing objects, not refs, keeps it identical.
    repo, first, second = source_repo
    dest = tmp_path / "job" / "base"

    _checkout_from_local_objects(repo, first, dest)

    assert _git("rev-list", "--all", cwd=dest).split() == [first]
    assert second not in _git("rev-list", "--all", cwd=dest)


def test_checkout_from_local_objects_returns_false_and_leaves_nothing_when_the_commit_is_missing(tmp_path, source_repo):
    repo, _first, _second = source_repo
    dest = tmp_path / "job" / "base"

    assert _checkout_from_local_objects(repo, "0" * 40, dest) is False

    assert not dest.exists()


def test_prepare_base_checkout_uses_the_persistent_objects_and_never_touches_the_network(tmp_path, source_repo, monkeypatch):
    repo, first, _second = source_repo
    dest = tmp_path / "job" / "base"

    def _no_network(*args, **kwargs):
        raise AssertionError("must not fetch the base commit again when the persistent checkout has it")

    monkeypatch.setattr("scan_worker.jobs._clone_ref", _no_network)

    _prepare_base_checkout("https://x-access-token:tok@github.com/o/r.git", first, dest, persistent_dir=repo)

    assert (dest / "a.txt").read_text() == "one"


def test_prepare_base_checkout_falls_back_to_a_fresh_fetch_without_a_persistent_checkout(tmp_path, monkeypatch):
    # The persistent checkout is itself a best-effort optimisation (see _prepare_head_checkout): when it was
    # unavailable the head side fell back to an ephemeral clone, and the base side must too.
    calls = []
    monkeypatch.setattr("scan_worker.jobs._clone_ref", lambda url, ref, dest, pr_number=None: calls.append((url, ref, dest)))
    dest = tmp_path / "job" / "base"

    _prepare_base_checkout("https://github.com/o/r.git", "abc123", dest, persistent_dir=None)

    assert calls == [("https://github.com/o/r.git", "abc123", dest)]


def test_prepare_base_checkout_falls_back_when_the_persistent_checkout_lacks_the_commit(tmp_path, source_repo, monkeypatch):
    # E.g. the base branch was force-pushed after the PR was opened, so the old base is no longer reachable.
    repo, _first, _second = source_repo
    calls = []
    monkeypatch.setattr("scan_worker.jobs._clone_ref", lambda url, ref, dest, pr_number=None: calls.append(ref))
    dest = tmp_path / "job" / "base"

    _prepare_base_checkout("https://github.com/o/r.git", "f" * 40, dest, persistent_dir=repo)

    assert calls == ["f" * 40]
