"""Lock management utility for virtual environment setup and usage.

To prevent race conditions when multiple skill tool scripts are invoked
simultaneously, we implement two locking mechanisms:
1. Setup Lock (.setup.lock): An exclusive flock acquired during setup.
   This prevents concurrent execution instances from running 'pip install' or
   'venv.create' on the same virtualenv directory simultaneously, which would
   corrupt the virtual environment.
2. Use Lock (use.lock): A shared flock acquired during python script execution.
   It remains active as long as the Python process runs (making the file
   descriptor
   inheritable across execv). The background cleanup sweep checks if it can
   acquire
   an exclusive lock on use.lock; if it fails (because a shared use lock is
   held),
   it knows the environment is still in use and skips deletion.
"""

import fcntl
import os
import pathlib


def acquire_setup_lock(venv_base: pathlib.Path, req_hash: str) -> int:
  """Acquires an exclusive blocking lock for the setup phase of the virtualenv.

  This blocks any concurrent process trying to initialize or write to the
  same virtualenv directory until the current process completes installation
  and releases it.

  Args:
      venv_base: The path to the parent directory of all virtualenvs.
      req_hash: The sha256 hash string of requirements.txt.

  Returns:
      The open file descriptor pointing to the setup lock file.
  """
  lock_dir = venv_base / "locks"
  lock_dir.mkdir(parents=True, exist_ok=True)
  setup_lock_file = lock_dir / f"{req_hash}.setup.lock"
  fd = os.open(setup_lock_file, os.O_CREAT | os.O_WRONLY, 0o666)
  fcntl.flock(fd, fcntl.LOCK_EX)
  return fd


def release_setup_lock(fd: int):
  """Releases the exclusive setup lock and closes its file descriptor.

  Args:
      fd: The open file descriptor representing the setup lock.
  """
  fcntl.flock(fd, fcntl.LOCK_UN)
  os.close(fd)


def acquire_use_lock(venv_dir: pathlib.Path) -> int:
  """Acquires a shared lock representing active execution usage.

  This file descriptor is explicitly set to be inheritable so that the flock
  remains held by the Python process when it transitions via execv into
  the target python execution context.

  Args:
      venv_dir: The directory of the active virtualenv.

  Returns:
      The open file descriptor pointing to the use lock file.
  """
  use_lock_file = venv_dir / "use.lock"
  use_lock_file.touch(exist_ok=True)
  fd = os.open(use_lock_file, os.O_RDONLY)
  fcntl.flock(fd, fcntl.LOCK_SH)
  os.set_inheritable(fd, True)
  return fd
