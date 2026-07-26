"""Normalizes each tool's raw output into a common finding schema:
{"file": str|None, "line": int|None, "message": str, "severity": str|None}."""
from aletheore.citation_verifier import extract_citations


def normalize_aletheore(report_text: str) -> list[dict]:
    findings = []
    for paragraph in report_text.split("\n\n"):
        for citation in extract_citations(paragraph):
            findings.append({
                "file": citation["file"],
                "line": citation["line"],
                "message": paragraph.strip(),
                "severity": None,
            })
    return findings


def normalize_pr_agent(raw: dict) -> list[dict]:
    return [
        {
            "file": suggestion.get("relevant_file"),
            "line": suggestion.get("relevant_line"),
            "message": suggestion.get("suggestion_content", ""),
            "severity": suggestion.get("label"),
        }
        for suggestion in raw.get("code_suggestions", [])
    ]


def normalize_deepsource(raw: dict) -> list[dict]:
    findings = []
    for issue in raw.get("issues", []):
        location = issue.get("location", {})
        findings.append({
            "file": location.get("path"),
            "line": location.get("position", {}).get("begin", {}).get("line"),
            "message": issue.get("title", ""),
            "severity": issue.get("severity"),
        })
    return findings


def normalize_coderabbit(raw_comments: list[dict]) -> list[dict]:
    return [
        {
            "file": comment.get("path"),
            "line": comment.get("line") or comment.get("original_line"),
            "message": comment.get("body", ""),
            "severity": None,
        }
        for comment in raw_comments
    ]
