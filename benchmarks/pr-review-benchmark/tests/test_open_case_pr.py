import subprocess

from scripts.open_case_pr import open_case_pr, prepare_seed_and_head_trees


def _run(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def _rev_parse(cwd):
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def make_case_fixture(tmp_path):
    """A minimal real_bug_fix-shaped case: a small "source repo" with a
    real bug-fix commit, and a case_dir (repo.txt + pr.diff) pointing at
    it the same way a real corpus case does - deliberately with several
    OTHER unrelated files in the source repo too, so a test asserting the
    final PR diff touches exactly one file actually exercises the bug
    this script fixes (the old construction would show all of them)."""
    source = tmp_path / "source"
    source.mkdir()
    _run("git", "init", cwd=source)
    _run("git", "config", "user.email", "test@example.com", cwd=source)
    _run("git", "config", "user.name", "Test", cwd=source)
    (source / "README.md").write_text("unrelated file one\n")
    (source / "other.py").write_text("unrelated file two\n")
    (source / "buggy.py").write_text("def add(a, b):\n    return a + b + 1\n")
    _run("git", "add", ".", cwd=source)
    _run("git", "commit", "-m", "original buggy state", cwd=source)
    buggy_commit = _rev_parse(source)

    # base_commit points at the real FIX (matches the corpus convention:
    # case 002's own base_commit is literally the fix commit's own SHA).
    (source / "buggy.py").write_text("def add(a, b):\n    return a + b\n")
    _run("git", "add", "buggy.py", cwd=source)
    _run("git", "commit", "-m", "the real fix", cwd=source)
    base_commit = _rev_parse(source)

    # pr.diff is the inverse of the fix - applied on top of base_commit
    # (fixed), it reintroduces the bug, matching every real_bug_fix case
    # in the actual corpus.
    diff = subprocess.run(
        ["git", "diff", base_commit, buggy_commit, "--", "buggy.py"],
        cwd=source, check=True, capture_output=True, text=True,
    ).stdout

    source_remote = tmp_path / "source-remote.git"
    _run("git", "clone", "--bare", str(source), str(source_remote))

    case_dir = tmp_path / "cases" / "999-fixture-case"
    case_dir.mkdir(parents=True)
    (case_dir / "repo.txt").write_text(f"repo_url={source_remote}\nbase_commit={base_commit}\n")
    (case_dir / "pr.diff").write_text(diff)
    return case_dir


def make_scratch_fixture(tmp_path):
    """A bare "scratch repo" remote with an existing default-branch commit,
    plus a local clone of it - mirroring how open_case_pr is actually
    handed an already-cloned scratch_clone directory."""
    scratch_remote = tmp_path / "scratch-remote.git"
    seed = tmp_path / "scratch-seed"
    seed.mkdir()
    _run("git", "init", "-b", "main", cwd=seed)
    _run("git", "config", "user.email", "test@example.com", cwd=seed)
    _run("git", "config", "user.name", "Test", cwd=seed)
    (seed / "README.md").write_text("scratch repo\n")
    _run("git", "add", ".", cwd=seed)
    _run("git", "commit", "-m", "init", cwd=seed)
    _run("git", "clone", "--bare", str(seed), str(scratch_remote))

    scratch_clone = tmp_path / "scratch-clone"
    _run("git", "clone", str(scratch_remote), str(scratch_clone))
    _run("git", "checkout", "-B", "main", "origin/main", cwd=scratch_clone)
    return scratch_remote, scratch_clone


def test_prepare_seed_and_head_trees_seed_has_no_diff_applied(tmp_path):
    case_dir = make_case_fixture(tmp_path)
    from scripts.cases import load_repo_pointer

    seed_dir, head_dir = prepare_seed_and_head_trees(
        load_repo_pointer(case_dir), case_dir / "pr.diff", tmp_path / "work"
    )

    assert (seed_dir / "buggy.py").read_text() == "def add(a, b):\n    return a + b\n"
    assert (head_dir / "buggy.py").read_text() == "def add(a, b):\n    return a + b + 1\n"
    # Both unrelated files exist in both trees - confirms this isn't
    # accidentally stripping the rest of the repo, only diffing buggy.py.
    for tree in (seed_dir, head_dir):
        assert (tree / "README.md").exists()
        assert (tree / "other.py").exists()
    assert not (seed_dir / ".git").exists()


def test_open_case_pr_final_diff_touches_exactly_the_real_changed_file(tmp_path):
    # The actual bug this script fixes: the OLD construction (whole tree
    # dumped as one commit against main) would make this PR's diff touch
    # every file in the case repo (3, in this fixture - would be 100+ on
    # a real corpus case). The new seed/fix branch-pair construction must
    # make the PR's real diff (fix_branch vs seed_branch) touch only the
    # one file the case's own pr.diff actually changes.
    case_dir = make_case_fixture(tmp_path)
    _, scratch_clone = make_scratch_fixture(tmp_path)

    result = open_case_pr(case_dir, scratch_clone, tmp_path / "work", push=False)

    diff_stat = subprocess.run(
        ["git", "diff", "--stat", result["seed_branch"], result["fix_branch"]],
        cwd=scratch_clone, check=True, capture_output=True, text=True,
    ).stdout
    changed_files = subprocess.run(
        ["git", "diff", "--name-only", result["seed_branch"], result["fix_branch"]],
        cwd=scratch_clone, check=True, capture_output=True, text=True,
    ).stdout.splitlines()

    assert changed_files == ["benchmark-sandbox/999-fixture-case/buggy.py"], diff_stat
    assert (
        (scratch_clone / "benchmark-sandbox" / "999-fixture-case" / "buggy.py").read_text()
        == "def add(a, b):\n    return a + b + 1\n"
    )
    # Both unrelated files still exist on the fix branch - not a partial
    # tree, just a diff scoped to what actually changed.
    _run("git", "checkout", result["fix_branch"], cwd=scratch_clone)
    assert (scratch_clone / "benchmark-sandbox" / "999-fixture-case" / "README.md").exists()
    assert (scratch_clone / "benchmark-sandbox" / "999-fixture-case" / "other.py").exists()


def test_open_case_pr_invokes_pr_creator_with_the_seed_and_fix_branches(tmp_path):
    case_dir = make_case_fixture(tmp_path)
    _, scratch_clone = make_scratch_fixture(tmp_path)
    calls = []

    def fake_pr_creator(case_id, base_branch, head_branch):
        calls.append((case_id, base_branch, head_branch))
        return f"https://example.invalid/pull/1-{case_id}"

    result = open_case_pr(
        case_dir, scratch_clone, tmp_path / "work", push=False, pr_creator=fake_pr_creator
    )

    assert calls == [("999-fixture-case", "seed/999-fixture-case", "fix/999-fixture-case")]
    assert result["pr"] == "https://example.invalid/pull/1-999-fixture-case"


def test_open_case_pr_is_rerunnable_without_a_stale_dirty_worktree(tmp_path):
    # Real risk with -B (force-checkout/reset a branch): running this
    # script twice for the same case (e.g. after fixing a corpus typo)
    # must not fail on a dirty working tree left over from the previous
    # run's sandbox-directory swap.
    case_dir = make_case_fixture(tmp_path)
    _, scratch_clone = make_scratch_fixture(tmp_path)

    open_case_pr(case_dir, scratch_clone, tmp_path / "work1", push=False)
    result = open_case_pr(case_dir, scratch_clone, tmp_path / "work2", push=False)

    changed_files = subprocess.run(
        ["git", "diff", "--name-only", result["seed_branch"], result["fix_branch"]],
        cwd=scratch_clone, check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    assert changed_files == ["benchmark-sandbox/999-fixture-case/buggy.py"]
