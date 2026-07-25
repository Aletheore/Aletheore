"""Loads and validates a benchmark test case directory."""
from pathlib import Path
import yaml

VALID_CATEGORIES = {"real_bug_fix", "injected_bug", "clean"}


def load_repo_pointer(case_dir: Path) -> dict:
    text = (Path(case_dir) / "repo.txt").read_text()
    pointer = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        pointer[key.strip()] = value.strip()
    if "repo_url" not in pointer or "base_commit" not in pointer:
        raise ValueError(f"{case_dir}/repo.txt must define repo_url and base_commit")
    return pointer


def load_ground_truth(case_dir: Path) -> dict:
    data = yaml.safe_load((Path(case_dir) / "ground_truth.yaml").read_text())
    if data.get("category") not in VALID_CATEGORIES:
        raise ValueError(
            f"{case_dir}/ground_truth.yaml: category must be one of {VALID_CATEGORIES}"
        )
    return data


def load_case(case_dir: Path) -> dict:
    case_dir = Path(case_dir)
    return {
        "case_id": case_dir.name,
        "repo": load_repo_pointer(case_dir),
        "diff_path": case_dir / "pr.diff",
        "ground_truth": load_ground_truth(case_dir),
    }
