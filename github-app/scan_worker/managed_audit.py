import json
import logging
from pathlib import Path
from typing import Callable

import aletheore.cli as _aletheore_cli
from aletheore.citation_verifier import citation_verification_section as _citation_verification_section
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
