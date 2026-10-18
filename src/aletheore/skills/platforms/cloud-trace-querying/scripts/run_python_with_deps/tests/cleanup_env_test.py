"""Unit tests for cleanup_env utility."""

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
from cleanup_env import _clean_if_unused
from cleanup_env import _remove_empty_parents

# pylint: enable=g-import-not-at-top


class TestCleanupEnv(unittest.TestCase):

  @mock.patch("cleanup_env.os.listdir")
  @mock.patch("cleanup_env.pathlib.Path.rmdir")
  def test_remove_empty_parents(self, mock_rmdir, mock_listdir):
    """Verifies that remove_empty_parents recursively deletes empty paths."""
    mock_listdir.side_effect = [
        [],
        ["some_file"],
    ]  # first is empty, second is not
    with mock.patch("cleanup_env.pathlib.Path.exists") as mock_exists:
      mock_exists.return_value = True
      # Starting path /tmp/a/b/c
      _remove_empty_parents(pathlib.Path("/tmp/a/b/c"))
      # It should call rmdir once on the empty leaf directory, then stop on parent.
      mock_rmdir.assert_called_once()

  @mock.patch("cleanup_env.os.open")
  @mock.patch("cleanup_env.os.close")
  @mock.patch("cleanup_env.fcntl.flock")
  @mock.patch("cleanup_env.shutil.rmtree")
  def test_clean_if_unused_success(
      self, mock_rmtree, mock_flock, mock_close, mock_open
  ):
    """Verifies environment is deleted if exclusive lock can be acquired."""
    mock_open.return_value = 55
    venv_dir = pathlib.Path("/tmp/old_venv")
    lock_file = pathlib.Path("/tmp/old_venv/use.lock")

    with mock.patch("cleanup_env._remove_empty_parents") as mock_remove_parents:
      _clean_if_unused(venv_dir, lock_file)
      mock_rmtree.assert_called_once_with(venv_dir)
      mock_remove_parents.assert_called_once()

    mock_flock.assert_called_once_with(55, fcntl.LOCK_EX | fcntl.LOCK_NB)
    mock_close.assert_called_once_with(55)

  @mock.patch("cleanup_env.os.open")
  @mock.patch("cleanup_env.fcntl.flock")
  @mock.patch("cleanup_env.shutil.rmtree")
  def test_clean_if_unused_blocked_skips(
      self, mock_rmtree, mock_flock, mock_open
  ):
    """Verifies deletion is skipped if a shared use lock blocks exclusive lock."""
    mock_open.return_value = 55
    mock_flock.side_effect = BlockingIOError
    venv_dir = pathlib.Path("/tmp/active_venv")
    lock_file = pathlib.Path("/tmp/active_venv/use.lock")

    _clean_if_unused(venv_dir, lock_file)
    mock_rmtree.assert_not_called()


if __name__ == "__main__":
  unittest.main()
