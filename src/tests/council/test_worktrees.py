import subprocess
from pathlib import Path

import pytest

from aletheore.council.worktrees import (
    changed_files,
    commit_all,
    create_worktree,
    files_outside_area,
    merge_branch,
)


def git(repo, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "my repo"  # a space on purpose
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "redis").mkdir()
    (r / "schema").mkdir()
    (r / "redis" / "a.py").write_text("a\n")
    (r / "schema" / "s.sql").write_text("s\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


def test_two_workers_get_separate_directories_and_branches(repo, tmp_path):
    p1, b1 = create_worktree(repo, "room1", "codex", tmp_path / "wt")
    p2, b2 = create_worktree(repo, "room1", "claude", tmp_path / "wt")
    assert p1 != p2 and b1 != b2
    assert p1.is_dir() and p2.is_dir()
    assert git(p1, "branch", "--show-current") == b1
    assert git(p2, "branch", "--show-current") == b2


def test_files_outside_area_detects_escape():
    assert files_outside_area(["redis/a.py", "schema/b.sql"], ["redis/**"]) == ["schema/b.sql"]
    assert files_outside_area(["redis/x/y.py"], ["redis/**"]) == []


def test_changed_files_uses_forward_slashes(repo, tmp_path):
    p, b = create_worktree(repo, "room1", "codex", tmp_path / "wt")
    (p / "redis" / "deep").mkdir()
    (p / "redis" / "deep" / "n.py").write_text("n\n")
    commit_all(p, "work")
    assert changed_files(p, "council/room1/main") == ["redis/deep/n.py"]


def test_merge_clean_branch_ok(repo, tmp_path):
    p, b = create_worktree(repo, "room1", "codex", tmp_path / "wt")
    (p / "redis" / "n.py").write_text("n\n")
    commit_all(p, "work")
    res = merge_branch(repo, "room1", b)
    assert res.ok and res.conflicts == []
    assert "redis/n.py" in git(repo, "ls-tree", "-r", "--name-only", "council/room1/main")


def test_merge_conflict_reports_files_and_leaves_repo_clean(repo, tmp_path):
    p1, b1 = create_worktree(repo, "room1", "codex", tmp_path / "wt")
    p2, b2 = create_worktree(repo, "room1", "claude", tmp_path / "wt")
    (p1 / "schema" / "s.sql").write_text("one\n")
    commit_all(p1, "one")
    (p2 / "schema" / "s.sql").write_text("two\n")
    commit_all(p2, "two")
    assert merge_branch(repo, "room1", b1).ok
    res = merge_branch(repo, "room1", b2)
    assert not res.ok and res.conflicts == ["schema/s.sql"]
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "worktree", "list").count("\n") == 2  # main repo + 2 worker worktrees only


def test_users_checked_out_branch_untouched(repo, tmp_path):
    before = (git(repo, "rev-parse", "HEAD"), git(repo, "branch", "--show-current"))
    p, b = create_worktree(repo, "room1", "codex", tmp_path / "wt")
    (p / "redis" / "n.py").write_text("n\n")
    commit_all(p, "work")
    merge_branch(repo, "room1", b)
    assert (git(repo, "rev-parse", "HEAD"), git(repo, "branch", "--show-current")) == before
    assert not (repo / "redis" / "n.py").exists()


def test_worktree_path_with_spaces_works(repo, tmp_path):
    p, _ = create_worktree(repo, "room1", "codex", tmp_path / "work trees")
    assert " " in str(p) and (p / "redis" / "a.py").exists()


def test_rename_across_areas_is_reported_not_hidden(repo, tmp_path):
    p, b = create_worktree(repo, "room1", "codex", tmp_path / "wt")
    git(p, "mv", "schema/s.sql", "redis/s.sql")  # moves another area's file into this one
    commit_all(p, "sneaky move")
    changed = changed_files(p, "council/room1/main")
    assert "schema/s.sql" in changed and "redis/s.sql" in changed
    assert files_outside_area(changed, ["redis/**"]) == ["schema/s.sql"]


@pytest.mark.skipif(__import__("sys").platform == "win32", reason="posix hook script")
def test_council_git_calls_do_not_run_repo_hooks_or_see_parent_secrets(repo, tmp_path, monkeypatch):
    hooks = repo / ".githooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text('#!/bin/sh\necho "$SECRET_PARENT_KEY" > "$PWD/leaked.txt"\ntouch "$PWD/hook-ran.txt"\n')
    hook.chmod(0o755)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "add hook")
    git(repo, "config", "core.hooksPath", ".githooks")  # relative: resolves inside each worktree
    monkeypatch.setenv("SECRET_PARENT_KEY", "topsecret")
    p, b = create_worktree(repo, "room1", "codex", tmp_path / "wt")
    (p / "redis" / "n.py").write_text("n\n")
    commit_all(p, "work")
    assert not (p / "hook-ran.txt").exists() and not (p / "leaked.txt").exists()
    assert "redis/n.py" in git(repo, "ls-tree", "-r", "--name-only", b)
