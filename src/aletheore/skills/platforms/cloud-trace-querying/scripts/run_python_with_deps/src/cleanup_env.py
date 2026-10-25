"""Cleans up old, unused virtual environments in a background process.

We create a new virtual environment directory daily (using the current date path
in the hash) to ensure that updates to standard dependencies or requirements
are picked up eventually, even if the requirements hash didn't change.
To prevent these daily virtual environments from filling up the disk
indefinitely,
this background cleanup task deletes any virtualenv whose use.lock file is not
exclusively locked (indicating it is not currently in use by any active process)
and does not belong to the current day.
"""

import datetime
import fcntl
import os
import pathlib
import shutil
import sys


def _remove_empty_parents(d: pathlib.Path):
  """Prunes empty parent date directories up to 3 levels (YYYY/MM/DD).

  Args:
      d: The parent directory path to start pruning from.
  """
  for _ in range(3):
    if d.exists() and not os.listdir(d):
      d.rmdir()
      d = d.parent
    else:
      break


def _clean_if_unused(venv_dir: pathlib.Path, lock_file: pathlib.Path):
  """Attempts to delete the virtualenv if it is not currently in use.

  It attempts to acquire an exclusive non-blocking lock on the virtualenv's
  use.lock. If successful, it means no other active script process is currently
  running using this environment, and it is safe to delete it.

  Args:
      venv_dir: The absolute path to the virtualenv directory to delete.
      lock_file: The path to the use.lock file inside the virtualenv.
  """
  try:
    fd = os.open(lock_file, os.O_WRONLY)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.close(fd)
    shutil.rmtree(venv_dir)
    _remove_empty_parents(venv_dir.parent)
  except (BlockingIOError, OSError):
    pass


def main():
  """Sweeps the virtualenv base directory and triggers cleanup on older envs."""
  if len(sys.argv) < 2:
    sys.exit(1)
  venv_base = pathlib.Path(sys.argv[1])
  today_str = datetime.date.today().strftime("%Y/%m/%d")
  for lock_file in venv_base.glob("**/use.lock"):
    if today_str not in str(lock_file):
      _clean_if_unused(lock_file.parent, lock_file)


if __name__ == "__main__":
  main()
