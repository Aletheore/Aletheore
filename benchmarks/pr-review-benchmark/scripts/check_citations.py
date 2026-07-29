"""Grounding checks for benchmark findings, at the two levels that
actually distinguish the tools under comparison.

**Location grounding** (`grounding_rate`): the cited file exists in the
checkout and the cited line is inside it. This is a low bar - a static
analyser that reports its own AST positions clears it by construction, so
a rate of 1.0 here says almost nothing about whether a finding's claim is
really anchored to what it describes.

**Content grounding** (`content_grounding_rate`): text the finding quotes
verbatim actually appears near the line it cites. This is the bar
Aletheore's Flash Review holds *itself* to in production
(github-app/scan_worker/flash_review.py's _line_citation_content_matches
drops any finding that fails it), and it is the thing its grounding claim
is actually about.

Measuring only the first level made the benchmark structurally unable to
show that difference, and worse than neutral: competitors got full
grounding credit for clearing a bar Aletheore voluntarily exceeds, while
Aletheore's stricter internal check removed findings and depressed its
recall. Both levels are now reported for every tool, scored identically,
so the comparison is symmetric.

Findings that quote nothing verbatim can't be content-checked at all;
those are counted separately as uncheckable rather than being silently
scored as either passes or failures, since counting them either way would
misstate a tool's grounding.

NOTE: the content rule below intentionally mirrors flash_review.py's
(quotes of 8+ characters, +/-8 line window). It is duplicated rather than
imported because that logic currently lives inside github-app, which this
harness does not import. If it is ever extracted into the aletheore
package, this should import it instead - a benchmark that measures a
*drifted* copy of the production rule would recreate the exact problem
this module was rewritten to fix.
"""
import re
from pathlib import Path

_QUOTED_STRING_RE = re.compile(r"'([^'\n]{8,})'|\"([^\"\n]{8,})\"")
LINE_CITATION_CONTEXT_WINDOW = 8


def _quoted_strings(text: str) -> list[str]:
    matches = []
    for match in _QUOTED_STRING_RE.finditer(text or ""):
        matches.append(match.group(1) if match.group(1) is not None else match.group(2))
    return matches


def _resolved_file(checkout_dir: Path, file_path: str | None) -> Path | None:
    if not file_path:
        return None
    full_path = (checkout_dir / file_path).resolve()
    if not full_path.is_relative_to(checkout_dir) or not full_path.is_file():
        return None
    return full_path


def _content_matches_cited_line(finding: dict, full_path: Path) -> bool | None:
    """True/False when the finding quotes something checkable, None when it
    quotes nothing and so cannot be judged at this level.

    Only the finding's own message is checked, never a suggested fix: a
    suggestion is the code the tool wants written, so looking for it in the
    code as it stands today can only ever fail. That exact confusion was a
    real production bug in Flash Review, which silently discarded correct
    findings because of it.
    """
    quoted = _quoted_strings(finding.get("message"))
    if not quoted:
        return None
    line = finding.get("line")
    if line is None:
        return None
    lines = full_path.read_text(errors="replace").splitlines()
    start = max(0, line - 1 - LINE_CITATION_CONTEXT_WINDOW)
    end = min(len(lines), line + LINE_CITATION_CONTEXT_WINDOW)
    window = "\n".join(lines[start:end])
    return any(q in window for q in quoted)


def verify_findings_against_checkout(findings: list[dict], checkout_dir: Path) -> dict:
    verified = []
    unverified = []
    content_verified = []
    content_unverified = []
    content_uncheckable = []
    checkout_dir = Path(checkout_dir).resolve()

    for finding in findings:
        full_path = _resolved_file(checkout_dir, finding.get("file"))
        if full_path is None:
            unverified.append(finding)
            continue
        line = finding.get("line")
        if line is not None:
            line_count = sum(1 for _ in full_path.open())
            if line < 1 or line > line_count:
                unverified.append(finding)
                continue
        verified.append(finding)

        # Content grounding is only meaningful for a finding that already
        # points at a real place; one that fails above has nothing to check
        # its quotes against.
        matched = _content_matches_cited_line(finding, full_path)
        if matched is None:
            content_uncheckable.append(finding)
        elif matched:
            content_verified.append(finding)
        else:
            content_unverified.append(finding)

    checkable = len(content_verified) + len(content_unverified)
    return {
        "total_findings": len(findings),
        "verified": verified,
        "unverified": unverified,
        "grounding_rate": (len(verified) / len(findings)) if findings else None,
        "content_verified": content_verified,
        "content_unverified": content_unverified,
        "content_uncheckable": content_uncheckable,
        "content_grounding_rate": (len(content_verified) / checkable) if checkable else None,
    }
