"""Opens a case's benchmark PR on the scratch repo with a realistic,
small diff instead of dumping the case's whole repo tree as N brand-new
files.

Real bug found via a Sourcery/Greptile pilot run (2026-09-13): every
existing case PR was opened by copying the case's entire materialized
repo tree fresh into a new benchmark-sandbox/<case-id>/ subdirectory in
one commit against the scratch repo's default branch - git (and every
tool) sees that as 100+ brand-new files, not the real 1-file bug/fix
diff pr.diff actually represents. Confirmed on 4 existing case PRs
(103/131/150/213 changed files each), and it silently breaks three
different things: Greptile hard-caps review at 100 changed files and
skips entirely; Sourcery's own per-installation review-budget (diff
chars/7 days) degrades a diff this size into a content-free "Approved";
and normalize_pr_agent's file attribution explicitly falls back to None
whenever the PR's changed-file count isn't exactly 1, an assumption
every case under the old construction method violated.

Fixed by giving each case PR a real base branch of its own: a
`seed/<case-id>` branch holding the case's full tree at base_commit (the
"before" state - already-fixed code for a real_bug_fix case, clean code
for an injected_bug case), and a `fix/<case-id>` branch on top of it
holding base_commit + pr.diff applied (the state under test). The PR is
opened base=seed/<case-id>, head=fix/<case-id> - GitHub allows any
branch as a PR base, not just the repo default - so the diff every tool
actually sees is exactly pr.diff, regardless of category.
"""
import shutil
import subprocess
import sys
from pathlib import Path

from scripts.cases import load_repo_pointer
from scripts.fixtures import expand_placeholders_in_tree

BENCHMARK_SANDBOX_DIR = "benchmark-sandbox"


def _run(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed in {cwd}: {result.stderr}")
    return result


def prepare_seed_and_head_trees(repo_pointer: dict, diff_path: Path, workdir: Path) -> tuple[Path, Path]:
    """Materializes the case's two real states as standalone trees (no
    .git directory, just the files) - `seed` is the repo at base_commit
    exactly as it is, `head` is that same tree with pr.diff applied on
    top. Both get corpus placeholders expanded (see scripts/fixtures.py);
    expanding both, not just head, is deliberate - nothing guarantees a
    future case's placeholder lives in base_commit's own tree rather than
    being introduced by the diff.
    """
    Path(workdir).mkdir(parents=True, exist_ok=True)
    checkout_dir = Path(workdir) / "checkout"
    _run(["git", "clone", repo_pointer["repo_url"], str(checkout_dir)], cwd=Path(workdir))
    _run(["git", "checkout", repo_pointer["base_commit"]], cwd=checkout_dir)

    seed_dir = Path(workdir) / "seed"
    shutil.copytree(checkout_dir, seed_dir, ignore=shutil.ignore_patterns(".git"))
    expand_placeholders_in_tree(seed_dir)

    _run(["git", "apply", str(diff_path)], cwd=checkout_dir)
    head_dir = Path(workdir) / "head"
    shutil.copytree(checkout_dir, head_dir, ignore=shutil.ignore_patterns(".git"))
    expand_placeholders_in_tree(head_dir)

    return seed_dir, head_dir


def _replace_sandbox_dir(scratch_clone: Path, case_id: str, source_tree: Path) -> None:
    sandbox_dir = scratch_clone / BENCHMARK_SANDBOX_DIR / case_id
    if sandbox_dir.exists():
        shutil.rmtree(sandbox_dir)
    shutil.copytree(source_tree, sandbox_dir)


def open_case_pr(
    case_dir: Path,
    scratch_clone: Path,
    workdir: Path,
    default_branch: str = "main",
    push: bool = True,
    pr_creator=None,
) -> dict:
    """Opens (or re-bases) one case's benchmark PR on an already-cloned
    scratch repo, using the seed/fix branch-pair construction described
    in this module's own docstring.

    `pr_creator`, if given, is called as `pr_creator(case_id, base_branch,
    head_branch)` and its return value is passed through as this
    function's "pr" key - injected so callers can use `gh pr create` (or
    a test double) without this function shelling out to `gh` itself.
    `push` defaults to True; set False to build and commit both local
    branches without pushing (used by tests against a local bare-repo
    fixture where a real push has nowhere real to land).

    Returns {"case_id", "seed_branch", "fix_branch", "pr"} - `pr` is
    None when `pr_creator` wasn't given.
    """
    case_dir = Path(case_dir)
    case_id = case_dir.name
    repo_pointer = load_repo_pointer(case_dir)
    diff_path = case_dir / "pr.diff"

    seed_dir, head_dir = prepare_seed_and_head_trees(repo_pointer, diff_path, Path(workdir))

    seed_branch = f"seed/{case_id}"
    fix_branch = f"fix/{case_id}"

    _run(["git", "fetch", "origin", default_branch], cwd=scratch_clone)
    _run(["git", "checkout", "-B", seed_branch, f"origin/{default_branch}"], cwd=scratch_clone)
    _replace_sandbox_dir(scratch_clone, case_id, seed_dir)
    _run(["git", "add", BENCHMARK_SANDBOX_DIR], cwd=scratch_clone)
    # --allow-empty: a case whose base_commit tree is already present
    # (re-running this script for a case already seeded) must not fail
    # here just because nothing changed - the fix commit below is what
    # actually needs new content, this one only needs to exist as a
    # real base ref for the PR.
    _run(
        ["git", "commit", "--allow-empty", "-m", f"seed: {case_id} base tree (no diff)"],
        cwd=scratch_clone,
    )
    if push:
        _run(["git", "push", "-f", "origin", f"HEAD:{seed_branch}"], cwd=scratch_clone)

    _run(["git", "checkout", "-B", fix_branch, seed_branch], cwd=scratch_clone)
    _replace_sandbox_dir(scratch_clone, case_id, head_dir)
    _run(["git", "add", BENCHMARK_SANDBOX_DIR], cwd=scratch_clone)
    _run(["git", "commit", "--allow-empty", "-m", f"test case: {case_id}"], cwd=scratch_clone)
    if push:
        _run(["git", "push", "-f", "origin", f"HEAD:{fix_branch}"], cwd=scratch_clone)

    pr = pr_creator(case_id, seed_branch, fix_branch) if pr_creator else None
    return {"case_id": case_id, "seed_branch": seed_branch, "fix_branch": fix_branch, "pr": pr}


def gh_pr_creator(scratch_repo: str):
    """Real pr_creator: opens the PR via `gh pr create`, returning its URL."""

    def _create(case_id: str, base_branch: str, head_branch: str) -> str:
        result = subprocess.run(
            [
                "gh", "pr", "create", "--repo", scratch_repo,
                "--base", base_branch, "--head", head_branch,
                "--title", f"[benchmark] {case_id}",
                "--body", "Benchmark case from pr-review-benchmark; see ground_truth.md for the real issue.",
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"gh pr create failed: {result.stderr}")
        return result.stdout.strip()

    return _create


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: python -m scripts.open_case_pr <case_dir> <scratch_clone_dir>", file=sys.stderr)
        sys.exit(1)
    case_dir_arg, scratch_clone_arg = Path(sys.argv[1]), Path(sys.argv[2])
    scratch_repo_arg = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        cwd=scratch_clone_arg, capture_output=True, text=True, check=True,
    ).stdout.strip()
    workdir_arg = Path("/tmp/pr-review-benchmark/open_case_pr_work") / case_dir_arg.name
    workdir_arg.mkdir(parents=True, exist_ok=True)
    result = open_case_pr(
        case_dir_arg, scratch_clone_arg, workdir_arg, pr_creator=gh_pr_creator(scratch_repo_arg)
    )
    print(result["pr"])
