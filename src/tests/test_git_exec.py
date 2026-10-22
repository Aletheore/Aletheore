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

    assert git_executable() == str(fake)
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
