"""Virtual environment initialization and package installation manager.

Handles directory setup, python virtual environment initialization, and
package installations from requirements configuration.
"""

import pathlib
import subprocess
import sys
import venv


def create_and_upgrade_pip(venv_dir: pathlib.Path, pip_path: pathlib.Path):
  """Creates the virtualenv directory and upgrades pip inside it.

  Args:
      venv_dir: The target path where the virtualenv should be created.
      pip_path: The expected absolute path to the pip executable.
  """
  venv_dir.mkdir(parents=True, exist_ok=True)
  venv.create(venv_dir, system_site_packages=True, with_pip=True)
  upgrade_args = [
      str(pip_path),
      "install",
      "--timeout",
      "3",
      "--retries",
      "0",
      "--quiet",
      "--upgrade",
      "pip",
  ]
  subprocess.run(upgrade_args, capture_output=True, check=False)


def install_requirements(pip_path: pathlib.Path, req_file: pathlib.Path):
  """Installs dependencies from the requirements file via pip.

  Args:
      pip_path: The absolute path to the pip executable.
      req_file: The path to the requirements.txt file to install.
  """
  install_args = [
      str(pip_path),
      "install",
      "--timeout",
      "3",
      "--retries",
      "0",
      "--quiet",
      "-r",
      str(req_file),
  ]
  res = subprocess.run(install_args, check=False)
  if res.returncode != 0:
    print(
        "Warning: Failed to install dependencies. Proceeding anyway...",
        file=sys.stderr,
    )


def setup_venv_if_missing(venv_dir: pathlib.Path, req_file: pathlib.Path):
  """Initializes virtual environment and dependencies if not already present.

  Args:
      venv_dir: The target path where the virtualenv should exist.
      req_file: The path to requirements.txt.
  """
  if venv_dir.exists():
    return
  print(f"Creating virtual environment in {venv_dir}...", file=sys.stderr)
  pip_path = venv_dir / "bin" / "pip"
  create_and_upgrade_pip(venv_dir, pip_path)
  print("Installing dependencies from requirements.txt...", file=sys.stderr)
  install_requirements(pip_path, req_file)
