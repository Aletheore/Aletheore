"""Unit tests for lock_manager utility."""

import fcntl
import os
import pathlib
import sys
import unittest
from unittest import mock

_script_dir = str(pathlib.Path(__file__).parent.parent / "src")
if _script_dir not in sys.path:
  sys.path.insert(0, _script_dir)

# pylint: disable=g-import-not-at-top
from lock_manager import acquire_setup_lock
from lock_manager import acquire_use_lock
from lock_manager import release_setup_lock

# pylint: enable=g-import-not-at-top


class TestLockManager(unittest.TestCase):

  @mock.patch("lock_manager.os.open")
  @mock.patch("lock_manager.fcntl.flock")
  def test_acquire_setup_lock(self, mock_flock, mock_open):
    """Verifies that acquire_setup_lock opens lock file and requests exclusive lock."""
    mock_open.return_value = 42
    venv_base = pathlib.Path("/tmp/test_venvs")
    fd = acquire_setup_lock(venv_base, "dummy_hash")
    self.assertEqual(fd, 42)
    mock_open.assert_called_once()
    mock_flock.assert_called_once_with(42, fcntl.LOCK_EX)

  @mock.patch("lock_manager.os.close")
  @mock.patch("lock_manager.fcntl.flock")
  def test_release_setup_lock(self, mock_flock, mock_close):
    """Verifies that release_setup_lock releases lock and closes fd."""
    release_setup_lock(100)
    mock_flock.assert_called_once_with(100, fcntl.LOCK_UN)
    mock_close.assert_called_once_with(100)

  @mock.patch("lock_manager.os.open")
  @mock.patch("lock_manager.fcntl.flock")
  @mock.patch("lock_manager.os.set_inheritable")
  def test_acquire_use_lock(self, mock_inheritable, mock_flock, mock_open):
    """Verifies that acquire_use_lock requests shared lock and flags inheritable."""
    mock_open.return_value = 84
    venv_dir = pathlib.Path("/tmp/test_venvs/dummy_venv")
    with mock.patch("lock_manager.pathlib.Path.touch") as mock_touch:
      fd = acquire_use_lock(venv_dir)
      self.assertEqual(fd, 84)
      mock_touch.assert_called_once()
    mock_flock.assert_called_once_with(84, fcntl.LOCK_SH)
    mock_inheritable.assert_called_once_with(84, True)


if __name__ == "__main__":
  unittest.main()
