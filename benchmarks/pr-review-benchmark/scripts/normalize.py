"""Normalizes each tool's raw output into a common finding schema:
{"file": str|None, "line": int|None, "message": str, "severity": str|None}."""
import re

from aletheore.citation_verifier import extract_citations


def normalize_aletheore(raw_comments: list[dict]) -> list[dict]:
    """`raw_comments` is a list of GitHub PR-comment dicts (already filtered
    to aletheore[bot] by scripts/adapters.py's aletheore_adapter) posted by
    Aletheore's hosted Flash Review -- not a whole-repo CLI `audit` report."""
    findings = []
    for comment in raw_comments:
        body = comment.get("body", "")
        for paragraph in body.split("\n\n"):
            for citation in extract_citations(paragraph):
                findings.append({
                    "file": citation["file"],
                    "line": citation["line"],
                    "message": paragraph.strip(),
                    "severity": None,
                })
    return findings


# PR-Agent 0.39.0's `review` command (the one scripts/adapters.py invokes)
# does not print JSON to stdout and never emits a `code_suggestions` list
# (that shape belongs to PR-Agent's separate `improve` command, which this
# benchmark does not run). Instead it posts one markdown/HTML "PR Reviewer
# Guide" comment to the PR. Confirmed against a real PR-Agent run with a
# DeepSeek backend on 2026-07-26 (see
# https://github.com/ArihantK15/proctor-browser/pull/213 and pull/214).
#
# Each "Recommended focus areas for review" block is the closest per-location
# analog to other tools' findings; it links to a GitHub diff anchor
# (`#diff-<hash>R<start>-R<end>`) identifying a line range in the new file,
# but the visible comment text never names the file path itself.
_PR_AGENT_FOCUS_AREA_PATTERN = re.compile(
    r"<details><summary><a href='[^']*#diff-[0-9a-f]+R(\d+)(?:-R(\d+))?'>"
    r"<strong>(.*?)</strong></a>\s*\n\n(.*?)\n</summary>",
    re.DOTALL,
)


def normalize_pr_agent(raw: dict) -> list[dict]:
    """`raw` is {"comment_body": <posted comment text>, "changed_files":
    [<PR's changed file paths>]}. File attribution falls back to the single
    changed file when there's exactly one (true for every case in this
    corpus) and to None (unverifiable) otherwise, rather than guessing which
    changed file a diff-hash anchor refers to."""
    comment_body = raw.get("comment_body", "")
    changed_files = raw.get("changed_files", [])
    file = changed_files[0] if len(changed_files) == 1 else None

    findings = []
    for match in _PR_AGENT_FOCUS_AREA_PATTERN.finditer(comment_body):
        start, end, title, message = match.groups()
        findings.append({
            "file": file,
            "line": int(end or start),
            "message": message.strip(),
            "severity": title.strip(),
        })
    return findings


# DeepSource's GitHub App posts findings as ordinary GitHub PR *review*
# comments (path/line/body), not via a separate run_id-keyed issues API
# returning {"issues": [...]}. Confirmed against a real DeepSource run on
# the scratch repo on 2026-07-26: comments arrive through
# `GET /repos/.../pulls/<n>/comments`, distinguished by `user.login`. The
# finding title and severity are embedded inside the HTML comment body
# rather than being separate JSON fields.
_DEEPSOURCE_SEVERITY_PATTERN = re.compile(r"severity_indicator_(\w+)\.svg")
_DEEPSOURCE_TITLE_PATTERN = re.compile(r"<h3>.*?</picture>(.*?)</h3>", re.DOTALL)


def normalize_deepsource(raw_comments: list[dict]) -> list[dict]:
    """`raw_comments` is a list of GitHub PR-review-comment dicts (path/line/
    body) authored by DeepSource's GitHub App -- see module docstring above
    normalize_deepsource for why this differs from the original run_id/issues
    API assumption."""
    findings = []
    for comment in raw_comments:
        body = comment.get("body", "")
        severity_match = _DEEPSOURCE_SEVERITY_PATTERN.search(body)
        title_match = _DEEPSOURCE_TITLE_PATTERN.search(body)
        findings.append({
            "file": comment.get("path"),
            "line": comment.get("line") or comment.get("original_line"),
            "message": title_match.group(1).strip() if title_match else body.strip(),
            "severity": severity_match.group(1) if severity_match else None,
        })
    return findings
