import os
import stat
import sys

import pytest

from aletheore import user_paths

_posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits only")


@pytest.fixture(autouse=True)
def _clear_mkdtemp_cache():
    user_paths._mkdtemp_fallback_home.cache_clear()
    yield
    user_paths._mkdtemp_fallback_home.cache_clear()


def test_user_home_returns_path_home_when_available():
    assert user_paths.user_home() == user_paths.Path.home()


def test_private_fallback_home_creates_and_returns_the_deterministic_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(user_paths.tempfile, "gettempdir", lambda: str(tmp_path))
    path = user_paths._private_fallback_home()
    assert path.is_dir()
    assert path == tmp_path / f"aletheore-home-{os.getuid() if hasattr(os, 'getuid') else 'user'}"


@_posix_only
def test_private_fallback_home_rejects_a_preexisting_dir_with_loose_permissions(tmp_path, monkeypatch):
    # Real gap found via audit: mkdir(exist_ok=True) never changes the mode
    # of a directory that already exists, so a dir we own but that was
    # created (by an earlier run, before this check existed) with looser
    # permissions would otherwise pass every other check and be trusted.
    monkeypatch.setattr(user_paths.tempfile, "gettempdir", lambda: str(tmp_path))
    uid = os.getuid() if hasattr(os, "getuid") else "user"
    preexisting = tmp_path / f"aletheore-home-{uid}"
    preexisting.mkdir(mode=0o755)
    path = user_paths._private_fallback_home()
    assert path != preexisting, "a world-readable pre-existing dir must not be trusted as private"


@_posix_only
def test_private_fallback_home_rejects_a_preexisting_symlink(tmp_path, monkeypatch):
    monkeypatch.setattr(user_paths.tempfile, "gettempdir", lambda: str(tmp_path))
    uid = os.getuid() if hasattr(os, "getuid") else "user"
    real_dir = tmp_path / "elsewhere"
    real_dir.mkdir(mode=0o700)
    (tmp_path / f"aletheore-home-{uid}").symlink_to(real_dir)
    path = user_paths._private_fallback_home()
    assert path != real_dir


def test_mkdtemp_fallback_is_called_at_most_once_per_process(tmp_path, monkeypatch):
    # Real gap found via audit: every DEFAULT_*_PATH constant calls
    # user_home() independently at import time, so without caching, each
    # of several call sites in the same process would land in a different
    # freshly mkdtemp'd directory, scattering what is supposed to be one
    # shared fallback home.
    calls = []

    def fake_mkdtemp(prefix=""):
        d = tmp_path / f"{prefix}{len(calls)}"
        d.mkdir()
        calls.append(d)
        return str(d)

    monkeypatch.setattr(user_paths.tempfile, "mkdtemp", fake_mkdtemp)
    first = user_paths._mkdtemp_fallback_home()
    second = user_paths._mkdtemp_fallback_home()
    assert first == second
    assert len(calls) == 1


def test_mkdtemp_fallback_degrades_to_the_bare_temp_dir_if_mkdtemp_fails(tmp_path, monkeypatch):
    # Real gap found via audit: mkdtemp's own OSError (e.g. TMPDIR full or
    # unwritable) was not caught, reintroducing the import-time crash this
    # whole module exists to prevent.
    def raise_oserror(prefix=""):
        raise OSError("no space left on device")

    monkeypatch.setattr(user_paths.tempfile, "mkdtemp", raise_oserror)
    monkeypatch.setattr(user_paths.tempfile, "gettempdir", lambda: str(tmp_path))
    assert user_paths._mkdtemp_fallback_home() == tmp_path


@_posix_only
def test_private_fallback_home_falls_through_to_mkdtemp_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(user_paths.tempfile, "gettempdir", lambda: str(tmp_path))
    uid = os.getuid() if hasattr(os, "getuid") else "user"
    (tmp_path / f"aletheore-home-{uid}").mkdir(mode=0o755)
    path = user_paths._private_fallback_home()
    assert path.is_dir()
    assert stat.S_IMODE(path.lstat().st_mode) != 0o755
