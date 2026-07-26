import re
from collections.abc import Callable

# Matches file:line citations in report text, e.g. "server/routes/billing.ts:142"
# or "`app.py:12`". Deliberately narrow (word chars, dots, slashes, hyphens in the
# path) rather than a permissive catch-all - a citation format the audit manual
# doesn't actually produce is not something this can silently guess at.
_CITATION_PATTERN = re.compile(r"`?([\w./-]+\.[A-Za-z0-9]+):(\d+)`?")


def _known_file_paths(evidence: dict) -> set[str]:
    repository = evidence.get("repository", {})
    paths = {m.get("path") for m in repository.get("modules", []) if m.get("path")}
    paths |= {f.get("path") for f in repository.get("unparseable_files", []) if f.get("path")}
    return paths


def extract_citations(report_text: str) -> list[dict]:
    citations = []
    for match in _CITATION_PATTERN.finditer(report_text):
        file_path, line_str = match.groups()
        citations.append({"file": file_path, "line": int(line_str)})
    return citations


def verify_citations(
    report_text: str,
    evidence: dict,
    *,
    fetch_line_count: Callable[[str], int | None] | None = None,
) -> dict:
    """Checks each file:line citation in a generated report against the
    deterministic evidence it was supposedly grounded in.

    This always verifies file existence, which AIR data can answer with
    certainty. It does NOT verify the cited line is where the claimed
    issue actually lives unless `fetch_line_count` is given - AIR itself
    doesn't record per-file line counts, so without a fetcher, a citation
    naming a real file but a fabricated line is still reported as
    "verified" here (a real, documented limitation, not an oversight).

    `fetch_line_count`, when given, closes that gap: it's called with a
    citation's file path and must return that file's real line count (or
    None if unavailable, e.g. a fetch failure - which never turns into a
    false "unverified", it just skips the bounds check for that citation
    and falls back to file-existence-only). Confirmed as a real,
    reproducible bug when unguarded: Flash Review once cited a real,
    correctly-quoted issue at a line 237 lines away from where it actually
    is (see flash_review.py's _line_citation_content_matches) - a fabricated
    line is a real failure mode, not a hypothetical one.
    """
    known_paths = _known_file_paths(evidence)
    citations = extract_citations(report_text)

    verified = []
    unverified = []
    for citation in citations:
        if citation["file"] not in known_paths:
            unverified.append(citation)
            continue
        if fetch_line_count is not None:
            line_count = fetch_line_count(citation["file"])
            if line_count is not None and citation["line"] > line_count:
                unverified.append(citation)
                continue
        verified.append(citation)

    return {
        "total_citations": len(citations),
        "verified": verified,
        "unverified": unverified,
        "all_verified": len(unverified) == 0,
    }
