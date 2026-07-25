import subprocess
from scripts.build_case_repo import prepare_case_checkout


def _run(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def make_fixture_repo(tmp_path):
    remote = tmp_path / "remote.git"
    work = tmp_path / "seed"
    work.mkdir()
    _run("git", "init", cwd=work)
    _run("git", "config", "user.email", "test@example.com", cwd=work)
    _run("git", "config", "user.name", "Test", cwd=work)
    (work / "x.py").write_text("value = 1\n")
    _run("git", "add", "x.py", cwd=work)
    _run("git", "commit", "-m", "base", cwd=work)
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=work, check=True, capture_output=True, text=True
    ).stdout.strip()

    (work / "x.py").write_text("value = 2\n")
    _run("git", "add", "x.py", cwd=work)
    _run("git", "commit", "-m", "change value", cwd=work)

    diff_path = tmp_path / "pr.diff"
    diff = subprocess.run(
        ["git", "diff", base_commit, "HEAD"], cwd=work, check=True, capture_output=True, text=True
    ).stdout
    diff_path.write_text(diff)

    _run("git", "clone", "--bare", str(work), str(remote))
    return remote, base_commit, diff_path


def test_prepare_case_checkout_applies_pr_diff_on_base_commit(tmp_path):
    remote, base_commit, diff_path = make_fixture_repo(tmp_path)
    workdir = tmp_path / "work"
    workdir.mkdir()

    checkout_dir = prepare_case_checkout(
        {"repo_url": str(remote), "base_commit": base_commit}, diff_path, workdir
    )

    assert (checkout_dir / "x.py").read_text() == "value = 2\n"
