import hashlib
import subprocess
import sys

import pytest

from aletheore.council import bundle_transport as bt
from aletheore.council.bundle_transport import (
    TransportError,
    create_bundle,
    ensure_integration_branch,
    has_commit,
    import_bundle,
    validate_branch_name,
    validate_participant,
)

BRANCH = "council/room1/codex"


def git(repo, *args, check=True):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, capture_output=True, text=True)
    if check and r.returncode:
        raise AssertionError(r.stderr)
    return r.stdout.strip()


@pytest.fixture
def pair(tmp_path):
    a = tmp_path / "machine a"
    a.mkdir()
    git(a, "init", "-q", "-b", "main")
    (a / "f.txt").write_text("base\n")
    git(a, "add", "-A")
    git(a, "commit", "-q", "-m", "base")
    b = tmp_path / "machine b"
    git(tmp_path, "clone", "-q", str(a), str(b))
    return a, b, git(a, "rev-parse", "HEAD")


def worker_commit(repo, base, branch=BRANCH, name="n.py", text="1\n"):
    git(repo, "checkout", "-q", "-b", branch, base)
    (repo / name).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "work")
    git(repo, "checkout", "-q", "main")


def test_create_then_import_across_two_clones(pair, tmp_path):
    a, b, base = pair
    worker_commit(a, base)
    info = create_bundle(a, BRANCH, base, tmp_path / "out.bundle")
    assert info.sha256 == hashlib.sha256((tmp_path / "out.bundle").read_bytes()).hexdigest()
    assert info.base_sha == base and info.tip_sha == git(a, "rev-parse", BRANCH) and info.size > 0
    heads_before = git(b, "branch", "--list")
    ref = import_bundle(b, "alice", BRANCH, tmp_path / "out.bundle", info.sha256)
    assert ref == f"refs/council/peers/alice/{BRANCH}"
    assert git(b, "rev-parse", ref) == info.tip_sha
    assert git(b, "branch", "--list") == heads_before  # refs/heads gained nothing


def test_import_rejects_wrong_sha256(pair, tmp_path):
    a, b, base = pair
    worker_commit(a, base)
    create_bundle(a, BRANCH, base, tmp_path / "o.bundle")
    with pytest.raises(TransportError):
        import_bundle(b, "alice", BRANCH, tmp_path / "o.bundle", "0" * 64)
    assert git(b, "for-each-ref", "refs/council/") == ""


def test_import_rejects_bundle_with_extra_refs(pair, tmp_path):
    a, b, base = pair
    worker_commit(a, base)
    worker_commit(a, base, branch="council/room1/other", name="m.py")
    out = tmp_path / "multi.bundle"
    git(a, "bundle", "create", str(out), f"refs/heads/{BRANCH}", "refs/heads/council/room1/other", f"^{base}")
    with pytest.raises(TransportError):
        import_bundle(b, "alice", BRANCH, out, hashlib.sha256(out.read_bytes()).hexdigest())
    assert git(b, "for-each-ref", "refs/council/") == ""


def test_import_rejects_bundle_whose_ref_is_not_the_expected_branch(pair, tmp_path):
    a, b, base = pair
    (a / "g.txt").write_text("more\n")
    git(a, "add", "-A")
    git(a, "commit", "-q", "-m", "more on main")
    out = tmp_path / "main.bundle"
    git(a, "bundle", "create", str(out), "refs/heads/main", f"^{base}")
    with pytest.raises(TransportError):
        import_bundle(b, "alice", BRANCH, out, hashlib.sha256(out.read_bytes()).hexdigest())
    assert git(b, "for-each-ref", "refs/council/") == ""


def test_import_rejects_bundle_missing_prerequisite(pair, tmp_path):
    a, b, base = pair
    worker_commit(a, base)
    info = create_bundle(a, BRANCH, base, tmp_path / "o.bundle")
    stranger = tmp_path / "stranger"
    stranger.mkdir()
    git(stranger, "init", "-q", "-b", "main")
    (stranger / "x").write_text("x")
    git(stranger, "add", "-A")
    git(stranger, "commit", "-q", "-m", "unrelated")
    with pytest.raises(TransportError):
        import_bundle(stranger, "alice", BRANCH, tmp_path / "o.bundle", info.sha256)


def test_create_rejects_oversized_bundle(pair, tmp_path, monkeypatch):
    a, _, base = pair
    worker_commit(a, base)
    monkeypatch.setattr(bt, "MAX_BUNDLE_BYTES", 10)
    with pytest.raises(TransportError):
        create_bundle(a, BRANCH, base, tmp_path / "o.bundle")


def test_create_rejects_empty_branch(pair, tmp_path):
    a, _, base = pair
    git(a, "branch", BRANCH, base)  # no new commits
    with pytest.raises(TransportError):
        create_bundle(a, BRANCH, base, tmp_path / "o.bundle")


@pytest.mark.parametrize("bad", ["../x", "council/r/-f", "council/r/a b", "council/r/a\nb", "main", "council/../x/y", "council/r/..", ""])
def test_branch_name_validation_rejects(bad):
    with pytest.raises(TransportError):
        validate_branch_name(bad)


def test_branch_name_and_participant_validation_accept():
    assert validate_branch_name("council/room-1/codex") == "council/room-1/codex"
    assert validate_participant("alice_01") == "alice_01"
    for bad in ("", "a b", "../x", "x" * 65, "a/b"):
        with pytest.raises(TransportError):
            validate_participant(bad)


@pytest.mark.skipif(sys.platform == "win32", reason="posix hook script")
def test_import_runs_no_hooks(pair, tmp_path):
    a, b, base = pair
    worker_commit(a, base)
    info = create_bundle(a, BRANCH, base, tmp_path / "o.bundle")
    hooks = b / ".githooks"
    hooks.mkdir()
    hook = hooks / "reference-transaction"
    hook.write_text('#!/bin/sh\ntouch "$PWD/hook-ran.txt"\n')
    hook.chmod(0o755)
    git(b, "config", "core.hooksPath", ".githooks")
    # sanity: this hook DOES fire for a plain git fetch, so the assertion below is meaningful
    git(b, "fetch", "-q", str(tmp_path / "o.bundle"), f"refs/heads/{BRANCH}:refs/scratch/x")
    assert (b / "hook-ran.txt").exists()
    (b / "hook-ran.txt").unlink()
    import_bundle(b, "alice", BRANCH, tmp_path / "o.bundle", info.sha256)
    assert not (b / "hook-ran.txt").exists()


def test_ensure_integration_branch_requires_base_commit(pair, tmp_path):
    a, b, base = pair
    ensure_integration_branch(b, "room1", base)
    assert git(b, "rev-parse", "council/room1/main") == base
    ensure_integration_branch(b, "room1", base)  # idempotent
    assert has_commit(b, base) and not has_commit(b, "f" * 40)
    with pytest.raises(TransportError):
        ensure_integration_branch(b, "room2", "f" * 40)
    assert git(b, "branch", "--list", "council/room2/main") == ""


@pytest.mark.parametrize("kind", ["symlink", "gitattributes", "lfsconfig", "gitmodules", "nested_gitattributes"])
def test_import_refuses_risky_files_and_leaves_no_ref(pair, tmp_path, kind):
    a, b, base = pair
    git(a, "checkout", "-q", "-b", BRANCH, base)
    if kind == "symlink":
        try:
            (a / "link").symlink_to("/etc/passwd")
        except OSError:
            pytest.skip("this machine cannot create symlinks (Windows needs Developer Mode or admin rights)")
    elif kind == "nested_gitattributes":
        (a / "sub").mkdir()
        (a / "sub" / ".gitattributes").write_text("* filter=evil\n")
    else:
        (a / f".{kind}").write_text("x\n")
    git(a, "add", "-A")
    git(a, "commit", "-q", "-m", "risky")
    git(a, "checkout", "-q", "main")
    info = create_bundle(a, BRANCH, base, tmp_path / "r.bundle")
    with pytest.raises(TransportError, match="not allowed"):
        import_bundle(b, "alice", BRANCH, tmp_path / "r.bundle", info.sha256)
    assert git(b, "for-each-ref", "refs/council/") == ""
