import json
import logging
from pathlib import Path
from typing import Callable

import aletheore.cli as _aletheore_cli
from aletheore.citation_verifier import verify_citations
from aletheore.report import run_reasoning_phase
from scan_worker.model_tiers import writing_adapter_for_plan

logger = logging.getLogger("scan_worker.managed_audit")

LLM_SUGGESTION_SYSTEM_PROMPT = """You have just been given a completed, evidence-backed code audit report.
Based on it, provide your own broader assessment: a single overall quality rating out of 10 with a one-sentence
justification, followed by 3-5 concrete improvement suggestions a strong engineer might make after reading this
report - things beyond what the evidence-backed findings already state, or a different way to prioritize them.
Be specific and actionable, never vague ("consider best practices"). Respond with ONLY this JSON shape:
{"rating": <int 1-10>, "rating_justification": "<one sentence>", "suggestions": ["<suggestion 1>", ...]}
No markdown fences, no other text."""


def _llm_based_suggestion_section(
    report_text: str, plan: str, on_usage: Callable[[int, int], None] | None = None
) -> str | None:
    # Purely additive, and never allowed to break a real audit: the
    # evidence-backed report above this section is the product's core
    # promise ("no claim without evidence"), and this is the one place
    # that promise is deliberately relaxed - clearly labeled, never mixed
    # into the findings themselves. Any failure (bad JSON, missing key,
    # model outage) just means this section doesn't get appended.
    try:
        adapter = writing_adapter_for_plan(plan, on_usage=on_usage)
        raw = adapter.simple_completion(LLM_SUGGESTION_SYSTEM_PROMPT, report_text, cwd=".")
        parsed = json.loads(raw)
        rating = parsed.get("rating")
        justification = parsed.get("rating_justification") or ""
        suggestions = [s for s in parsed.get("suggestions", []) if isinstance(s, str) and s.strip()]
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "LLM-based suggestion section failed (%s); shipping the audit without it", type(exc).__name__
        )
        return None
    if not isinstance(rating, int) or not (1 <= rating <= 10) or not suggestions:
        return None

    lines = [
        "\n\n---\n\n## LLM Based Suggestion (Not Evidence Backed)\n",
        "_Everything above is Aletheore's deterministic, evidence-grounded audit. "
        "The section below is the model's own broader judgment, not tied to a specific "
        "citation - treat it as a second opinion, not a finding._\n",
        f"**Overall rating: {rating}/10** — {justification}\n",
        "**Suggestions:**",
    ]
    lines.extend(f"- {suggestion}" for suggestion in suggestions)
    return "\n".join(lines)


def _local_line_count_fetcher(repo_path: Path) -> Callable[[str], int | None]:
    """Real per-file line counts read straight off the checkout on disk.

    The managed audit runs against a real clone (see jobs.py's
    run_managed_audit_pr_job), so unlike AIRview - which has to fetch file
    contents back out of the GitHub API - this can bounds-check a cited
    line for free. Returns None for anything it can't read (missing file,
    path escape, unreadable bytes), which verify_citations treats as "skip
    the bounds check for this citation" rather than as a failure, so a
    read problem never manufactures a false "unverified".
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


def _load_verifiable_evidence(repo_path: Path) -> dict | None:
    """The audit's own air.json, but only when it actually carries the file
    inventory citation verification needs.

    run_managed_audit_api_job can hand this function a caller-supplied
    TOON blob and write a `{"managed_evidence": True}` placeholder as
    air.json (see jobs.py) - that placeholder has no `repository.modules`,
    so verifying against it would mark every single citation "unverified"
    and print an alarming, entirely wrong verification summary. Returning
    None here means "we cannot check this", which is reported honestly as
    unavailable rather than as failure.
    """
    try:
        evidence = json.loads((repo_path / ".aletheore" / "air.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(evidence, dict):
        return None
    if not evidence.get("repository", {}).get("modules"):
        return None
    return evidence


def _citation_verification_section(report_text: str, repo_path: Path) -> str:
    """Reports how many of the report's own file:line citations actually
    check out against the deterministic evidence it was generated from.

    This is appended to the report *before* it is signed (see jobs.py's
    _sign_and_persist_audit_report), so the Ed25519 signature covers the
    grounding result too - otherwise an "Audit Certificate" would attest
    only that Aletheore produced the text, saying nothing about whether
    its claims point at real places in the code.

    Deliberately annotates rather than deletes: silently dropping prose
    the customer paid for, on a citation heuristic, is the same
    all-or-nothing failure mode that made unverified findings invisible
    everywhere else in this codebase. The reader gets the full report and
    an honest statement of what held up.
    """
    evidence = _load_verifiable_evidence(repo_path)
    if evidence is None:
        logger.info("managed audit citation verification unavailable (no file inventory in evidence)")
        return (
            "\n\n---\n\n## Citation Verification\n\n"
            "_Not available for this run: the evidence supplied for this audit doesn't "
            "include the file inventory needed to check citations against._\n"
        )

    result = verify_citations(
        report_text, evidence, fetch_line_count=_local_line_count_fetcher(repo_path)
    )
    total = result["total_citations"]
    verified = len(result["verified"])
    unverified = result["unverified"]

    logger.info(
        "managed audit citation verification: %d/%d verified, %d unverified",
        verified,
        total,
        len(unverified),
    )

    if total == 0:
        return (
            "\n\n---\n\n## Citation Verification\n\n"
            "_This report contains no `file:line` citations to verify._\n"
        )

    lines = [
        "\n\n---\n\n## Citation Verification\n",
        f"_{verified} of {total} `file:line` citations in this report were checked against "
        "the deterministic evidence it was generated from: the file exists in the scanned "
        "repository, and the cited line is within that file's real length._\n",
    ]
    if unverified:
        lines.append(
            f"\n**{len(unverified)} citation(s) could not be verified** — treat the "
            "claims attached to these as unconfirmed:\n"
        )
        lines.extend(f"- `{c['file']}:{c['line']}`" for c in unverified)
        lines.append("")
    return "\n".join(lines)


def run_managed_audit(
    repo_path: Path,
    plan: str,
    manual_dir: str | None = None,
    on_usage: Callable[[int, int], None] | None = None,
) -> str:
    adapter = writing_adapter_for_plan(plan, on_usage=on_usage)
    report_path = run_reasoning_phase(
        adapter,
        str(repo_path),
        manual_dir or _aletheore_cli.MANUAL_DIR,
    )
    report_text = Path(report_path).read_text()

    # Verification runs against the evidence-backed report only, before the
    # explicitly-not-evidence-backed LLM suggestion section is appended -
    # that section is allowed to speak without citations, so counting it
    # here would be measuring the wrong thing.
    report_text += _citation_verification_section(report_text, repo_path)

    suggestion_section = _llm_based_suggestion_section(report_text, plan, on_usage=on_usage)
    if suggestion_section:
        report_text += suggestion_section
    return report_text
