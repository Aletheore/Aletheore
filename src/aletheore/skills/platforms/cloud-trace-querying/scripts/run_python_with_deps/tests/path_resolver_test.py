"""Unit tests for path_resolver utility."""

import hashlib
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

_script_dir = str(pathlib.Path(__file__).parent.parent / "src")
if _script_dir not in sys.path:
  sys.path.insert(0, _script_dir)

# pylint: disable=g-import-not-at-top
from path_resolver import _WORKSPACE_MARKERS
from path_resolver import find_workspace_root
from path_resolver import get_requirements_hash
from path_resolver import get_temp_dir
from path_resolver import get_workspace_identifier
from path_resolver import resolve_paths

# pylint: enable=g-import-not-at-top


class TestPathResolver(unittest.TestCase):

  def test_get_requirements_hash(self):
    """Verifies SHA256 hashing matches computed sum of temp requirements."""
    content = b"google-cloud-trace==1.20.0\n"
    expected = hashlib.sha256(content).hexdigest()
    with tempfile.NamedTemporaryFile("wb", delete=False) as temp_file:
      temp_file.write(content)
      temp_path = pathlib.Path(temp_file.name)
    try:
      self.assertEqual(get_requirements_hash(temp_path), expected)
    finally:
      temp_path.unlink(missing_ok=True)

  def test_find_workspace_root_with_all_marker_types(self):
    """Verifies find_workspace_root recognizes each distinct workspace marker type."""
    for marker in _WORKSPACE_MARKERS:
      with tempfile.TemporaryDirectory() as tmp_dir:
        root = pathlib.Path(tmp_dir)
        marker_path = root / marker
        if marker.startswith("."):
          marker_path.mkdir(parents=True, exist_ok=True)
        else:
          marker_path.touch()

        target_dir = root / "sub1" / "sub2"
        target_dir.mkdir(parents=True, exist_ok=True)

        found_root = find_workspace_root(target_dir)
        self.assertEqual(
            found_root,
            root.resolve(),
            f"Failed to find workspace root for marker '{marker}'",
        )

  def test_find_workspace_root_at_various_depths(self):
    """Verifies find_workspace_root works at 0, 1, 3, and 10 levels deep."""
    depths = [0, 1, 3, 10]
    for depth in depths:
      with tempfile.TemporaryDirectory() as tmp_dir:
        root = pathlib.Path(tmp_dir)
        (root / "WORKSPACE").touch()

        curr = root
        for i in range(depth):
          curr = curr / f"dir_{i}"
        curr.mkdir(parents=True, exist_ok=True)

        found_root = find_workspace_root(curr)
        self.assertEqual(
            found_root,
            root.resolve(),
            f"Failed to find workspace root at depth {depth}",
        )

  def test_find_workspace_root_when_no_markers_exist(self):
    """Verifies fallback when no workspace markers exist in parent tree."""
    with tempfile.TemporaryDirectory() as tmp_dir:
      deep_dir = pathlib.Path(tmp_dir) / "level1" / "level2" / "level3"
      deep_dir.mkdir(parents=True, exist_ok=True)

      original_exists = pathlib.Path.exists

      def fake_exists(self):
        if any(self.name == marker for marker in _WORKSPACE_MARKERS):
          return False
        return original_exists(self)

      with mock.patch.object(pathlib.Path, "exists", fake_exists):
        found_root = find_workspace_root(deep_dir)
        self.assertEqual(found_root, deep_dir.resolve())

  def test_find_workspace_root_at_filesystem_root(self):
    """Verifies find_workspace_root handles root directory '/' safely."""
    root_path = pathlib.Path("/")
    original_exists = pathlib.Path.exists

    def fake_exists(self):
      if any(self.name == marker for marker in _WORKSPACE_MARKERS):
        return False
      return original_exists(self)

    with mock.patch.object(pathlib.Path, "exists", fake_exists):
      found_root = find_workspace_root(root_path)
      self.assertEqual(found_root, root_path.resolve())

  def test_get_workspace_identifier_uniqueness_and_consistency(self):
    """Verifies workspace identifier produces consistent hashes and unique subpaths."""
    with (
        tempfile.TemporaryDirectory() as tmp_dir1,
        tempfile.TemporaryDirectory() as tmp_dir2,
    ):
      root1 = pathlib.Path(tmp_dir1)
      (root1 / ".git").mkdir()
      sub1 = root1 / "a" / "b"
      sub1.mkdir(parents=True)

      root2 = pathlib.Path(tmp_dir2)
      (root2 / ".git").mkdir()
      sub2 = root2 / "x" / "y"
      sub2.mkdir(parents=True)

      id1_a = get_workspace_identifier(sub1)
      id1_b = get_workspace_identifier(root1)
      id2 = get_workspace_identifier(sub2)

      self.assertEqual(id1_a, id1_b)
      self.assertNotEqual(id1_a, id2)
      self.assertIn("_", id1_a)

  def test_get_temp_dir_environment_overrides(self):
    """Verifies get_temp_dir respects TMP_DIR and TMPDIR environment variables."""
    with mock.patch.dict(
        os.environ, {"TMP_DIR": "/custom/tmp_dir"}, clear=True
    ):
      self.assertEqual(get_temp_dir(), pathlib.Path("/custom/tmp_dir"))

    with mock.patch.dict(os.environ, {"TMPDIR": "/custom/tmpdir"}, clear=True):
      self.assertEqual(get_temp_dir(), pathlib.Path("/custom/tmpdir"))

  def test_resolve_paths_returns_valid_structure(self):
    """Verifies resolved paths structure aligns with project layout."""
    req_file, venv_base, venv_dir = resolve_paths()
    self.assertTrue(req_file.is_absolute())
    self.assertEqual(req_file.name, "requirements.txt")
    self.assertIn("gcp_trace_querying_venvs", venv_base.as_posix())
    self.assertIn(venv_base.as_posix(), venv_dir.as_posix())


if __name__ == "__main__":
  unittest.main()
