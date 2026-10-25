"""Unit tests for venv_setup manager."""

import pathlib
import subprocess
import sys
import unittest
from unittest import mock

_script_dir = str(pathlib.Path(__file__).parent.parent / "src")
if _script_dir not in sys.path:
  sys.path.insert(0, _script_dir)

# pylint: disable=g-import-not-at-top
from venv_setup import setup_venv_if_missing

# pylint: enable=g-import-not-at-top


class TestVenvSetup(unittest.TestCase):

  @mock.patch("venv_setup.pathlib.Path.exists")
  def test_setup_venv_if_exists_does_nothing(self, mock_exists):
    """Verifies setup skips installation if the target directory already exists."""
    mock_exists.return_value = True
    venv_dir = pathlib.Path("/tmp/existing_venv")
    req_file = pathlib.Path("/tmp/req.txt")
    with mock.patch("venv_setup.venv.create") as mock_create:
      setup_venv_if_missing(venv_dir, req_file)
      mock_create.assert_not_called()

  @mock.patch("venv_setup.pathlib.Path.exists")
  @mock.patch("venv_setup.venv.create")
  @mock.patch("venv_setup.subprocess.run")
  def test_setup_venv_initializes_missing(
      self, mock_run, mock_create, mock_exists
  ):
    """Verifies setup runs venv.create and pip install when target is missing."""
    mock_exists.return_value = False
    venv_dir = pathlib.Path("/tmp/missing_venv")
    req_file = pathlib.Path("/tmp/req.txt")

    mock_run.return_value = mock.MagicMock(returncode=0)

    with mock.patch("venv_setup.pathlib.Path.mkdir") as mock_mkdir:
      setup_venv_if_missing(venv_dir, req_file)
      mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)

    mock_create.assert_called_once_with(
        venv_dir, system_site_packages=True, with_pip=True
    )
    self.assertEqual(
        mock_run.call_count, 2
    )  # 1 for pip upgrade, 1 for requirements install


if __name__ == "__main__":
  unittest.main()
