import dataclasses
import os
import subprocess
import tempfile
import threading
import unicodedata
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


def _init(path, commit=True):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "T")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "commit.gpgsign", "false")
    _git(path, "config", "core.autocrlf", "false")
    if commit:
        (path / "a.txt").write_text("a\n")
        _git(path, "add", "a.txt")
        _git(path, "commit", "-q", "-m", "init")
    return path


def _tree_paths(repo, treeish):
    out = _git(repo, "ls-tree", "-r", "-z", "--name-only", treeish)
    return {
        unicodedata.normalize("NFC", p) for p in out.decode("utf-8").split("\0") if p
    }


def _blob(repo, treeish, path):
    return _git(repo, "cat-file", "blob", f"{treeish}:{path}")


def _checkpoint_refs(repo):
    out = _git(
        repo, "for-each-ref", "--format=%(refname)", "refs/aletheore/checkpoints/"
    )
    return [line for line in out.decode("utf-8").splitlines() if line]


def _git_state(repo):
    index = (repo / ".git" / "index").read_bytes()
    return {
        "status": _git(
            repo,
            "--no-optional-locks",
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ),
        "stash": _git(repo, "stash", "list"),
        "index": index,
        "index_mtime": (repo / ".git" / "index").stat().st_mtime_ns,
        "branches": _git(repo, "branch", "-a", "-v", "--no-abbrev"),
        "reflog": _git(repo, "reflog", "--all"),
        "head": (repo / ".git" / "HEAD").read_bytes(),
        "logs": sorted(
            str(p.relative_to(repo / ".git" / "logs"))
            for p in (repo / ".git" / "logs").rglob("*")
        ),
    }


def test_constants_match_the_spec():
    assert cp.MAX_FILE_BYTES == 5 * 1024 * 1024
    assert cp.MAX_TOTAL_BYTES == 100 * 1024 * 1024


def test_checkpoint_leaves_git_state_untouched(tmp_path):
    repo = _init(tmp_path / "repo")
    (repo / "a.txt").write_text("stashed change\n")
    _git(repo, "stash", "push", "-q", "-m", "keep me")
    (repo / "b.txt").write_text("staged\n")
    _git(repo, "add", "b.txt")
    (repo / "a.txt").write_text("unstaged change\n")
    (repo / "untracked.txt").write_text("u\n")
    working = {p.name: p.read_bytes() for p in repo.iterdir() if p.is_file()}
    before = _git_state(repo)

    result = cp.take_checkpoint(repo, "sess-1", now=T0)

    assert result is not None
    assert _git_state(repo) == before
    assert {p.name: p.read_bytes() for p in repo.iterdir() if p.is_file()} == working
    assert not (repo / ".git" / "logs" / "refs" / "aletheore").exists()
    assert result.ref.startswith("refs/aletheore/checkpoints/")
    assert _checkpoint_refs(repo) == [result.ref]
    assert _blob(repo, result.commit, "a.txt") == b"unstaged change\n"
    assert _blob(repo, result.commit, "b.txt") == b"staged\n"
    head = _git(repo, "rev-parse", "HEAD").decode().strip()
    parents = (
        _git(repo, "rev-list", "--parents", "-n", "1", result.commit)
        .decode()
        .split()[1:]
    )
    assert parents == [head]
    assert result.session == "sess-1"
    assert result.created_at == T0
    assert result.files == 3


def test_checkpoint_includes_untracked_and_skips_ignored_denied_and_large(tmp_path):
    repo = _init(tmp_path / "repo")
    (repo / ".gitignore").write_text("ignored.log\n")
    (repo / "ignored.log").write_text("noise\n")
    (repo / "new.py").write_text("print('hi')\n")
    (repo / ".env.local").write_text("SECRET=1\n")
    (repo / "big.bin").write_bytes(b"\0" * (6 * 1024 * 1024))
    (repo / ".aletheore" / "shield").mkdir(parents=True)
    (repo / ".aletheore" / "shield" / "state.json").write_text("{}\n")

    result = cp.take_checkpoint(repo, "s", now=T0)

    assert result is not None
    paths = _tree_paths(repo, result.tree)
    assert paths == {"a.txt", ".gitignore", "new.py"}
    assert "big.bin" in result.skipped
    assert ".env.local" in result.skipped
    assert "ignored.log" not in result.skipped
    assert result.files == 3
    listed = cp.list_checkpoints(repo)
    assert listed == [result]


def test_total_cap_stops_at_100mb(tmp_path, monkeypatch):
    repo = _init(tmp_path / "repo", commit=False)
    for name in ("f1.txt", "f2.txt", "f3.txt", "f4.txt"):
        (repo / name).write_bytes(b"x" * 100)
    monkeypatch.setattr(cp, "MAX_TOTAL_BYTES", 250)

    result = cp.take_checkpoint(repo, "s", now=T0)

    assert result is not None
    assert _tree_paths(repo, result.tree) == {"f1.txt", "f2.txt"}
    assert result.skipped == ("f3.txt", "f4.txt")
    assert result.files == 2


def test_identical_tree_returns_none(tmp_path):
    repo = _init(tmp_path / "repo")
    first = cp.take_checkpoint(repo, "s", now=T0)
    assert first is not None

    assert cp.take_checkpoint(repo, "s", now=T0 + timedelta(minutes=5)) is None
    assert (
        cp.take_checkpoint(repo, "s", force=True, now=T0 + timedelta(minutes=5)) is None
    )

    (repo / "a.txt").write_text("changed\n")
    second = cp.take_checkpoint(repo, "s", now=T0 + timedelta(minutes=6))
    assert second is not None and second.tree != first.tree
    assert cp.list_checkpoints(repo) == [second, first]


def test_identical_tree_still_checkpoints_a_different_session(tmp_path):
    repo = _init(tmp_path / "repo")
    first = cp.take_checkpoint(repo, "s1", now=T0)
    # Same tree and inside the rate limit, but another session: it still gets its own starting point.
    second = cp.take_checkpoint(repo, "s2", now=T0 + timedelta(seconds=1))
    assert first is not None and second is not None
    assert second.tree == first.tree and second.session == "s2"
    # The same session again (even forced) with an unchanged tree saves nothing.
    assert cp.take_checkpoint(repo, "s2", force=True, now=T0 + timedelta(minutes=5)) is None


def test_checked_repo_state_skips_the_probe(tmp_path, monkeypatch):
    repo = _init(tmp_path / "repo")
    checked = cp.repo_state(repo)
    monkeypatch.setattr(cp, "repo_state", lambda r: (_ for _ in ()).throw(AssertionError("probed again")))
    assert cp.take_checkpoint(repo, "s", checked=checked) is not None
    unsupported = dataclasses.replace(checked, ok=False, reason="x")
    (repo / "a.txt").write_text("changed\n")
    assert cp.take_checkpoint(repo, "s", force=True, checked=unsupported) is None


def test_rate_limit_returns_none_unless_forced(tmp_path):
    repo = _init(tmp_path / "repo")
    assert cp.take_checkpoint(repo, "s", now=T0) is not None
    (repo / "a.txt").write_text("edit 1\n")

    assert cp.take_checkpoint(repo, "s", now=T0 + timedelta(seconds=2)) is None
    forced = cp.take_checkpoint(repo, "s", force=True, now=T0 + timedelta(seconds=2))
    assert forced is not None

    (repo / "a.txt").write_text("edit 2\n")
    assert cp.take_checkpoint(repo, "s", now=T0 + timedelta(seconds=4)) is None
    assert cp.take_checkpoint(repo, "s", now=T0 + timedelta(seconds=8)) is not None
    (repo / "a.txt").write_text("edit 3\n")
    assert (
        cp.take_checkpoint(repo, "s", now=T0 + timedelta(seconds=30), min_interval_s=60)
        is None
    )
    assert len(cp.list_checkpoints(repo)) == 3


def test_deleted_tracked_file_is_captured_as_absent(tmp_path):
    repo = _init(tmp_path / "repo")
    (repo / "keep.txt").write_text("k\n")
    _git(repo, "add", "keep.txt")
    _git(repo, "commit", "-q", "-m", "keep")
    (repo / "a.txt").unlink()

    result = cp.take_checkpoint(repo, "s", now=T0)

    assert result is not None
    assert _tree_paths(repo, result.tree) == {"keep.txt"}
    assert "a.txt" not in result.skipped
    assert "a.txt" in _git(repo, "ls-files").decode()
    assert cp.changed_since(repo, result) == []
    (repo / "a.txt").write_text("back\n")
    assert cp.changed_since(repo, result) == ["a.txt"]


def test_repo_with_no_commits_gets_parentless_checkpoint(tmp_path):
    repo = _init(tmp_path / "repo", commit=False)
    (repo / "first.txt").write_text("1\n")

    result = cp.take_checkpoint(repo, "s", now=T0)

    assert result is not None
    assert _tree_paths(repo, result.tree) == {"first.txt"}
    raw = _git(repo, "cat-file", "commit", result.commit).decode()
    assert "\nparent " not in raw and not raw.startswith("parent ")
    assert (
        subprocess.run(
            ["git", "rev-parse", "--verify", "-q", "HEAD"],
            cwd=repo,
            capture_output=True,
        ).returncode
        != 0
    )


def test_odd_filenames_round_trip(tmp_path):
    repo = _init(tmp_path / "repo")
    names = ["with space.txt", "café/日本 語.txt", "-dash.txt"]
    for name in names:
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{name}\n", encoding="utf-8")
    newline_name = "new\nline.txt"
    # Some names are only legal on some filesystems (Windows forbids quotes and newlines).
    for optional in ("quote'and\"double.txt", newline_name):
        try:
            (repo / optional).write_text("opt\n", encoding="utf-8")
        except OSError:
            continue
        names.append(optional)

    result = cp.take_checkpoint(repo, "s", now=T0)

    assert result is not None
    expected = {unicodedata.normalize("NFC", n) for n in names} | {"a.txt"}
    assert _tree_paths(repo, result.tree) == expected
    assert cp.changed_since(repo, result) == []
    (repo / names[1]).write_text("changed\n", encoding="utf-8")
    changed = cp.changed_since(repo, result)
    assert [unicodedata.normalize("NFC", p) for p in changed] == [
        unicodedata.normalize("NFC", names[1])
    ]
    if newline_name in names:
        (repo / newline_name).write_text("changed\n", encoding="utf-8")
        assert newline_name in cp.changed_since(repo, result)


def test_concurrent_checkpoints_use_unique_refs(tmp_path, monkeypatch):
    repo = _init(tmp_path / "repo")
    (repo / "a.txt").write_text("dirty\n")
    # Simulate the race: both threads read "no previous checkpoint" before either writes one,
    # and both use the same timestamp, so only the commit hash can keep the refs apart.
    monkeypatch.setattr(cp, "_latest_checkpoint", lambda *args, **kwargs: None)
    barrier = threading.Barrier(2)
    results = {}
    errors = []

    def run(session):
        try:
            barrier.wait()
            results[session] = cp.take_checkpoint(repo, session, now=T0)
        except Exception as exc:  # pragma: no cover - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(s,)) for s in ("sess-a", "sess-b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert results["sess-a"] is not None and results["sess-b"] is not None
    assert results["sess-a"].ref != results["sess-b"].ref
    assert sorted(_checkpoint_refs(repo)) == sorted(
        [results["sess-a"].ref, results["sess-b"].ref]
    )
    for session, result in results.items():
        assert _git(repo, "rev-parse", result.ref).decode().strip() == result.commit
        assert result.session == session


def test_same_session_same_instant_still_gets_unique_refs(tmp_path, monkeypatch):
    repo = _init(tmp_path / "repo")
    (repo / "a.txt").write_text("dirty\n")
    monkeypatch.setattr(cp, "_latest_checkpoint", lambda *args, **kwargs: None)
    first = cp.take_checkpoint(repo, "same", now=T0)
    second = cp.take_checkpoint(repo, "same", now=T0)
    assert first is not None and second is not None
    assert first.ref != second.ref and first.commit != second.commit
    assert len(_checkpoint_refs(repo)) == 2


def test_prune_keeps_newest_and_recent(tmp_path):
    repo = _init(tmp_path / "repo")
    made = []
    for age_days in (40, 30, 20, 5, 1):
        (repo / "a.txt").write_text(f"age {age_days}\n")
        result = cp.take_checkpoint(repo, "s", now=T0 - timedelta(days=age_days))
        assert result is not None
        made.append(result)

    assert cp.prune_checkpoints(repo, keep=3, keep_days=7, now=T0) == 2
    assert cp.list_checkpoints(repo) == list(reversed(made[2:]))

    assert cp.prune_checkpoints(repo, keep=1, keep_days=7, now=T0) == 1
    assert cp.list_checkpoints(repo) == [made[4], made[3]]

    assert cp.prune_checkpoints(repo, now=T0) == 0
    assert cp.list_checkpoints(repo, limit=1) == [made[4]]


def test_unsupported_state_returns_none(tmp_path):
    repo = _init(tmp_path / "repo")
    _git(repo, "checkout", "-q", "--detach")
    (repo / "a.txt").write_text("changed\n")

    assert cp.take_checkpoint(repo, "s", now=T0) is None
    assert cp.take_checkpoint(repo, "s", force=True, now=T0) is None
    assert _checkpoint_refs(repo) == []

    plain = tmp_path / "plain"
    plain.mkdir()
    assert cp.take_checkpoint(plain, "s", now=T0) is None
    assert cp.list_checkpoints(plain) == []


def test_checkpoint_from_subdirectory_uses_repo_root_paths(tmp_path):
    repo = _init(tmp_path / "repo")
    (repo / "pkg").mkdir()
    (repo / "pkg" / "mod.py").write_text("x = 1\n")

    result = cp.take_checkpoint(repo / "pkg", "s", now=T0)

    assert result is not None
    assert _tree_paths(repo, result.tree) == {"a.txt", "pkg/mod.py"}
    (repo / "a.txt").write_text("changed\n")
    assert cp.changed_since(repo / "pkg", result) == ["a.txt"]


def test_linked_worktrees_keep_separate_checkpoint_lists(tmp_path):
    main = _init(tmp_path / "main")
    linked = tmp_path / "linked"
    _git(main, "worktree", "add", "-q", "-b", "side", str(linked))
    (main / "a.txt").write_text("main edit\n")
    (linked / "a.txt").write_text("linked edit\n")

    in_main = cp.take_checkpoint(main, "m", now=T0)
    in_linked = cp.take_checkpoint(linked, "l", now=T0 + timedelta(seconds=1))

    assert in_main is not None and in_linked is not None
    assert cp.list_checkpoints(main) == [in_main]
    assert cp.list_checkpoints(linked) == [in_linked]


def test_foreign_refs_in_namespace_are_ignored(tmp_path):
    repo = _init(tmp_path / "repo")
    head = _git(repo, "rev-parse", "HEAD").decode().strip()
    _git(repo, "update-ref", "refs/aletheore/checkpoints/zzz-not-ours", head)

    result = cp.take_checkpoint(repo, "s", now=T0)

    assert result is not None
    assert cp.list_checkpoints(repo) == [result]
    assert (
        cp.prune_checkpoints(repo, keep=0, keep_days=0, now=T0 + timedelta(days=30))
        == 1
    )
    assert _checkpoint_refs(repo) == ["refs/aletheore/checkpoints/zzz-not-ours"]


def test_nested_repositories_keep_gitlinks_and_skip_untracked_repos(tmp_path):
    repo = _init(tmp_path / "repo")
    inner = _init(repo / "inner")
    _git(repo, "add", "inner")
    _git(repo, "commit", "-q", "-m", "add gitlink")
    _init(repo / "loose")

    result = cp.take_checkpoint(repo, "s", now=T0)

    assert result is not None
    entries = _git(repo, "ls-tree", "-z", result.tree).decode().split("\0")
    inner_head = _git(inner, "rev-parse", "HEAD").decode().strip()
    assert f"160000 commit {inner_head}\tinner" in entries
    assert "loose/" in result.skipped
    assert cp.changed_since(repo, result) == []


def test_user_config_cannot_leak_into_checkpoint_writes(tmp_path):
    repo = _init(tmp_path / "repo")
    _git(repo, "config", "core.logAllRefUpdates", "always")
    _git(repo, "config", "core.splitIndex", "true")
    _git(repo, "config", "core.autocrlf", "true")
    _git(repo, "config", "core.safecrlf", "true")
    _git(repo, "config", "commit.gpgsign", "true")
    _git(repo, "config", "gpg.program", "false")
    marker = tmp_path / "hook-ran"
    hook = repo / ".git" / "hooks" / "reference-transaction"
    hook.write_text(f"#!/bin/sh\necho ran >> '{marker.as_posix()}'\n")
    hook.chmod(0o755)
    (repo / "mixed.txt").write_bytes(b"one\r\ntwo\n")
    before = sorted(p.name for p in (repo / ".git").iterdir())

    result = cp.take_checkpoint(repo, "s", now=T0)

    assert result is not None
    assert "mixed.txt" in _tree_paths(repo, result.tree)
    assert not (repo / ".git" / "logs" / "refs" / "aletheore").exists()
    assert not marker.exists()
    assert not [
        n for n in (repo / ".git").iterdir() if n.name.startswith("sharedindex")
    ]
    assert sorted(p.name for p in (repo / ".git").iterdir()) == before


def test_seeded_index_leaves_user_index_bytes_and_mtime_unchanged(
    tmp_path, monkeypatch
):
    repo = _init(tmp_path / "repo")
    for i in range(5):
        (repo / f"f{i}.txt").write_text(f"{i}\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "more")
    (repo / "f2.txt").write_text("edited on disk\n")
    real_index = repo / ".git" / "index"
    before = (real_index.read_bytes(), real_index.stat().st_mtime_ns)
    seeds = []
    real_copy2 = cp.shutil.copy2

    def spy(src, dst, **kwargs):
        seeds.append(os.path.realpath(src))
        return real_copy2(src, dst, **kwargs)

    monkeypatch.setattr(cp.shutil, "copy2", spy)

    result = cp.take_checkpoint(repo, "s", now=T0)

    assert result is not None
    assert seeds == [os.path.realpath(real_index)]
    assert (real_index.read_bytes(), real_index.stat().st_mtime_ns) == before
    assert _blob(repo, result.commit, "f2.txt") == b"edited on disk\n"
    assert _blob(repo, result.commit, "f3.txt") == b"3\n"
    assert cp.changed_since(repo, result) == []


def test_flagged_index_entries_are_captured_from_disk(tmp_path):
    repo = _init(tmp_path / "repo")
    (repo / "b.txt").write_text("b\n")
    _git(repo, "add", "b.txt")
    _git(repo, "commit", "-q", "-m", "b")
    _git(repo, "update-index", "--assume-unchanged", "a.txt")
    _git(repo, "update-index", "--skip-worktree", "b.txt")
    (repo / "a.txt").write_text("a on disk\n")
    (repo / "b.txt").write_text("b on disk\n")

    result = cp.take_checkpoint(repo, "s", now=T0)

    assert result is not None
    assert _blob(repo, result.commit, "a.txt") == b"a on disk\n"
    assert _blob(repo, result.commit, "b.txt") == b"b on disk\n"


def test_unmerged_index_entries_still_yield_a_checkpoint(tmp_path):
    repo = _init(tmp_path / "repo")
    base = _git(repo, "symbolic-ref", "--short", "HEAD").decode().strip()
    _git(repo, "checkout", "-q", "-b", "other")
    (repo / "a.txt").write_text("other side\n")
    _git(repo, "commit", "-q", "-am", "other")
    _git(repo, "checkout", "-q", base)
    (repo / "a.txt").write_text("base side\n")
    _git(repo, "commit", "-q", "-am", "base")
    picked = subprocess.run(
        ["git", "cherry-pick", "other"], cwd=repo, capture_output=True
    )
    assert picked.returncode != 0
    assert b"\t" in _git(repo, "ls-files", "-u")
    index_before = (repo / ".git" / "index").read_bytes()

    result = cp.take_checkpoint(repo, "s", now=T0)

    assert result is not None
    assert b"<<<<<<<" in _blob(repo, result.commit, "a.txt")
    assert (repo / ".git" / "index").read_bytes() == index_before


def test_seeded_failure_retries_with_a_fresh_index(tmp_path, monkeypatch):
    repo = _init(tmp_path / "repo")
    (repo / "new.txt").write_text("n\n")
    real_run_git = cp.run_git
    failed = []

    def flaky(top, *args, env=None, **kwargs):
        if (
            "write-tree" in args
            and env
            and env["GIT_INDEX_FILE"].endswith("seeded-index")
        ):
            failed.append(True)
            return subprocess.CompletedProcess(args, 128, b"", b"boom")
        return real_run_git(top, *args, env=env, **kwargs)

    monkeypatch.setattr(cp, "run_git", flaky)

    result = cp.take_checkpoint(repo, "s", now=T0)

    assert failed == [True]
    assert result is not None
    assert _tree_paths(repo, result.tree) == {"a.txt", "new.txt"}


def test_scratch_dir_is_removed_when_snapshot_raises(tmp_path, monkeypatch):
    repo = _init(tmp_path / "repo")
    (repo / "a.txt").write_text("dirty\n")
    scratch_root = tmp_path / "scratch-root"
    scratch_root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch_root))
    real_run_git = cp.run_git

    def exploding(top, *args, **kwargs):
        if "write-tree" in args:
            raise RuntimeError("boom")
        return real_run_git(top, *args, **kwargs)

    monkeypatch.setattr(cp, "run_git", exploding)

    with pytest.raises(RuntimeError):
        cp.take_checkpoint(repo, "s", now=T0)
    with pytest.raises(RuntimeError):
        cp.changed_since(repo, cp.Checkpoint("r", "c", "t", T0, "s", 0, ()))
    assert [
        p.name for p in scratch_root.iterdir() if p.name.startswith("aletheore-shield-")
    ] == []


def test_changed_since_raises_when_git_fails(tmp_path):
    repo = _init(tmp_path / "repo")
    result = cp.take_checkpoint(repo, "s", now=T0)
    assert result is not None
    bogus = dataclasses.replace(result, tree="0" * 40)
    with pytest.raises(cp.CheckpointError):
        cp.changed_since(repo, bogus)
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(cp.CheckpointError):
        cp.changed_since(plain, result)


def test_tracked_path_under_symlinked_folder_is_skipped(tmp_path):
    repo = _init(tmp_path / "repo")
    (repo / "d").mkdir()
    (repo / "d" / "f.txt").write_text("f\n")
    _git(repo, "add", "d/f.txt")
    _git(repo, "commit", "-q", "-m", "d")
    (repo / "d").rename(repo / "real")
    try:
        os.symlink("real", repo / "d", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks cannot be created here")

    result = cp.take_checkpoint(repo, "s", now=T0)

    assert result is not None
    assert "d/f.txt" in result.skipped
    paths = _tree_paths(repo, result.tree)
    assert "real/f.txt" in paths and "d/f.txt" not in paths


def test_skipped_list_is_bounded(tmp_path, monkeypatch):
    repo = _init(tmp_path / "repo")
    monkeypatch.setattr(cp, "MAX_SKIPPED_STORED", 3)
    for i in range(5):
        (repo / f"credentials-{i}").write_text("x\n")

    result = cp.take_checkpoint(repo, "s", now=T0)

    assert result is not None
    assert result.skipped == ("credentials-0", "credentials-1", "credentials-2")
    assert result.skipped_count == 5 and result.skipped_truncated is True
    assert cp.list_checkpoints(repo) == [result]
    raw = _git(repo, "cat-file", "commit", result.commit).decode()
    assert "credentials-4" not in raw


def test_small_skipped_list_is_not_truncated(tmp_path):
    repo = _init(tmp_path / "repo")
    (repo / ".env").write_text("X=1\n")
    result = cp.take_checkpoint(repo, "s", now=T0)
    assert result is not None
    assert result.skipped == (".env",)
    assert result.skipped_count == 1 and result.skipped_truncated is False


def test_latest_lookup_reads_one_body_and_refs_carry_worktree(tmp_path, monkeypatch):
    main = _init(tmp_path / "main")
    linked = tmp_path / "linked"
    _git(main, "worktree", "add", "-q", "-b", "side", str(linked))
    made = []
    for i in range(4):
        (main / "a.txt").write_text(f"{i}\n")
        made.append(cp.take_checkpoint(main, "s", now=T0 + timedelta(minutes=i)))
    (linked / "a.txt").write_text("linked\n")
    other = cp.take_checkpoint(linked, "l", now=T0 + timedelta(minutes=9))
    assert all(made) and other is not None
    assert all(c.ref.startswith(cp.REF_PREFIX + "main/") for c in made)
    assert other.ref.startswith(cp.REF_PREFIX + "wt-linked/")

    loaded = []
    real_load = cp._load

    def counting(top, refs):
        loaded.append(len(refs))
        return real_load(top, refs)

    monkeypatch.setattr(cp, "_load", counting)
    assert cp.list_checkpoints(main, limit=1) == [made[-1]]
    assert loaded == [1]
