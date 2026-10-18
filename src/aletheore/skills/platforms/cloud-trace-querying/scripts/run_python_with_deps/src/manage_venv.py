"""Manages virtualenv creation, locking, requirements sync, and cleanup."""

import pathlib
import sys

# Ensure sibling modules under src/ can be imported when PYTHONSAFEPATH=1.
_script_dir = str(pathlib.Path(__file__).parent.resolve())
if _script_dir not in sys.path:
  sys.path.insert(0, _script_dir)

# pylint: disable=g-import-not-at-top
from lock_manager import acquire_setup_lock
from lock_manager import acquire_use_lock
from lock_manager import release_setup_lock
from path_resolver import get_requirements_hash
from path_resolver import resolve_paths
from venv_cleaner import trigger_background_cleanup
from venv_executor import execute_python
from venv_setup import setup_venv_if_missing

# pylint: enable=g-import-not-at-top


def setup_and_lock_venv(
    venv_base: pathlib.Path, venv_dir: pathlib.Path, req_file: pathlib.Path
):
  """Secures setup and execution locks, and initializes the virtualenv.

  Args:
      venv_base: The path to the parent virtualenv directory.
      venv_dir: The target path where the virtualenv should exist.
      req_file: The path to requirements.txt.
  """
  req_hash = get_requirements_hash(req_file)
  setup_fd = acquire_setup_lock(venv_base, req_hash)
  setup_venv_if_missing(venv_dir, req_file)
  acquire_use_lock(venv_dir)
  release_setup_lock(setup_fd)


def main():
  """Main entrypoint orchestrating path setup, locking, and execution."""
  if len(sys.argv) < 2:
    print("Usage: manage_venv.py <script.py> [args...]", file=sys.stderr)
    sys.exit(1)
  req_file, venv_base, venv_dir = resolve_paths()
  setup_and_lock_venv(venv_base, venv_dir, req_file)
  trigger_background_cleanup(venv_base.parent)
  execute_python(venv_dir)


if __name__ == "__main__":
  main()
