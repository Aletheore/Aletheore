"""Prepares a local checkout of a test case's PR-under-test: clone the
repo, check out base_commit, and apply pr.diff on top."""
import subprocess
from pathlib import Path


def prepare_case_checkout(repo_pointer: dict, diff_path: Path, workdir: Path) -> Path:
    checkout_dir = Path(workdir) / "checkout"
    subprocess.run(
        ["git", "clone", repo_pointer["repo_url"], str(checkout_dir)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", repo_pointer["base_commit"]],
        cwd=checkout_dir, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "apply", str(diff_path)],
        cwd=checkout_dir, check=True, capture_output=True,
    )
    return checkout_dir
