import subprocess

from scan_worker.jobs import _ensure_persistent_checkout, _persistent_checkout_dir


def _push_new_commit(bare_repo: str, work_dir, content: str) -> str:
    subprocess.run(["git", "clone", "-q", bare_repo, str(work_dir)], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=work_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=work_dir, check=True)
    (work_dir / "app.py").write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=work_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "another change"], cwd=work_dir, check=True)
    subprocess.run(["git", "push", "-q"], cwd=work_dir, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work_dir, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_persistent_checkout_dir_is_scoped_by_installation_and_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("ALETHEORE_REPO_CHECKOUT_ROOT", str(tmp_path))

    a = _persistent_checkout_dir(1, "octocat/hello-world")
    b = _persistent_checkout_dir(2, "octocat/hello-world")
    c = _persistent_checkout_dir(1, "octocat/other-repo")

    assert a != b
    assert a != c
    assert str(tmp_path) in str(a)


def test_ensure_persistent_checkout_clones_fresh_when_no_checkout_exists(bare_repo_with_two_commits, tmp_path):
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    checkout_dir = tmp_path / "checkout"

    _ensure_persistent_checkout(bare_path, head_sha, checkout_dir)

    assert (checkout_dir / "app.py").exists()
    current_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=checkout_dir, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert current_sha == head_sha


def test_ensure_persistent_checkout_reuses_existing_checkout_on_second_call(bare_repo_with_two_commits, tmp_path):
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    checkout_dir = tmp_path / "checkout"

    _ensure_persistent_checkout(bare_path, head_sha, checkout_dir)

    # A new commit lands on the remote after the first checkout - proves
    # the second call actually fetches and updates, rather than silently
    # reusing stale content.
    new_sha = _push_new_commit(bare_path, tmp_path / "pusher", "print('updated')\n")

    _ensure_persistent_checkout(bare_path, new_sha, checkout_dir)

    assert (checkout_dir / "app.py").read_text() == "print('updated')\n"
    current_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=checkout_dir, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert current_sha == new_sha


def test_ensure_persistent_checkout_cleans_stray_untracked_files_on_reuse(bare_repo_with_two_commits, tmp_path):
    # A prior job could in principle leave behind stray build artifacts,
    # scratch files, or a half-written scan output - reuse must not let
    # those bleed into the next scan of the same repo.
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    checkout_dir = tmp_path / "checkout"

    _ensure_persistent_checkout(bare_path, head_sha, checkout_dir)
    (checkout_dir / "leftover-from-a-previous-job.tmp").write_text("stray")

    _ensure_persistent_checkout(bare_path, head_sha, checkout_dir)

    assert not (checkout_dir / "leftover-from-a-previous-job.tmp").exists()


def test_ensure_persistent_checkout_reuses_the_same_git_directory_not_a_fresh_clone(
    bare_repo_with_two_commits, tmp_path, monkeypatch
):
    bare_path, base_sha, head_sha = bare_repo_with_two_commits
    checkout_dir = tmp_path / "checkout"
    _ensure_persistent_checkout(bare_path, head_sha, checkout_dir)

    calls = []
    real_run = subprocess.run

    def spy_run(args, **kwargs):
        calls.append(args)
        return real_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy_run)

    _ensure_persistent_checkout(bare_path, head_sha, checkout_dir)

    assert not any(call[:2] == ["git", "clone"] for call in calls)
    assert any(call[:2] == ["git", "fetch"] for call in calls)
