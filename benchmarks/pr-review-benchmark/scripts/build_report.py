"""Merges the automated grounding check (Task 4, keyed by real tool name)
into the blind-scored scorecard (Task 8, already de-anonymized and keyed
by real tool name thanks to Task 8's internal de-anonymization) after
grounding results are loaded, and renders the final report table."""


_GROUNDING_KEYS = ("grounding_rate", "content_grounding_rate")


def merge_grounding_into_scorecard(scorecard: dict, grounding_by_case_and_tool: dict) -> dict:
    """Averages both grounding levels per tool - see check_citations.py for
    why reporting only the weaker one made the comparison misleading."""
    rates_by_tool: dict[str, dict[str, list[float]]] = {}
    for grounding_by_tool in grounding_by_case_and_tool.values():
        for tool, grounding in grounding_by_tool.items():
            for key in _GROUNDING_KEYS:
                rate = grounding.get(key)
                if rate is not None:
                    rates_by_tool.setdefault(tool, {}).setdefault(key, []).append(rate)

    per_tool = {}
    for tool, stats in scorecard["per_tool"].items():
        merged_stats = dict(stats)
        for key in _GROUNDING_KEYS:
            rates = rates_by_tool.get(tool, {}).get(key)
            if rates:
                merged_stats[key] = sum(rates) / len(rates)
        per_tool[tool] = merged_stats

    return {"per_tool": per_tool, "human_llm_agreement": scorecard["human_llm_agreement"]}


def render_report_markdown(scorecard: dict) -> str:
    lines = [
        "# Aletheore PR-Review Benchmark — Results",
        "",
        "| Tool | Hit | Partial | Miss | False Positives | Avg Actionability | "
        "Location Grounding | Content Grounding |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for tool, stats in scorecard["per_tool"].items():
        avg_actionability = (
            stats["actionability_total"] / stats["actionability_count"]
            if stats.get("actionability_count") else "n/a"
        )
        lines.append(
            f"| {tool} | {stats.get('hit', 0)} | {stats.get('partial', 0)} | "
            f"{stats.get('miss', 0)} | {stats.get('false_positive_count', 0)} | "
            f"{avg_actionability} | {stats.get('grounding_rate', 'n/a')} | "
            f"{stats.get('content_grounding_rate', 'n/a')} |"
        )

    lines += [
        "",
        "**Location grounding** — the cited file exists and the cited line is inside it. "
        "A static analyser reporting its own AST positions clears this by construction, so "
        "a rate of 1.0 here is close to uninformative.",
        "",
        "**Content grounding** — text the finding quotes verbatim really appears near the "
        "line it cites. This is the bar Aletheore's Flash Review enforces on itself in "
        "production, applied identically to every tool here. Findings that quote nothing "
        "verbatim cannot be scored at this level and are excluded from its denominator "
        "rather than counted as passes or failures.",
        "",
        "## Human/LLM judge agreement",
        "",
        f"- Recall agreement: {scorecard['human_llm_agreement'].get('recall', 'n/a')}",
        f"- Actionability agreement: {scorecard['human_llm_agreement'].get('actionability', 'n/a')}",
    ]
    return "\n".join(lines)
