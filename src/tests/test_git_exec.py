import os
import stat
import sys

import pytest

from aletheore.git_exec import git_executable


def _fake_git(directory):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("git.bat" if sys.platform == "win32" else "git")
    path.write_text("@echo off\n" if sys.platform == "win32" else "#!/bin/sh\nexit 0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_resolves_to_an_absolute_path(tmp_path, monkeypatch):
    fake = _fake_git(tmp_path / "bin")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))

    # shutil.which returns the PATHEXT spelling on Windows (git.BAT for git.bat), so compare case-folded there.
    assert os.path.normcase(git_executable()) == os.path.normcase(str(fake))
    assert os.path.isabs(git_executable())


def test_a_relative_path_entry_is_made_absolute_against_the_current_directory(tmp_path, monkeypatch):
    _fake_git(tmp_path / "tools")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "tools")

    resolved = git_executable()

    assert os.path.isabs(resolved)
    assert resolved.startswith(str(tmp_path.resolve())) or resolved.startswith(str(tmp_path))


def test_missing_git_raises_instead_of_falling_back_to_the_bare_name(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(FileNotFoundError):
        git_executable()


def test_absolute_path_only_never_resolves_git_from_a_relative_entry_or_the_current_directory(tmp_path, monkeypatch):
    # Shield's hooks start with the repository as their current directory: a repository must not be able to plant a
    # git that a relative PATH entry (or Windows' implicit current-directory search) would pick.
    _fake_git(tmp_path / "tools")
    _fake_git(tmp_path)  # git in the current directory itself
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", os.pathsep.join(["tools", ".", ""]))
    with pytest.raises(FileNotFoundError):
        git_executable(absolute_path_only=True)
    real = _fake_git(tmp_path / "abs" / "bin")
    monkeypatch.setenv("PATH", os.pathsep.join(["tools", str(tmp_path / "abs" / "bin")]))
    assert os.path.normcase(git_executable(absolute_path_only=True)) == os.path.normcase(str(real))


def test_shield_resolves_git_from_absolute_path_entries_only(tmp_path, monkeypatch):
    from aletheore.shield import gitutil

    _fake_git(tmp_path / "tools")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "tools")
    assert gitutil.run_git(tmp_path, "--version").returncode == gitutil._SPAWN_FAILED
