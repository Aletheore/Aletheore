import re

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


def verify_citations(report_text: str, evidence: dict) -> dict:
    """Checks each file:line citation in a generated report against the
    deterministic evidence it was supposedly grounded in.

    This verifies file existence, which AIR data can answer with certainty.
    It does not verify the cited line is where the claimed issue actually
    lives - AIR doesn't record per-file line counts, so a citation naming a
    real file but a fabricated line would still be reported as "verified"
    here. That is a real limitation, not an oversight: report an explicit
    unavailable rather than a false confidence, per this codebase's
    evidence-resolution rule of never inventing certainty it doesn't have.
    """
    known_paths = _known_file_paths(evidence)
    citations = extract_citations(report_text)

    verified = []
    unverified = []
    for citation in citations:
        if citation["file"] in known_paths:
            verified.append(citation)
        else:
            unverified.append(citation)

    return {
        "total_citations": len(citations),
        "verified": verified,
        "unverified": unverified,
        "all_verified": len(unverified) == 0,
    }
