"""Prepares a local checkout of a test case's PR-under-test: clone the
repo, check out base_commit, apply pr.diff on top, and expand any corpus
fixture placeholders (see scripts/fixtures.py)."""
import subprocess
from pathlib import Path

from scripts.fixtures import expand_placeholders_in_tree


def prepare_case_checkout(repo_pointer: dict, diff_path: Path, workdir: Path) -> Path:
    checkout_dir = Path(workdir) / "checkout"

    try:
        subprocess.run(
            ["git", "clone", repo_pointer["repo_url"], str(checkout_dir)],
            check=True, capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"git clone failed: {e.stderr.decode()}"
        ) from e

    try:
        subprocess.run(
            ["git", "checkout", repo_pointer["base_commit"]],
            cwd=checkout_dir, check=True, capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"git checkout {repo_pointer['base_commit']} failed: {e.stderr.decode()}"
        ) from e

    try:
        subprocess.run(
            ["git", "apply", str(diff_path)],
            cwd=checkout_dir, check=True, capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"git apply failed: {e.stderr.decode()}"
        ) from e

    # After the diff lands, so placeholders introduced by the diff itself
    # (case 020's hardcoded secret) get expanded too.
    expand_placeholders_in_tree(checkout_dir)

    return checkout_dir
