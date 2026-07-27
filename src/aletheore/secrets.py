import json
import os
import re
import subprocess
from pathlib import Path

from aletheore.scanner.detect import IGNORED_DIRS

BINARY_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".svg",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
    ".mp4",
    ".mp3",
    ".wav",
    ".pyc",
    ".so",
    ".dylib",
    ".dll",
}

PLACEHOLDER_PATH_MARKERS = ("example", "test", "fixture", "mock")

# Each entry's third element is the regex group index holding the actual secret value to
# redact. Most patterns match the credential directly, so group 0 (the whole match) IS the
# value. generic_credential_assignment is different: it matches "KEYWORD=value" syntax, so
# group 0 includes the keyword name (useless as a preview) and - critically - its tail end
# overlaps the real value, meaning a naive redact(group(0)) leaks trailing characters of the
# actual secret. Group 2 isolates just the captured value.
SECRET_PATTERNS = [
    ("aws_access_key_id", re.compile(r"AKIA[0-9A-Z]{16}"), 0),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"), 0),
    ("stripe_key", re.compile(r"(sk|pk)_(live|test)_[A-Za-z0-9]{16,}"), 0),
    ("private_key_header", re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"), 0),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), 0),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_-]{35}"), 0),
    (
        "generic_credential_assignment",
        re.compile(r"(?i)\b(PASSWORD|SECRET|API_KEY)\s*[:=]\s*['\"]([A-Za-z0-9+/=_-]{16,})['\"]"),
        2,
    ),
]


def iter_all_files(repo_path: Path):
    # os.walk(followlinks=False) rather than Path.rglob("*") - a symlinked
    # directory anywhere in the tree (real case: a monorepo tool, or an
    # accidental symlink to something outside the checkout) would otherwise
    # have its contents walked and reported on as if they were part of this
    # repo. followlinks only stops descent into symlinked *directories* -
    # a symlinked file sitting directly in a real directory still needs its
    # own explicit is_symlink() check below.
    for dirpath, dirnames, filenames in os.walk(repo_path, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.is_symlink() or not path.is_file():
                continue
            if path.suffix in BINARY_EXTENSIONS:
                continue
            yield path


def _is_likely_placeholder(rel_path: str) -> bool:
    lower = rel_path.lower()
    return any(marker in lower for marker in PLACEHOLDER_PATH_MARKERS)


def _redact(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * 4}...{value[-4:]}"


def load_secrets_baseline(repo_path: Path) -> list[dict]:
    config_file = repo_path / ".aletheore.json"
    if not config_file.exists():
        return []
    try:
        data = json.loads(config_file.read_text(encoding="utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []

    accepted = data.get("accepted_secrets", [])
    if not isinstance(accepted, list):
        return []
    return [entry for entry in accepted if isinstance(entry, dict)]


def _baseline_keys(baseline: list[dict] | None) -> set[tuple]:
    # Identity is (path, pattern, match_preview) for both current and history findings - not
    # commit, even for history ones, since accepting a leaked-then-fixed secret is a judgment
    # about that specific value at that path, not about one particular commit that happens to
    # surface it.
    return {(entry.get("path"), entry.get("pattern"), entry.get("match_preview")) for entry in (baseline or [])}


def find_secrets(repo_path: Path, baseline: list[dict] | None = None) -> dict:
    findings: list[dict] = []
    scanned_files = 0
    accepted_keys = _baseline_keys(baseline)

    for path in iter_all_files(repo_path):
        scanned_files += 1
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        rel_path = path.relative_to(repo_path).as_posix()
        for line_no, line in enumerate(text.splitlines(), start=1):
            for pattern_name, pattern, value_group in SECRET_PATTERNS:
                match = pattern.search(line)
                if match:
                    match_preview = _redact(match.group(value_group))
                    findings.append(
                        {
                            "path": rel_path,
                            "line": line_no,
                            "pattern": pattern_name,
                            "match_preview": match_preview,
                            "likely_placeholder": _is_likely_placeholder(rel_path),
                            "accepted": (rel_path, pattern_name, match_preview) in accepted_keys,
                        }
                    )

    return {"scanned_files": scanned_files, "findings": findings}


def find_secrets_in_history(
    repo_path: Path, baseline: list[dict] | None = None, *, max_commits: int | None = None
) -> dict:
    # `git log -p` generates a full unified diff for every commit in range -
    # for a repo at torvalds/linux's scale (1.46M commits) that's over 2GB
    # of diff text and ~50 minutes of git's own time, confirmed by direct
    # measurement (1000 commits -> ~1.4MB / ~2s, extrapolated linearly).
    # Streaming avoids buffering that text, but a hosted PR scan can't
    # spend 50 minutes walking a repo's entire history on every run
    # regardless - max_commits bounds it to the most recent N commits,
    # same pattern as git_intel's depth_cap.
    accepted_keys = _baseline_keys(baseline)
    args = ["git", "log", "-p", "--format=COMMIT_START\x1f%H\x1f%ad", "--date=iso-strict"]
    if max_commits is not None:
        args += ["-n", str(max_commits)]
    process = subprocess.Popen(
        args,
        cwd=repo_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        errors="ignore",
    )

    findings: list[dict] = []
    scanned_commits: set[str] = set()
    current_commit: str | None = None
    current_commit_date: str | None = None
    current_file: str | None = None

    assert process.stdout is not None
    for raw_line in process.stdout:
        line = raw_line.rstrip("\n")
        if line.startswith("COMMIT_START\x1f"):
            parts = line.split("\x1f")
            current_commit = parts[1] if len(parts) > 1 else None
            current_commit_date = parts[2] if len(parts) > 2 else None
            if current_commit:
                scanned_commits.add(current_commit)
            continue
        if line.startswith("+++ b/"):
            current_file = line[len("+++ b/"):]
            continue
        if line.startswith("+++"):
            continue
        if not line.startswith("+"):
            continue

        content = line[1:]
        for pattern_name, pattern, value_group in SECRET_PATTERNS:
            match = pattern.search(content)
            if match:
                match_preview = _redact(match.group(value_group))
                findings.append(
                    {
                        "commit": current_commit,
                        "commit_date": current_commit_date,
                        "path": current_file,
                        "pattern": pattern_name,
                        "match_preview": match_preview,
                        "likely_placeholder": _is_likely_placeholder(current_file or ""),
                        "accepted": (current_file, pattern_name, match_preview) in accepted_keys,
                    }
                )

    process.stdout.close()
    process.wait()

    if process.returncode != 0:
        return {"history_scanned_commits": 0, "history_findings": []}

    return {"history_scanned_commits": len(scanned_commits), "history_findings": findings}
