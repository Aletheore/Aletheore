"""Clone the pinned Zod commit (fixture.json) and build a real Aletheore
index over it via the actual `aletheore` CLI - the same scan+index path a
real user runs, so a regression in scanner/graph.py is exercised here too,
not just search_index.py.

Idempotent: skips straight to done if the workspace already has the pinned
commit checked out and an index already built, so a warm GitHub Actions
cache (keyed on fixture.json's commit) makes a repeat run near-instant
instead of re-cloning and re-embedding on every run.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = json.loads((HERE / "fixture.json").read_text())


def _workspace() -> Path:
    root = Path(os.environ.get("BENCH_CI_WORKSPACE", HERE / ".fixture-cache"))
    return root / FIXTURE["name"]


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    print(f"+ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def _current_commit(repo_dir: Path) -> str | None:
    if not (repo_dir / ".git").exists():
        return None
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else None


def main() -> int:
    workspace = _workspace()
    pinned = FIXTURE["commit"]

    if _current_commit(workspace) != pinned:
        # Fresh checkout or the wrong commit - start clean rather than try
        # to reconcile an unknown prior state (a half-finished clone from a
        # cancelled run, an out-of-date pin, etc).
        if workspace.exists():
            shutil.rmtree(workspace)
        workspace.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", "--quiet", FIXTURE["url"], str(workspace)])
        _run(["git", "checkout", "--quiet", pinned], cwd=workspace)

    index_path = workspace / ".aletheore" / "index.lancedb"
    if index_path.exists():
        print(f"index already built at {index_path}, skipping scan+index")
        return 0

    # Only the checks retrieval quality actually depends on - vulnerability/
    # secrets/license/endpoint scanning are real, useful work this gate has
    # no reason to pay for (some hit the network, e.g. OSV.dev).
    _run([
        "aletheore", "scan", str(workspace),
        "--no-check-vulnerabilities",
        "--no-scan-git-history",
        "--no-check-licenses",
        "--no-map-endpoints",
    ])
    _run(["aletheore", "index", str(workspace)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
