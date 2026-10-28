import dataclasses
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from aletheore.shield import checkpoint as cp

T0 = datetime(2026, 10, 28, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _hermetic_git(tmp_path, monkeypatch):
    """Keep the machine's global and system git config, and any inherited repo pointers, out of these tests."""
    empty = tmp_path / "empty-gitconfig"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_PARAMETERS"):
        monkeypatch.delenv(name, raising=False)


def _git(repo, *args, input=None):
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, input=input
    )
    return result.stdout


def _init(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "T")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "commit.gpgsign", "false")
    _git(path, "config", "core.autocrlf", "false")
    (path / "a.txt").write_bytes(b"a\n")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-q", "-m", "init")
    return path


def _take(repo, minutes):
    made = cp.take_checkpoint(repo, "s", now=T0 + timedelta(minutes=minutes))
    assert made is not None
    return made


def _git_state(repo):
    return {
        "index": (repo / ".git" / "index").read_bytes(),
        "index_mtime": (repo / ".git" / "index").stat().st_mtime_ns,
        "head": (repo / ".git" / "HEAD").read_bytes(),
        "head_commit": _git(repo, "rev-parse", "HEAD"),
        "branches": _git(repo, "branch", "-a", "-v", "--no-abbrev"),
        "stash": _git(repo, "stash", "list"),
        "reflog": _git(repo, "reflog", "--all"),
        "staged": _git(repo, "diff", "--cached", "--name-status", "-z"),
    }


# ---------------------------------------------------------------------------- the brief's tests


def test_restore_recovers_overwritten_uncommitted_file(tmp_path):
    repo = _init(tmp_path / "repo")
    (repo / "a.txt").write_bytes(b"precious uncommitted work\n")
    before = _take(repo, 0)
    (repo / "a.txt").write_bytes(b"agent clobbered it\n")

    plan = cp.plan_restore(repo, before)
    assert plan.to_write == ("a.txt",)
    assert plan.left_in_place == ()

    result = cp.restore_checkpoint(repo, before, "s")

    assert result.restored == ("a.txt",)
    assert (repo / "a.txt").read_bytes() == b"precious uncommitted work\n"
    assert result.safety is not None and result.safety.tree != before.tree


def test_restore_is_itself_undoable(tmp_path):
    repo = _init(tmp_path / "repo")
    (repo / "a.txt").write_bytes(b"version one\n")
    first = _take(repo, 0)
    (repo / "a.txt").write_bytes(b"version two\n")
    (repo / "gone.txt").write_bytes(b"later file\n")

    undo = cp.restore_checkpoint(repo, first, "s")
    assert (repo / "a.txt").read_bytes() == b"version one\n"
    assert undo.safety is not None

    redo = cp.restore_checkpoint(repo, undo.safety, "s")

    assert redo.restored == ("a.txt",)
    assert (repo / "a.txt").read_bytes() == b"version two\n"
    assert (repo / "gone.txt").read_bytes() == b"later file\n"


def test_files_created_after_checkpoint_are_left_in_place(tmp_path):
    repo = _init(tmp_path / "repo")
    before = _take(repo, 0)
    (repo / "new").mkdir()
    (repo / "new" / "work.py").write_bytes(b"print('new')\n")
    (repo / "a.txt").write_bytes(b"changed\n")

    plan = cp.plan_restore(repo, before)
    assert plan.to_write == ("a.txt",)
    assert plan.left_in_place == ("new/work.py",)

    result = cp.restore_checkpoint(repo, before, "s")

    assert result.restored == ("a.txt",)
    assert (repo / "new" / "work.py").read_bytes() == b"print('new')\n"
    assert (repo / "a.txt").read_bytes() == b"a\n"


def test_restore_touches_only_differing_files(tmp_path):
    repo = _init(tmp_path / "repo")
    (repo / "same.txt").write_bytes(b"unchanged\n")
    (repo / "diff.txt").write_bytes(b"old\n")
    before = _take(repo, 0)
    (repo / "diff.txt").write_bytes(b"new\n")
    old = 1_600_000_000_000_000_000
    for name in ("same.txt", "a.txt"):
        os.utime(repo / name, ns=(old, old))

    result = cp.restore_checkpoint(repo, before, "s")

    assert result.restored == ("diff.txt",)
    assert (repo / "same.txt").stat().st_mtime_ns == old
    assert (repo / "a.txt").stat().st_mtime_ns == old
    assert (repo / "diff.txt").read_bytes() == b"old\n"


def test_restore_does_not_move_branch_or_index(tmp_path):
    repo = _init(tmp_path / "repo")
    (repo / "a.txt").write_bytes(b"stash me\n")
    _git(repo, "stash", "push", "-q", "-m", "keep me")
    (repo / "staged.txt").write_bytes(b"staged\n")
    _git(repo, "add", "staged.txt")
    (repo / "a.txt").write_bytes(b"checkpointed\n")
    before = _take(repo, 0)
    (repo / "a.txt").write_bytes(b"clobbered\n")
    (repo / "staged.txt").write_bytes(b"clobbered too\n")
    state = _git_state(repo)

    result = cp.restore_checkpoint(repo, before, "s")

    assert set(result.restored) == {"a.txt", "staged.txt"}
    assert _git_state(repo) == state
    assert (repo / "a.txt").read_bytes() == b"checkpointed\n"
    assert (repo / "staged.txt").read_bytes() == b"staged\n"


def test_restore_preserves_crlf_bytes(tmp_path):
    repo = _init(tmp_path / "repo")
    payload = b"line one\r\nline two\r\n\x00\xff binary tail\r\n"
    (repo / "win.txt").write_bytes(payload)
    before = _take(repo, 0)
    (repo / "win.txt").write_bytes(b"line one\nline two\n")

    cp.restore_checkpoint(repo, before, "s")

    assert (repo / "win.txt").read_bytes() == payload


# ---------------------------------------------------------------------------- extra safety tests


def test_restore_recreates_deleted_file_and_parent_folders(tmp_path):
    repo = _init(tmp_path / "repo")
    (repo / "deep" / "er").mkdir(parents=True)
    (repo / "deep" / "er" / "f.bin").write_bytes(b"\x00\x01\x02")
    before = _take(repo, 0)
    (repo / "deep" / "er" / "f.bin").unlink()
    (repo / "deep" / "er").rmdir()
    (repo / "deep").rmdir()

    result = cp.restore_checkpoint(repo, before, "s")

    assert result.restored == ("deep/er/f.bin",)
    assert (repo / "deep" / "er" / "f.bin").read_bytes() == b"\x00\x01\x02"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_restore_keeps_executable_bit(tmp_path):
    repo = _init(tmp_path / "repo")
    script = repo / "run.sh"
    script.write_bytes(b"#!/bin/sh\necho hi\n")
    script.chmod(0o755)
    before = _take(repo, 0)
    script.unlink()

    cp.restore_checkpoint(repo, before, "s")

    assert script.read_bytes() == b"#!/bin/sh\necho hi\n"
    assert os.stat(script).st_mode & 0o100


def test_restore_with_unchanged_tree_reuses_latest_as_safety(tmp_path):
    repo = _init(tmp_path / "repo")
    (repo / "a.txt").write_bytes(b"one\n")
    first = _take(repo, 0)
    (repo / "a.txt").write_bytes(b"two\n")
    latest = _take(repo, 1)

    result = cp.restore_checkpoint(repo, first, "s")

    assert result.safety == latest
    assert cp.list_checkpoints(repo)[0] == latest
    assert (repo / "a.txt").read_bytes() == b"one\n"


def test_restore_never_writes_paths_the_checkpoint_skipped(tmp_path, monkeypatch):
    repo = _init(tmp_path / "repo")
    monkeypatch.setattr(cp, "MAX_SKIPPED_STORED", 1)
    (repo / ".env").write_bytes(b"SECRET=old\n")
    (repo / "credentials.json").write_bytes(b"{}\n")
    (repo / ".aletheore").mkdir()
    (repo / ".aletheore" / "state.json").write_bytes(b"old\n")
    before = _take(repo, 0)
    assert before.skipped_truncated is True
    (repo / ".env").write_bytes(b"SECRET=new\n")
    (repo / "credentials.json").write_bytes(b'{"new": 1}\n')
    (repo / ".aletheore" / "state.json").write_bytes(b"new\n")
    (repo / "a.txt").write_bytes(b"changed\n")

    result = cp.restore_checkpoint(repo, before, "s")

    assert result.restored == ("a.txt",)
    assert (repo / ".env").read_bytes() == b"SECRET=new\n"
    assert (repo / "credentials.json").read_bytes() == b'{"new": 1}\n'
    assert (repo / ".aletheore" / "state.json").read_bytes() == b"new\n"


def test_restore_refuses_to_overwrite_content_the_safety_checkpoint_cannot_hold(
    tmp_path, monkeypatch
):
    repo = _init(tmp_path / "repo")
    (repo / ".gitignore").write_bytes(b"")
    (repo / "big.bin").write_bytes(b"small\n")
    (repo / "later-ignored.txt").write_bytes(b"old\n")
    before = _take(repo, 0)
    monkeypatch.setattr(cp, "MAX_FILE_BYTES", 25)
    (repo / "big.bin").write_bytes(b"now much larger than the patched cap\n")
    (repo / ".gitignore").write_bytes(b"later-ignored.txt\n")
    (repo / "later-ignored.txt").write_bytes(b"new and ignored\n")

    plan = cp.plan_restore(repo, before)
    assert "big.bin" in plan.blocked and "big.bin" not in plan.to_write
    assert "later-ignored.txt" in plan.blocked

    result = cp.restore_checkpoint(repo, before, "s")

    assert "big.bin" not in result.restored
    assert (repo / "big.bin").read_bytes() == b"now much larger than the patched cap\n"
    assert (repo / "later-ignored.txt").read_bytes() == b"new and ignored\n"
    assert (repo / ".gitignore").read_bytes() == b""


def test_restore_refuses_when_a_folder_became_a_file(tmp_path):
    repo = _init(tmp_path / "repo")
    (repo / "d").mkdir()
    (repo / "d" / "f.txt").write_bytes(b"f\n")
    before = _take(repo, 0)
    (repo / "d" / "f.txt").unlink()
    (repo / "d").rmdir()
    (repo / "d").write_bytes(b"now a file\n")

    plan = cp.plan_restore(repo, before)
    assert plan.blocked == ("d/f.txt",)
    assert plan.left_in_place == ("d",)

    result = cp.restore_checkpoint(repo, before, "s")

    assert result.restored == ()
    assert (repo / "d").read_bytes() == b"now a file\n"


@pytest.mark.skipif(sys.platform == "win32", reason="needs symlinks")
def test_restore_never_writes_through_a_symlinked_folder(tmp_path):
    repo = _init(tmp_path / "repo")
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "d").mkdir()
    (repo / "d" / "f.txt").write_bytes(b"inside\n")
    before = _take(repo, 0)
    (repo / "d" / "f.txt").unlink()
    (repo / "d").rmdir()
    os.symlink(outside, repo / "d", target_is_directory=True)

    plan = cp.plan_restore(repo, before)
    assert "d/f.txt" in plan.blocked

    result = cp.restore_checkpoint(repo, before, "s")

    assert "d/f.txt" not in result.restored
    assert list(outside.iterdir()) == []


def test_restore_never_writes_into_a_nested_repository(tmp_path):
    repo = _init(tmp_path / "repo")
    (repo / "vendor").mkdir()
    (repo / "vendor" / "lib.py").write_bytes(b"old\n")
    before = _take(repo, 0)
    (repo / "vendor" / "lib.py").unlink()
    _init(repo / "vendor")

    plan = cp.plan_restore(repo, before)
    assert "vendor/lib.py" in plan.blocked

    cp.restore_checkpoint(repo, before, "s")

    assert not (repo / "vendor" / "lib.py").exists()


def test_restore_refuses_paths_that_escape_the_repository(tmp_path):
    repo = _init(tmp_path / "repo")
    before = _take(repo, 0)
    blob = (
        _git(repo, "hash-object", "-w", "--stdin", input=b"escaped\n").decode().strip()
    )
    inner = (
        _git(repo, "mktree", input=f"100644 blob {blob}\tescape.txt\n".encode())
        .decode()
        .strip()
    )
    a_blob = _git(repo, "rev-parse", "HEAD:a.txt").decode().strip()
    evil = (
        _git(
            repo,
            "mktree",
            input=(
                f"040000 tree {inner}\t..\n"
                f"040000 tree {inner}\t.aletheore\n"
                f"100644 blob {blob}\t.env\n"
                f"040000 tree {inner}\t.git\n"
                f"100644 blob {a_blob}\ta.txt\n"
            ).encode(),
        )
        .decode()
        .strip()
    )
    forged = dataclasses.replace(before, tree=evil)
    git_dir_before = sorted(p.name for p in (repo / ".git").iterdir())

    plan = cp.plan_restore(repo, forged)
    assert set(plan.blocked) == {
        "../escape.txt",
        ".aletheore/escape.txt",
        ".env",
        ".git/escape.txt",
    }
    assert plan.to_write == ()

    result = cp.restore_checkpoint(repo, forged, "s")

    assert result.restored == ()
    assert not (tmp_path / "escape.txt").exists()
    assert not (repo / ".env").exists() and not (repo / ".aletheore").exists()
    assert sorted(p.name for p in (repo / ".git").iterdir()) == git_dir_before


def test_safe_relative_path_rules():
    assert cp._safe_relative("a/b.txt")
    for bad in (
        "",
        "/etc/passwd",
        "../x",
        "a/../b",
        "./a",
        "a//b",
        "a/",
        ".git/config",
        "x/.GIT/y",
    ):
        assert not cp._safe_relative(bad), bad


def test_restore_raises_without_a_safety_checkpoint(tmp_path, monkeypatch):
    repo = _init(tmp_path / "repo")
    before = _take(repo, 0)
    (repo / "a.txt").write_bytes(b"changed\n")
    monkeypatch.setattr(cp, "take_checkpoint", lambda *a, **k: None)

    with pytest.raises(cp.CheckpointError):
        cp.restore_checkpoint(repo, before, "s")

    assert (repo / "a.txt").read_bytes() == b"changed\n"


def test_restore_from_unknown_tree_raises(tmp_path):
    repo = _init(tmp_path / "repo")
    before = _take(repo, 0)
    bogus = dataclasses.replace(before, tree="0" * 40)
    with pytest.raises(cp.CheckpointError):
        cp.plan_restore(repo, bogus)


def test_restore_applies_checkout_line_endings_when_autocrlf_is_on(tmp_path):
    repo = _init(tmp_path / "repo")
    _git(repo, "config", "core.autocrlf", "true")
    (repo / "w.txt").write_bytes(b"one\r\ntwo\r\n")
    before = _take(repo, 0)
    (repo / "w.txt").write_bytes(b"clobbered\r\n")

    cp.restore_checkpoint(repo, before, "s")

    assert (repo / "w.txt").read_bytes() == b"one\r\ntwo\r\n"


@pytest.mark.skipif(sys.platform == "win32", reason="needs symlinks")
def test_restore_puts_a_symbolic_link_back_without_following_it(tmp_path):
    repo = _init(tmp_path / "repo")
    os.symlink("a.txt", repo / "link")
    before = _take(repo, 0)
    (repo / "link").unlink()
    (repo / "link").write_bytes(b"a regular file now\n")

    result = cp.restore_checkpoint(repo, before, "s")

    assert result.restored == ("link",)
    assert os.readlink(repo / "link") == "a.txt"
    assert (repo / "a.txt").read_bytes() == b"a\n"
