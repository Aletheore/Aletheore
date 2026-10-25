"""Executes target script within the context of the virtual environment.

Sets up required PYTHONPATH environment variables so that local shared modules
are visible, and execs the python binary within the virtual environment.
"""

import os
import pathlib
import sys


def execute_python(venv_dir: pathlib.Path) -> None:
  """Executes the target script using the virtualenv's Python binary.

  Prepends the shared scripts directory to PYTHONPATH, then calls os.execv to
  replace the current process with the virtualenv Python interpreter running
  the user's script.

  Args:
      venv_dir: The path to the virtualenv directory to run under.

  Raises:
      OSError: If the execv call fails (this is caught internally, printed to
        stderr, and triggers sys.exit(1)).
  """
  python_path = venv_dir / "bin" / "python3"
  script_dir = pathlib.Path(__file__).parent.resolve()
  scripts_dir = script_dir.parent.parent

  os.environ["PYTHONPATH"] = f"{scripts_dir}:{os.environ.get('PYTHONPATH', '')}"
  try:
    os.execv(str(python_path), [str(python_path)] + sys.argv[1:])
  except OSError as e:
    print(f"Error: Failed to execute {python_path}: {e}", file=sys.stderr)
    sys.exit(1)
