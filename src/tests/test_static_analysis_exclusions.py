import json

from aletheore.static_analysis._exclusions import excluded_dir_names, filter_findings, has_real_file


def test_excluded_dir_names_includes_the_shared_ignored_dirs_and_worktrees(tmp_path):
    names = excluded_dir_names(tmp_path)

    assert ".git" in names
    assert "node_modules" in names
    assert ".claude" in names
    # Real gap found live: a second, sibling worktree directory this
    # repo's own EnterWorktree tool creates, confirmed present via `git
    # status` as real untracked content, not covered by the shared
    # IGNORED_DIRS constant at all.
    assert ".worktrees" in names
    assert ".repowise" in names


def test_excluded_dir_names_includes_bare_repo_config_entries(tmp_path):
    (tmp_path / ".aletheore.json").write_text(json.dumps({"ignored_paths": ["vendor", "generated/**"]}))

    names = excluded_dir_names(tmp_path)

    assert "vendor" in names
    # A slash-bearing pattern doesn't correspond to a simple top-level
    # directory name any scanner's native exclude flag understands -
    # filter_findings (not this function) is what still enforces it.
    assert "generated/**" not in names


def test_filter_findings_drops_matches_under_an_excluded_directory(tmp_path):
    findings = [
        {"path": ".claude/worktrees/agent-1/app.py", "line": 1},
        {"path": "src/app.py", "line": 1},
    ]

    result = filter_findings(findings, tmp_path)

    assert [f["path"] for f in result] == ["src/app.py"]


def test_filter_findings_drops_matches_under_a_repo_configured_ignored_path(tmp_path):
    (tmp_path / ".aletheore.json").write_text(json.dumps({"ignored_paths": ["vendor/**"]}))
    findings = [
        {"path": "vendor/lib/thing.py", "line": 1},
        {"path": "src/app.py", "line": 1},
    ]

    result = filter_findings(findings, tmp_path)

    assert [f["path"] for f in result] == ["src/app.py"]


def test_has_real_file_ignores_matches_under_excluded_directories(tmp_path):
    (tmp_path / ".claude" / "worktrees").mkdir(parents=True)
    (tmp_path / ".claude" / "worktrees" / "dup.go").write_text("package main\n")

    assert has_real_file(tmp_path, "*.go") is False

    (tmp_path / "main.go").write_text("package main\n")

    assert has_real_file(tmp_path, "*.go") is True
