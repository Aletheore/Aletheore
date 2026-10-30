import subprocess

import pytest

from aletheore.shield import denylist, gitutil, state


def _git(repo, *args):
    subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True,
    )


def _init(path, commit=True):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "T")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "commit.gpgsign", "false")
    if commit:
        (path / "a.txt").write_text("a\n")
        _git(path, "add", "a.txt")
        _git(path, "commit", "-q", "-m", "init")
    return path


def test_repo_state_reports_each_unsupported_case(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    st = gitutil.repo_state(plain)
    assert st.ok is False and st.reason

    bare = tmp_path / "bare"
    bare.mkdir()
    _git(bare, "init", "-q", "--bare")
    st = gitutil.repo_state(bare)
    assert st.ok is False and st.reason

    detached = _init(tmp_path / "detached")
    _git(detached, "checkout", "-q", "--detach")
    st = gitutil.repo_state(detached)
    assert st.ok is False and st.reason

    rebasing = _init(tmp_path / "rebasing")
    (rebasing / ".git" / "rebase-merge").mkdir()
    st = gitutil.repo_state(rebasing)
    assert st.ok is False and st.reason

    merging = _init(tmp_path / "merging")
    (merging / ".git" / "MERGE_HEAD").write_text("0" * 40 + "\n")
    st = gitutil.repo_state(merging)
    assert st.ok is False and st.reason

    fresh = _init(tmp_path / "fresh", commit=False)
    st = gitutil.repo_state(fresh)
    assert st.ok is True and st.has_head is False and st.git_dir is not None

    normal = _init(tmp_path / "normal")
    st = gitutil.repo_state(normal)
    assert st.ok is True and st.has_head is True


def test_repo_state_accepts_linked_worktree(tmp_path):
    main = _init(tmp_path / "main")
    linked = tmp_path / "linked"
    _git(main, "worktree", "add", "-q", "-b", "side", str(linked))
    assert (linked / ".git").is_file()
    st = gitutil.repo_state(linked)
    assert st.ok is True and st.has_head is True


def test_run_git_handles_unicode_and_spaces_in_paths(tmp_path):
    repo = _init(tmp_path / "repo", commit=False)
    name = "dir with space/café 日本.txt"
    target = repo / name
    target.parent.mkdir()
    target.write_text("x\n")
    result = gitutil.run_git(repo, "ls-files", "-z", "--others", "--exclude-standard")
    assert result.returncode == 0
    assert result.stdout.decode("utf-8").split("\0")[:-1] == [name]


def test_run_git_nonzero_exit_does_not_raise(tmp_path):
    repo = _init(tmp_path / "repo")
    result = gitutil.run_git(repo, "rev-parse", "--verify", "no-such-ref")
    assert result.returncode != 0


def test_corrupt_config_falls_back_to_defaults(tmp_path):
    assert state.load_config(tmp_path) == state.ShieldConfig()
    assert not (tmp_path / ".aletheore").exists()
    directory = state.shield_dir(tmp_path, create=True)
    (directory / "config.json").write_text("{not json", encoding="utf-8")
    assert state.load_config(tmp_path) == state.ShieldConfig()
    state.save_config(tmp_path, state.ShieldConfig(registry_lookups=False))
    assert state.load_config(tmp_path).registry_lookups is False


def test_offline_env_disables_registry_lookups(tmp_path, monkeypatch):
    monkeypatch.delenv("ALETHEORE_SHIELD_OFFLINE", raising=False)
    assert state.registry_lookups_enabled(tmp_path) is True
    for value in ("1", "true", "yes"):
        monkeypatch.setenv("ALETHEORE_SHIELD_OFFLINE", value)
        assert state.registry_lookups_enabled(tmp_path) is False


def test_session_state_path_sanitizes_ids(tmp_path):
    assert state.session_state_path(tmp_path, "abc-123") == state.shield_dir(tmp_path) / "sessions" / "abc-123.json"
    for bad in ("", ".", "..", "a/b", "a\\b", "C:foo", "a:b", "../x", "x\0y", "ends.", ".hidden", "a" * 129):
        assert state.session_state_path(tmp_path, bad) is None
    for good in ("abc-123", "A.b_c", "a" * 128):
        p = state.session_state_path(tmp_path, good)
        assert p.parent == state.shield_dir(tmp_path) / "sessions"
    assert not (tmp_path / ".aletheore").exists()


@pytest.mark.parametrize(
    "path,expected",
    [
        (".env", True),
        (".env.local", True),
        ("a/b/server.pem", True),
        ("id_rsa.pub", True),
        ("credentials.json", True),
        ("src/environment.py", False),
        ("src/app.py", False),
        (".envrc", True),
        ("credentials.yml", True),
        ("ID_RSA", True),
        ("Foo.PEM", True),
        ("config/.ENV.Production", True),
    ],
)
def test_is_denied_path(path, expected):
    assert denylist.is_denied_path(path) is expected


def test_run_git_timeout_and_spawn_failure_do_not_raise(tmp_path, monkeypatch):
    def timeout(*a, **k):
        raise subprocess.TimeoutExpired("git", 0.01)

    monkeypatch.setattr(gitutil.subprocess, "run", timeout)
    result = gitutil.run_git(tmp_path, "status", timeout=0.01)
    assert result.returncode == 124 and result.stdout == b""
    st = gitutil.repo_state(tmp_path)
    assert st.ok is False and "not a git repository" not in st.reason

    def broken(*a, **k):
        raise PermissionError("nope")

    monkeypatch.setattr(gitutil.subprocess, "run", broken)
    result = gitutil.run_git(tmp_path, "status")
    assert result.returncode == 127 and result.stdout == b""


def test_run_git_drops_inherited_git_vars_but_keeps_explicit_env(tmp_path, monkeypatch):
    seen = {}

    def fake(command, **k):
        seen.update(k["env"])
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setenv("GIT_DIR", "/nowhere")
    monkeypatch.setenv("GIT_INDEX_FILE", "/nowhere/index")
    monkeypatch.setattr(gitutil.subprocess, "run", fake)
    gitutil.run_git(tmp_path, "status", env={"GIT_INDEX_FILE": "/mine"})
    assert "GIT_DIR" not in seen and seen["GIT_INDEX_FILE"] == "/mine"


def test_config_registry_lookups_false_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("ALETHEORE_SHIELD_OFFLINE", raising=False)
    state.save_config(tmp_path, state.ShieldConfig(registry_lookups=False))
    assert state.registry_lookups_enabled(tmp_path) is False
