import re
from pathlib import Path

from veridion.scanner.detect import IGNORED_DIRS

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

SECRET_PATTERNS = [
    ("aws_access_key_id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}")),
    ("stripe_key", re.compile(r"(sk|pk)_(live|test)_[A-Za-z0-9]{16,}")),
    ("private_key_header", re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("google_api_key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    (
        "generic_credential_assignment",
        re.compile(r"(?i)\b(PASSWORD|SECRET|API_KEY)\s*[:=]\s*['\"]([A-Za-z0-9+/=_-]{16,})['\"]"),
    ),
]


def _iter_all_files(repo_path: Path):
    for path in repo_path.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIRS for part in path.parts):
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


def find_secrets(repo_path: Path) -> dict:
    findings: list[dict] = []
    scanned_files = 0

    for path in _iter_all_files(repo_path):
        scanned_files += 1
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        rel_path = path.relative_to(repo_path).as_posix()
        for line_no, line in enumerate(text.splitlines(), start=1):
            for pattern_name, pattern in SECRET_PATTERNS:
                match = pattern.search(line)
                if match:
                    findings.append(
                        {
                            "path": rel_path,
                            "line": line_no,
                            "pattern": pattern_name,
                            "match_preview": _redact(match.group(0)),
                            "likely_placeholder": _is_likely_placeholder(rel_path),
                        }
                    )

    return {"scanned_files": scanned_files, "findings": findings}
