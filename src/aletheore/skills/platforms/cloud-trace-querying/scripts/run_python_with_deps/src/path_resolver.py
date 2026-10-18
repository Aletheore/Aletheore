"""Path resolution utility for virtualenv management.

Computes unique hashes of requirements.txt configurations and maps absolute
locations for active virtual environments.
"""

import datetime
import hashlib
import os
import pathlib
import tempfile

_WORKSPACE_MARKERS = (
    ".git",
    ".jj",
    ".hg",
    "WORKSPACE",
    "WORKSPACE.bazel",
    "MODULE.bazel",
)


def get_requirements_hash(req_file: pathlib.Path) -> str:
  """Computes a SHA256 hash of the requirements file contents.

  This hash is used as a directory component to uniquely identify a distinct set
  of dependency requirements, ensuring that environment setup only triggers
  when requirements are changed.

  Args:
      req_file: The path to the requirements.txt file.

  Returns:
      A string containing the SHA256 hex representation of the file contents.
  """
  h = hashlib.sha256()
  with open(req_file, "rb") as f:
    h.update(f.read())
  return h.hexdigest()


def find_workspace_root(path: pathlib.Path) -> pathlib.Path:
  """Walks up from path until finding a directory containing a workspace marker.

  Args:
      path: File or directory path to start searching from.

  Returns:
      The workspace root directory Path if a marker is found, or the resolved
      starting path.
  """
  resolved_path = path.resolve()
  for current in [resolved_path] + list(resolved_path.parents):
    for marker in _WORKSPACE_MARKERS:
      if (current / marker).exists():
        return current
  return resolved_path


def get_workspace_identifier(path: pathlib.Path) -> str:
  """Extracts a unique, fingerprinted workspace subpath identifier.

  Fingerprints the workspace root directory path (including all ancestor paths)
  to construct a collision-free per-workspace subpath.

  Args:
      path: Path to resolve workspace identifier for.

  Returns:
      A unique per-workspace subpath string formatted as 'dirname_hash'.
  """
  ws_root = find_workspace_root(path)
  full_path_str = str(ws_root)
  ws_name = ws_root.name or "workspace"
  path_hash = hashlib.sha256(full_path_str.encode("utf-8")).hexdigest()[:12]
  return f"{ws_name}_{path_hash}"


def get_temp_dir() -> pathlib.Path:
  """Returns temporary directory path respecting TMP_DIR or TMPDIR overrides."""
  tmp_env = os.environ.get("TMP_DIR") or os.environ.get("TMPDIR")
  if tmp_env:
    return pathlib.Path(tmp_env)
  return pathlib.Path(tempfile.gettempdir())


def resolve_paths() -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
  """Resolves paths for requirements, virtualenv base, and active virtualenv.

  The active virtualenv path is partitioned by fingerprinted workspace ID, date
  (YYYY/MM/DD) and config hash to enforce daily virtualenv refresh cycles and
  isolate concurrent evaluation runs.

  Returns:
      A tuple containing:
      - req_file: Path to requirements.txt.
      - venv_base: Path to parent virtualenv base directory under temp space
        (workspace-specific).
      - venv_dir: Path to the specific virtualenv directory.
  """
  this_dir = pathlib.Path(__file__).parent.resolve()
  req_file = this_dir.parent.parent / "requirements.txt"
  if not req_file.exists():
    req_file = this_dir.parent.parent.parent / "requirements.txt"
  root_base = get_temp_dir() / "gcp_trace_querying_venvs"
  ws_id = get_workspace_identifier(this_dir)
  venv_base = root_base / ws_id

  date_path = datetime.date.today().strftime("%Y/%m/%d")
  venv_dir = venv_base / date_path / get_requirements_hash(req_file)
  return req_file, venv_base, venv_dir
