import math
import os
import re
import subprocess
import threading
from collections import Counter
from pathlib import Path

from aletheore.repo_config import is_ignored, load_repo_config
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

# iter_all_files feeds full-file reads (find_secrets, mcp_server's
# aletheore_search) on the shared scan-worker, where every installation's
# scans compete for the same container's memory - a single unusually large
# committed file (a data dump, a generated fixture, a vendored bundle) read
# in full would risk OOMing that container for everyone. Skipped the same
# way BINARY_EXTENSIONS files are: silently excluded from the walk, not
# truncated (a truncated secrets scan could misreport line numbers).
MAX_SCANNED_FILE_BYTES = 10 * 1024 * 1024

PLACEHOLDER_PATH_MARKERS = ("example", "test", "fixture", "mock")

# Substrings that show up in hand-written placeholder values themselves
# (AWS's own documentation uses a well-known placeholder access key ending
# in "EXAMPLE", for instance), independent of where the file lives. Not
# spelled out literally here - this file's own aws_access_key_id pattern
# below would match it, and this path doesn't carry a PLACEHOLDER_PATH_MARKERS
# term, so it wouldn't get the placeholder benefit of the doubt.
PLACEHOLDER_VALUE_MARKERS = ("example", "xxxx", "changeme", "dummy", "placeholder", "sample", "fake", "yourkey")

# Below this, Shannon entropy indicates a short or narrow-alphabet value
# (repeated/sequential characters, e.g. "aaaaaaaa" or "12345678") rather
# than the effectively-random output of a real credential generator - real
# secrets measured well above this bar.
_LOW_ENTROPY_THRESHOLD = 3.0

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


def iter_all_files(repo_path: Path, ignored_paths: list[str] | None = None):
    # os.walk(followlinks=False) rather than Path.rglob("*") - a symlinked
    # directory anywhere in the tree (real case: a monorepo tool, or an
    # accidental symlink to something outside the checkout) would otherwise
    # have its contents walked and reported on as if they were part of this
    # repo. followlinks only stops descent into symlinked *directories* -
    # a symlinked file sitting directly in a real directory still needs its
    # own explicit is_symlink() check below.
    patterns = ignored_paths or []
    for dirpath, dirnames, filenames in os.walk(repo_path, followlinks=False):
        rel_dir = Path(dirpath).relative_to(repo_path).as_posix()
        dirnames[:] = [
            d
            for d in dirnames
            if d not in IGNORED_DIRS
            and not is_ignored(f"{rel_dir}/{d}" if rel_dir != "." else d, patterns)
        ]
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.is_symlink() or not path.is_file():
                continue
            if path.suffix in BINARY_EXTENSIONS:
                continue
            try:
                if path.stat().st_size > MAX_SCANNED_FILE_BYTES:
                    continue
            except OSError:
                continue
            rel_path = path.relative_to(repo_path).as_posix()
            if is_ignored(rel_path, patterns):
                continue
            yield path


def _shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _value_looks_like_a_placeholder(value: str) -> bool:
    lower = value.lower()
    if any(marker in lower for marker in PLACEHOLDER_VALUE_MARKERS):
        return True
    return _shannon_entropy(value) < _LOW_ENTROPY_THRESHOLD


def _is_likely_placeholder(rel_path: str, value: str) -> bool:
    # Path alone used to be sufficient - a real secret living at a path
    # containing "test"/"fixture"/"mock"/"example" (a plausible place to
    # accidentally commit one) was silently downgraded regardless of
    # whether the value itself looked remotely like a placeholder. Now the
    # path only qualifies a finding for a second, value-shape check rather
    # than deciding it outright.
    path_suggests_placeholder = any(marker in rel_path.lower() for marker in PLACEHOLDER_PATH_MARKERS)
    if not path_suggests_placeholder:
        return False
    return _value_looks_like_a_placeholder(value)


def _redact(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * 4}...{value[-4:]}"


def load_secrets_baseline(repo_path: Path) -> list[dict]:
    return load_repo_config(repo_path)["accepted_secrets"]


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
    ignored_paths = load_repo_config(repo_path)["ignored_paths"]

    for path in iter_all_files(repo_path, ignored_paths):
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
                    value = match.group(value_group)
                    match_preview = _redact(value)
                    findings.append(
                        {
                            "path": rel_path,
                            "line": line_no,
                            "pattern": pattern_name,
                            "match_preview": match_preview,
                            "likely_placeholder": _is_likely_placeholder(rel_path, value),
                            "accepted": (rel_path, pattern_name, match_preview) in accepted_keys,
                        }
                    )

    return {"scanned_files": scanned_files, "findings": findings}


DEFAULT_SECRETS_HISTORY_TIMEOUT_SECONDS = 300.0


def find_secrets_in_history(
    repo_path: Path,
    baseline: list[dict] | None = None,
    *,
    max_commits: int | None = None,
    timeout_seconds: float | None = DEFAULT_SECRETS_HISTORY_TIMEOUT_SECONDS,
) -> dict:
    # `git log -p` generates a full unified diff for every commit in range -
    # for a repo at torvalds/linux's scale (1.46M commits) that's over 2GB
    # of diff text and ~50 minutes of git's own time, confirmed by direct
    # measurement (1000 commits -> ~1.4MB / ~2s, extrapolated linearly).
    # Streaming avoids buffering that text, but a hosted PR scan can't
    # spend 50 minutes walking a repo's entire history on every run
    # regardless - max_commits bounds it to the most recent N commits,
    # same pattern as git_intel's depth_cap.
    #
    # max_commits is an explicit, known-cost bound. timeout_seconds guards
    # the other failure mode: git blocked on a slow read (e.g. blob reads
    # stalling on a network-backed filesystem) rather than genuinely
    # working through a large history - reproduced directly (a `git log -p`
    # that measured ~2s/1000 commits elsewhere took 7+ minutes at ~0% CPU
    # on a stalled checkout). Without a timeout that hangs this call, and
    # the CLI, forever with no feedback. A `threading.Timer` watchdog kills
    # the process on expiry; the read loop below then hits EOF naturally
    # and returns whatever was gathered before the stall, flagged as
    # incomplete rather than presented as a full scan.
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

    timed_out = threading.Event()
    watchdog: threading.Timer | None = None
    if timeout_seconds is not None:
        def _kill_on_timeout() -> None:
            timed_out.set()
            process.kill()

        watchdog = threading.Timer(timeout_seconds, _kill_on_timeout)
        watchdog.daemon = True
        watchdog.start()

    findings: list[dict] = []
    scanned_commits: set[str] = set()
    current_commit: str | None = None
    current_commit_date: str | None = None
    current_file: str | None = None

    try:
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
                    value = match.group(value_group)
                    match_preview = _redact(value)
                    findings.append(
                        {
                            "commit": current_commit,
                            "commit_date": current_commit_date,
                            "path": current_file,
                            "pattern": pattern_name,
                            "match_preview": match_preview,
                            "likely_placeholder": _is_likely_placeholder(current_file or "", value),
                            "accepted": (current_file, pattern_name, match_preview) in accepted_keys,
                        }
                    )
    finally:
        if watchdog is not None:
            watchdog.cancel()

    process.stdout.close()
    process.wait()

    if timed_out.is_set():
        return {
            "history_scanned_commits": len(scanned_commits),
            "history_findings": findings,
            "history_scan_timed_out": True,
        }

    if process.returncode != 0:
        return {"history_scanned_commits": 0, "history_findings": []}

    return {"history_scanned_commits": len(scanned_commits), "history_findings": findings}
