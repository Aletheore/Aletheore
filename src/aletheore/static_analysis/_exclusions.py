from pathlib import Path

from aletheore.scanner.detect import IGNORED_DIRS

# Every subprocess-based scanner in this package walks the filesystem
# itself - unlike the rest of Aletheore's checks, which build on the
# already-filtered module graph. Real bug found live wiring this up
# tonight: an unfiltered `bandit -r .` against this repo's own working
# tree returned 78,244 findings, 1,376 of them duplicates under
# .claude/worktrees/<agent-id>/ alone (a full nested checkout of this same
# repo - the exact scenario scanner/detect.py's own IGNORED_DIRS comment
# already documents for .claude). Neither ".worktrees" (a second, sibling
# directory this repo's EnterWorktree tool creates, confirmed present via
# `git status` as real untracked content) nor ".repowise" (Repowise's own
# local index/wiki cache - 51MB, confirmed live as the reason a full Bearer
# scan of this repo took over 10 minutes when the same scan scoped to just
# github-app/ took 18.7s) was in IGNORED_DIRS at the time this was written.
# ".repowise" has since been added to the shared IGNORED_DIRS constant
# (language detection/dead-code/secrets scanning get it for free now) -
# kept here too as a harmless, defense-in-depth duplicate rather than
# trusting a single list. ".worktrees" has no fixed name requirement for
# EnterWorktree's own directories and isn't a stable constant to add to
# IGNORED_DIRS the same way; it stays local to this module.
_EXTRA_EXCLUDED_DIRS = {".worktrees", ".repowise"}


def excluded_dir_names(repo_path: Path) -> list[str]:
    from aletheore.repo_config import load_repo_config

    config = load_repo_config(repo_path)
    # ignored_paths can carry glob/nested patterns (e.g. "docs/generated/**")
    # that don't correspond to a simple top-level directory name any of the
    # four scanners' native exclude flags understand - only the bare,
    # slash-free entries are usable here. The general case (arbitrary
    # ignored_paths patterns) is still enforced afterward by
    # filter_findings below, which is why a pattern falling out of this
    # list doesn't mean it's ignored.
    extra_bare = [p for p in config["ignored_paths"] if "/" not in p]
    return sorted(IGNORED_DIRS | _EXTRA_EXCLUDED_DIRS | set(extra_bare))


def count_real_files(repo_path: Path) -> int:
    """Shared by every scanner here that needs a repo-size-scaled timeout
    (see bearer_scanner.py's real calibration data, and semgrep_scanner.py/
    gosec_scanner.py's - both confirmed live by an independent benchmark
    run timing out at a flat 180s against a real monorepo-scale Go repo,
    the same "fast on a moderate real repo, not fast on a genuinely huge
    one" gap Bearer already had)."""
    excluded = set(excluded_dir_names(repo_path))
    return sum(
        1
        for path in repo_path.rglob("*")
        if path.is_file() and not excluded.intersection(path.relative_to(repo_path).parts)
    )


def has_real_file(repo_path: Path, pattern: str) -> bool:
    """Same rglob(pattern) every scanner's cheap pre-check already did, but
    excluded-dir-aware - real gap found live: an unfiltered rglob("*.go")
    against this repo matched real .go files only inside .claude/worktrees/
    (this repo has no Go code of its own outside that duplicated checkout),
    triggering a whole gosec subprocess invocation for zero real Go
    source. Harmless here (gosec's own -exclude-dir flags still produced a
    correct, empty result), but wasteful, and not guaranteed harmless on a
    much larger nested tree."""
    excluded = set(excluded_dir_names(repo_path))
    for match in repo_path.rglob(pattern):
        if not excluded.intersection(match.relative_to(repo_path).parts):
            return True
    return False


def file_census(repo_path: Path, pattern: str) -> tuple[bool, int]:
    """(has a real file matching pattern, total real file count) from a
    single walk - gosec and pmd each used to answer these as two separate
    questions (has_real_file then count_real_files for their scaled
    timeout), walking this same tree twice in one check_* call before
    even deciding whether to invoke their external tool."""
    excluded = set(excluded_dir_names(repo_path))
    has_match = False
    total = 0
    for path in repo_path.rglob("*"):
        if not path.is_file() or excluded.intersection(path.relative_to(repo_path).parts):
            continue
        total += 1
        if not has_match and path.match(pattern):
            has_match = True
    return has_match, total


def filter_findings(findings: list[dict], repo_path: Path) -> list[dict]:
    """Authoritative correctness backstop, independent of whether a given
    tool's own native exclude flag actually honored excluded_dir_names -
    every finding is re-checked against the same ignore rules regardless of
    which tool produced it or whether its own exclusion flag worked as
    expected."""
    from aletheore.repo_config import is_ignored, load_repo_config

    config = load_repo_config(repo_path)
    ignored_paths = config["ignored_paths"]
    excluded_names = excluded_dir_names(repo_path)

    def is_excluded(path: str) -> bool:
        parts = Path(path).parts
        if any(part in excluded_names for part in parts):
            return True
        return is_ignored(Path(path).as_posix(), ignored_paths)

    return [finding for finding in findings if not is_excluded(finding.get("path", ""))]
