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

import json
import logging
import re
from collections.abc import Callable
from pathlib import Path

from aletheore.evidence import is_evidence_version_compatible

logger = logging.getLogger("aletheore.citation_verifier")

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


def _extensionless_citation_pattern(known_paths: set[str]) -> re.Pattern | None:
    """Matches citations of real files that have no extension.

    _CITATION_PATTERN requires a dot-extension, so `Dockerfile:12`,
    `Makefile:8` and `Procfile:3` were never extracted at all - claims
    about them were not verified *and* not flagged, they simply did not
    exist as far as this module was concerned. Those files are genuinely
    scanned and genuinely discussed in audit reports, so that is a real
    blind spot rather than a theoretical one.

    Built from the scan's own inventory rather than from a guessed list of
    well-known filenames, so it can only ever match a path that really
    exists. That keeps the extraction as conservative as the original
    pattern: it will not start matching `http://host:8080` or prose like
    "step 3:12".
    """
    targets = [p for p in known_paths if "." not in p.rsplit("/", 1)[-1]]
    if not targets:
        return None
    # Longest first so a nested path wins over a bare filename suffix of it.
    alternation = "|".join(re.escape(p) for p in sorted(targets, key=len, reverse=True))
    return re.compile(rf"`?(?<![\w./-])({alternation}):(\d+)`?")


def extract_citations(report_text: str, known_paths: set[str] | None = None) -> list[dict]:
    """Pulls `file:line` citations out of report prose.

    `known_paths` is optional so this stays usable on its own, but without
    it, citations of extensionless files are invisible - pass the scan's
    file inventory to catch those too.
    """
    citations = []
    seen: set[tuple[str, int]] = set()

    patterns = [_CITATION_PATTERN]
    if known_paths:
        extensionless = _extensionless_citation_pattern(known_paths)
        if extensionless is not None:
            patterns.append(extensionless)

    for pattern in patterns:
        for match in pattern.finditer(report_text):
            file_path, line_str = match.groups()
            key = (file_path, int(line_str))
            if key in seen:
                continue
            seen.add(key)
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
    citations = extract_citations(report_text, known_paths)

    verified = []
    unverified = []
    line_bounds_checked = 0
    for citation in citations:
        if citation["file"] not in known_paths:
            unverified.append(citation)
            continue
        if citation["line"] < 1:
            # Only the upper bound was guarded before, so `app.py:0` passed
            # as verified. No file has a line 0; a citation naming one is
            # pointing at nothing whether or not the file is real.
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


def local_line_count_fetcher(repo_path: Path) -> Callable[[str], int | None]:
    """Real per-file line counts read straight off a checkout on disk.

    Works for any caller holding a real checkout (managed audit's clone,
    the local CLI's own repo, AIRview does not qualify - it has to fetch
    file contents back out of the GitHub API instead). Returns None for
    anything it can't read (missing file, path escape, unreadable bytes),
    which verify_citations treats as "skip the bounds check for this
    citation" rather than as a failure, so a read problem never
    manufactures a false "unverified".
    """
    root = repo_path.resolve()

    def _fetch(path: str) -> int | None:
        try:
            candidate = (root / path).resolve()
            if not candidate.is_relative_to(root) or not candidate.is_file():
                return None
            with candidate.open("rb") as handle:
                return sum(1 for _ in handle)
        except OSError:
            return None

    return _fetch


def load_verifiable_evidence(repo_path: Path) -> dict | None:
    """The repo's own air.json, but only when it actually carries the file
    inventory citation verification needs.

    A caller can hand this a placeholder evidence file with no
    `repository.modules` (e.g. run_managed_audit_api_job's caller-supplied
    TOON blob path) - verifying against that would mark every citation
    "unverified" and print an alarming, entirely wrong summary. Returning
    None here means "we cannot check this", reported honestly as
    unavailable rather than as failure.
    """
    try:
        evidence = json.loads((repo_path / ".aletheore" / "air.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(evidence, dict):
        return None
    if not is_evidence_version_compatible(evidence.get("aletheore_version")):
        return None
    if not evidence.get("repository", {}).get("modules"):
        return None
    return evidence


def citation_verification_section(report_text: str, repo_path: Path) -> str:
    """Reports how many of the report's own file:line citations actually
    check out against the deterministic evidence it was generated from.

    Deliberately annotates rather than deletes: silently dropping prose
    the user is relying on, on a citation heuristic, is the same
    all-or-nothing failure mode that made unverified findings invisible
    elsewhere in this codebase. The reader gets the full report and an
    honest statement of what held up.
    """
    evidence = load_verifiable_evidence(repo_path)
    if evidence is None:
        logger.info("citation verification unavailable (no file inventory in evidence)")
        return (
            "\n\n---\n\n## Citation Verification\n\n"
            "_Not available for this run: the evidence supplied for this audit doesn't "
            "include the file inventory needed to check citations against._\n"
        )

    result = verify_citations(report_text, evidence, fetch_line_count=local_line_count_fetcher(repo_path))
    total = result["total_citations"]
    verified = len(result["verified"])
    unverified = result["unverified"]

    logger.info(
        "citation verification: %d/%d verified, %d unverified",
        verified,
        total,
        len(unverified),
    )

    if total == 0:
        return (
            "\n\n---\n\n## Citation Verification\n\n"
            "_This report contains no `file:line` citations to verify._\n"
        )

    # State only the level actually reached. run_managed_audit_api_job's
    # dict-evidence path writes the evidence but no source files, so every
    # fetch_line_count call returns None there and nothing is bounds-checked
    # - claiming otherwise would be the same overclaim this whole section
    # exists to prevent.
    bounds_checked = result["line_bounds_checked"]
    if bounds_checked == total:
        checked_how = (
            "the file exists in the scanned repository, and the cited line is within that "
            "file's real length"
        )
    elif bounds_checked:
        checked_how = (
            "the file exists in the scanned repository; "
            f"{bounds_checked} of them could also be checked against the file's real length"
        )
    else:
        checked_how = (
            "the file exists in the scanned repository. Line numbers could not be "
            "bounds-checked in this run, so a citation naming a real file at a wrong line "
            "still counts as verified here"
        )

    lines = [
        "\n\n---\n\n## Citation Verification\n",
        f"_{verified} of {total} `file:line` citations in this report were checked against "
        f"the deterministic evidence it was generated from: {checked_how}._\n",
    ]
    if unverified:
        lines.append(
            f"\n**{len(unverified)} citation(s) could not be verified** — treat the "
            "claims attached to these as unconfirmed:\n"
        )
        lines.extend(f"- `{c['file']}:{c['line']}`" for c in unverified)
        lines.append("")
    return "\n".join(lines)
