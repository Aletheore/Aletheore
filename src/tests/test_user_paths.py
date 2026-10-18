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


# --- Windows (no os.getuid) path trust -------------------------------------
#
# Real gap found via audit: with os.getuid absent (true on Windows - this
# simulates it on any platform via monkeypatch, so it runs in CI everywhere,
# not just on a Windows runner), ownership/mode verification used to
# short-circuit to True unconditionally - trusting ANY pre-existing
# directory at this predictable path (not even per-user-unique: the name
# literally embeds "user", not a real identifier) with zero verification,
# even one left world-writable by a different account that planted it first.


def test_trusted_private_dir_never_trusts_a_preexisting_dir_without_getuid(tmp_path, monkeypatch):
    monkeypatch.delattr(os, "getuid", raising=False)
    preexisting = tmp_path / "aletheore-home-user"
    preexisting.mkdir(mode=0o777)

    result = user_paths._trusted_private_dir(preexisting)

    assert result is None


def test_trusted_private_dir_trusts_a_dir_it_just_created_without_getuid(tmp_path, monkeypatch):
    monkeypatch.delattr(os, "getuid", raising=False)
    fresh = tmp_path / "aletheore-home-user"

    result = user_paths._trusted_private_dir(fresh)

    assert result == fresh


def test_trusted_private_dir_never_trusts_a_preexisting_symlink_without_getuid(tmp_path, monkeypatch):
    monkeypatch.delattr(os, "getuid", raising=False)
    real_dir = tmp_path / "elsewhere"
    real_dir.mkdir()
    link = tmp_path / "aletheore-home-user"
    link.symlink_to(real_dir)

    result = user_paths._trusted_private_dir(link)

    assert result is None


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


@_posix_only
def test_mkdtemp_fallback_tries_one_more_private_dir_if_mkdtemp_fails(tmp_path, monkeypatch):
    # Real gap found via audit: mkdtemp's own OSError (e.g. TMPDIR full or
    # unwritable) previously fell straight to the bare, world-writable temp
    # dir - credentials.py and the license/vulnerability caches would then
    # write sensitive files somewhere every other user on the machine can
    # read. Now it tries one more private, deterministically-named dir first.
    def raise_oserror(prefix=""):
        raise OSError("no space left on device")

    monkeypatch.setattr(user_paths.tempfile, "mkdtemp", raise_oserror)
    monkeypatch.setattr(user_paths.tempfile, "gettempdir", lambda: str(tmp_path))
    path = user_paths._mkdtemp_fallback_home()
    assert path != tmp_path
    assert path.is_dir()
    assert stat.S_IMODE(path.lstat().st_mode) == 0o700


def test_mkdtemp_fallback_gives_up_to_the_bare_temp_dir_as_true_last_resort(tmp_path, monkeypatch):
    def raise_oserror(prefix=""):
        raise OSError("no space left on device")

    monkeypatch.setattr(user_paths.tempfile, "mkdtemp", raise_oserror)
    monkeypatch.setattr(user_paths.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(user_paths, "_trusted_private_dir", lambda path: None)
    assert user_paths._mkdtemp_fallback_home() == tmp_path


@_posix_only
def test_private_fallback_home_falls_through_to_mkdtemp_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(user_paths.tempfile, "gettempdir", lambda: str(tmp_path))
    uid = os.getuid() if hasattr(os, "getuid") else "user"
    (tmp_path / f"aletheore-home-{uid}").mkdir(mode=0o755)
    path = user_paths._private_fallback_home()
    assert path.is_dir()
    assert stat.S_IMODE(path.lstat().st_mode) != 0o755
