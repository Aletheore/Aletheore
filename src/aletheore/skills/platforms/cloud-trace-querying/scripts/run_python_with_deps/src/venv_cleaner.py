"""Triggers background cleanup task for old virtual environments.

Spawns an asynchronous process that runs independently of the main thread
to sweep and delete expired virtual environments.
"""

import pathlib
import subprocess
import sys


def trigger_background_cleanup(venv_base: pathlib.Path):
  """Spawns cleanup_env.py in the background to clean up stale virtualenvs.

  This triggers a sweep of old directories without blocking the main execution
  thread of the active script.

  Args:
      venv_base: The path to the parent directory containing all virtualenvs.
  """
  script_dir = pathlib.Path(__file__).parent.resolve()
  subprocess.Popen(
      [sys.executable, str(script_dir / "cleanup_env.py"), str(venv_base)],
      stdout=subprocess.DEVNULL,
      stderr=subprocess.DEVNULL,
  )
