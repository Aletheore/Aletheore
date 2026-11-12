import subprocess
import sys

import pytest

from aletheore.council import runners
from aletheore.council.bundle_transport import create_bundle, import_bundle
from aletheore.council.worktrees import changed_files, commit_all, create_worktree, files_outside_area


def git(repo, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def test_kill_process_tree_windows_branch_uses_taskkill(monkeypatch):
    calls = []
    monkeypatch.setattr(runners.sys, "platform", "win32")
    monkeypatch.setattr(runners.subprocess, "run", lambda argv, **kw: calls.append(argv))
    runners.kill_process_tree(1234)
    assert calls == [["taskkill", "/T", "/F", "/PID", "1234"]]


def test_platform_paths_with_spaces_round_trip(tmp_path):
    repo = tmp_path / "my project dir"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "redis").mkdir()
    (repo / "redis" / "a b.py").write_text("x\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    base = git(repo, "rev-parse", "HEAD")
    wt, branch = create_worktree(repo, "room1", "codex", tmp_path / "work trees")
    (wt / "redis" / "new file.py").write_text("n\n")
    commit_all(wt, "work")
    assert changed_files(wt, "council/room1/main") == ["redis/new file.py"]
    assert files_outside_area(["redis/new file.py"], ["redis/**"]) == []
    out = tmp_path / "bundle out dir" / "w b.bundle"
    info = create_bundle(repo, branch, base, out)
    clone = tmp_path / "other clone"
    git(tmp_path, "clone", "-q", str(repo), str(clone))
    assert import_bundle(clone, "bob", branch, out, info.sha256).startswith("refs/council/peers/bob/")


def test_no_posix_only_calls_outside_platform_checks():
    import ast
    import pathlib

    root = pathlib.Path(runners.__file__).parent
    banned = {"killpg", "getpgid", "fork", "geteuid", "getuid", "setsid"}
    for py in root.glob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in banned:
                # allowed only in runners.kill_process_tree, behind the win32 check
                assert py.name == "runners.py", f"{py.name} uses {node.attr}"
