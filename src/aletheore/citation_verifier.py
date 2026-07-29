"""Checks that generated prose points at places that really exist.

There are three levels of grounding in this codebase, in increasing
strength. Everything that reports a "verified" claim to a customer should
be explicit about which one it actually applied - reporting a level you
didn't reach is the same defect as not checking at all.

1. FILE EXISTS - the cited path is in the scan's file inventory. Always
   available, since AIR always lists the files it parsed. On its own this
   catches invented filenames and nothing else: a citation naming a real
   file at a completely fabricated line passes.
2. LINE IN BOUNDS - additionally, the cited line is within that file's
   real length. Needs a `fetch_line_count`, because AIR does not record
   per-file line counts. Callers that hold a real checkout (the managed
   audit) get this for free; callers that would have to re-fetch file
   contents over the network (AIRview) may not always have it. This
   result reports `line_bounds_checked` so a caller can state honestly
   how many citations actually reached this level.
3. CONTENT MATCHES - additionally, text the claim quotes verbatim appears
   near the cited line. Strongest, but only meaningful where the claim is
   structured enough to attach quotes to a single location, so it lives
   in flash_review.py's _line_citation_content_matches (per-finding,
   against a diff) rather than here (whole-document prose).

Level 3 is not a stricter version of this function that someone forgot to
write: it needs a different input shape. What matters is that each
surface says which level it reached.
"""

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
    line_bounds_checked = 0
    for citation in citations:
        if citation["file"] not in known_paths:
            unverified.append(citation)
            continue
        if fetch_line_count is not None:
            line_count = fetch_line_count(citation["file"])
            if line_count is not None:
                # Counted only when a real length came back: passing a
                # fetcher does not mean every file could be read, and a
                # caller that assumed it did would overstate how much of
                # its report was actually bounds-checked.
                line_bounds_checked += 1
                if citation["line"] > line_count:
                    unverified.append(citation)
                    continue
        verified.append(citation)

    return {
        "total_citations": len(citations),
        "verified": verified,
        "unverified": unverified,
        "all_verified": len(unverified) == 0,
        "line_bounds_checked": line_bounds_checked,
    }
