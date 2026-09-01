import hashlib
import math
import os
import re
import subprocess
import threading
import zlib
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

# Exact, publicly documented example/test values a vendor ships in its own
# docs, common enough in real repos (copy-pasted into READMEs, tutorials,
# Stack Overflow answers) that they're worth recognizing directly rather
# than fitting a general pattern to them. High-entropy and non-repeating,
# so neither PLACEHOLDER_VALUE_MARKERS nor _value_looks_synthetically_repeated
# catches them - listed here, not guessed, each one lifted verbatim from the
# vendor's own current documentation.
KNOWN_VENDOR_EXAMPLE_VALUES = frozenset(
    {
        "sk_test_4eC39HqLyjWDarjtT1zdp7dc",  # Stripe's own API docs example key
    }
)

# Below this, Shannon entropy indicates a short or narrow-alphabet value
# (repeated/sequential characters, e.g. "aaaaaaaa" or "12345678") rather
# than the effectively-random output of a real credential generator - real
# secrets measured well above this bar.
_LOW_ENTROPY_THRESHOLD = 3.0

# zlib-compressed length as a fraction of the raw value's length. A value
# built from a repeated unit (a student hand-typing or padding out a fake
# example, e.g. "abcdefghij1234567890" doubled) compresses well below 1.0;
# genuinely random secrets don't compress at all past zlib's own per-value
# overhead. Measured empirically against 1,000 real random secrets across
# the length range these patterns actually match (16-44 chars): worst
# (lowest, so closest to a false positive) observed ratio was 1.18. This
# threshold leaves real margin on both sides of that measurement.
_SYNTHETIC_REPETITION_RATIO_THRESHOLD = 1.1

# Each entry's third element is the regex group index holding the actual secret value to
# redact. Most patterns match the credential directly, so group 0 (the whole match) IS the
# value. generic_credential_assignment is different: it matches "KEYWORD=value" syntax, so
# group 0 includes the keyword name (useless as a preview) and - critically - its tail end
# overlaps the real value, meaning a naive redact(group(0)) leaks trailing characters of the
# actual secret. Group 2 isolates just the captured value.
SECRET_PATTERNS = [
    ("aws_access_key_id", re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}"), 0),
    ("github_token", re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{20,})"), 0),
    ("stripe_key", re.compile(r"(sk|pk)_(live|test)_[A-Za-z0-9]{16,}"), 0),
    ("private_key_header", re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"), 0),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), 0),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_-]{35}"), 0),
    (
        "generic_credential_assignment",
        re.compile(
            # The value class includes "." for key formats that embed one (e.g. newer
            # Google AI Studio keys: "AQ.Ab8R..." rather than the older AIza-prefixed
            # google_api_key shape above) - without it, GEMINI_API_KEY=AQ.Ab8R... matched
            # only the 2 characters before the dot, fell under the 16-char minimum, and
            # the whole credential silently went undetected rather than just unredacted.
            #
            # The left-boundary class includes "." too, alongside the pre-existing "_"
            # and "-", so a dotted attribute assignment (self.PASSWORD=..., cfg.API_KEY=...
            # - one of the most common hardcoded-credential shapes in object-oriented
            # code) isn't silently invisible to this pattern the way a bare "." boundary
            # was. MYPASSWORD= (no separator at all) still correctly does not match.
            #
            # The left-boundary class also includes '"' and "'", and an optional quote
            # is now consumed right after the keyword too (['\"]? before \s*[:=]) - without
            # both, a quoted-key credential ("API_KEY": "...", 'password': '...') was
            # completely invisible: the keyword's own closing quote sat between it and
            # the ':', which \s*[:=]\s* alone can't skip over. This is the single most
            # common real shape a hardcoded secret takes in JSON/YAML/dict-literal config
            # (docker-compose environment blocks, terraform.tfvars, settings.json, a
            # Python/JS dict literal) - confirmed as a real, silent false negative by
            # direct testing, not hypothetical: '{"API_KEY": "sk-..."}' never matched.
            #
            # The right-boundary class includes "}" and "]" alongside the pre-existing
            # whitespace/end-of-line/",#;)" set, for the same reason: a quoted value that
            # closes a JSON object or array (the overwhelmingly common case - the key is
            # rarely the last thing on the line followed by nothing) was invisible too,
            # since neither character was in the original lookahead's boundary set.
            #
            # An optional "]" is now also consumed right after the keyword's closing
            # quote - bracket-subscript key assignment (os.environ["API_KEY"] = "...",
            # config["SECRET"] = "...", JS process.env['API_KEY'] = '...') is an equally
            # common real shape as the quoted dict-key case just above, and was just as
            # invisible: the keyword's quote is followed by "]" before the "=", which
            # \s*[:=]\s* alone can't skip over either. Confirmed as a real, silent false
            # negative by direct testing: 'os.environ["API_KEY"] = "sk-..."' never
            # matched, the same failure shape the quoted-key fix above already covers
            # for a plain dict literal.
            r"(?i)(?:^|[\s_.'\"-])(PASSWORD|SECRET|API_KEY)['\"]?\]?\s*[:=]\s*"
            r"['\"]?([A-Za-z0-9+/=_.-]{16,})['\"]?(?=\s|$|[,#;)}\]])"
        ),
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


def _value_names_itself_a_placeholder(value: str) -> bool:
    lower = value.lower()
    return any(marker in lower for marker in PLACEHOLDER_VALUE_MARKERS)


def _value_looks_synthetically_repeated(value: str) -> bool:
    # See _SYNTHETIC_REPETITION_RATIO_THRESHOLD for the empirical basis of
    # the cutoff. Real credential generators don't emit repeated
    # substrings; a hand-typed or padded-out fake example often does.
    if not value:
        return False
    compressed_length = len(zlib.compress(value.encode("utf-8"), level=9))
    return (compressed_length / len(value)) < _SYNTHETIC_REPETITION_RATIO_THRESHOLD


def _is_likely_placeholder(rel_path: str, value: str) -> bool:
    # A value that names itself a placeholder (AWS's own docs example key
    # literally spells "EXAMPLE"; Django's docs use "changeme", etc.) is an
    # unambiguous signal on its own - no real credential generator emits
    # those words, so this holds regardless of where the file lives. A
    # student README pasting AWS's setup-docs example key is at least as
    # common as pasting it into a file whose path happens to say
    # "test"/"fixture", and the earlier path-gated version treated the
    # exact same value differently depending on which one it landed in.
    if _value_names_itself_a_placeholder(value):
        return True

    # Same reasoning for a value built from an obviously repeated unit
    # (see _value_looks_synthetically_repeated) or an exact match to a
    # vendor's own published example value (see KNOWN_VENDOR_EXAMPLE_VALUES)
    # - both are unambiguous regardless of path, for the same reason a
    # marker word is.
    if _value_looks_synthetically_repeated(value) or value in KNOWN_VENDOR_EXAMPLE_VALUES:
        return True

    # Low entropy alone is a much weaker signal (a short, low-entropy value
    # can still be someone's genuinely weak real password) - path alone used
    # to be sufficient for this case too, and a real secret living at a path
    # containing "test"/"fixture"/"mock"/"example" (a plausible place to
    # accidentally commit one) was silently downgraded regardless of
    # whether the value itself looked remotely like a placeholder. So this
    # part stays gated: the path only qualifies a finding for the entropy
    # check rather than deciding it outright.
    path_suggests_placeholder = any(marker in rel_path.lower() for marker in PLACEHOLDER_PATH_MARKERS)
    if not path_suggests_placeholder:
        return False
    return _shannon_entropy(value) < _LOW_ENTROPY_THRESHOLD


def _redact(value: str, salt: str) -> str:
    # Salted with the finding's own (path, pattern) rather than a fixed or
    # global salt - two identical secret values at different locations (or
    # in different repos) hash differently, so match_preview can't be used
    # as a lookup key to correlate/deanonymize a value across scan output.
    # This is a display truncation, not a real cryptographic protection
    # (whoever owns the repo can always read the raw value straight from
    # path:line); the point is only that a match_preview pasted into a PR
    # comment or dashboard, or scraped from either, no longer hands out 8
    # real characters of the secret the way the old first4...last4 format
    # did.
    digest = hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()[:12]
    return f"sha256:{digest}"


def _legacy_redact(value: str) -> str:
    # The pre-hash preview format (first 4 + last 4 raw characters) - kept
    # only so an accepted_secrets baseline entry written before this fix
    # still matches on the next scan. A finding's live value is re-derived
    # fresh from the file every scan, so this can be recomputed and checked
    # alongside the new format without needing to store or migrate
    # anything; there's no way to derive it FROM an old match_preview
    # (only 8 of the value's characters ever left the scan), so baseline
    # entries can't be rewritten automatically - they keep working via this
    # dual check indefinitely, and naturally end up in the new format
    # whenever someone re-baselines from current scan output.
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


def _is_accepted(accepted_keys: set[tuple], path: str | None, pattern_name: str, value: str) -> bool:
    salt = f"{path}:{pattern_name}"
    if (path, pattern_name, _redact(value, salt)) in accepted_keys:
        return True
    return (path, pattern_name, _legacy_redact(value)) in accepted_keys


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
                for match in pattern.finditer(line):
                    value = match.group(value_group)
                    match_preview = _redact(value, f"{rel_path}:{pattern_name}")
                    findings.append(
                        {
                            "path": rel_path,
                            "line": line_no,
                            "pattern": pattern_name,
                            "match_preview": match_preview,
                            "likely_placeholder": _is_likely_placeholder(rel_path, value),
                            "accepted": _is_accepted(accepted_keys, rel_path, pattern_name, value),
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
                for match in pattern.finditer(content):
                    value = match.group(value_group)
                    match_preview = _redact(value, f"{current_file}:{pattern_name}")
                    findings.append(
                        {
                            "commit": current_commit,
                            "commit_date": current_commit_date,
                            "path": current_file,
                            "pattern": pattern_name,
                            "match_preview": match_preview,
                            "likely_placeholder": _is_likely_placeholder(current_file or "", value),
                            "accepted": _is_accepted(accepted_keys, current_file, pattern_name, value),
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
