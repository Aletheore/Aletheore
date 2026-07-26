"""Merges the automated grounding check (Task 4, keyed by real tool name)
into the blind-scored scorecard (Task 8, already de-anonymized and keyed
by real tool name thanks to Task 8's internal de-anonymization) after
grounding results are loaded, and renders the final report table."""


def merge_grounding_into_scorecard(scorecard: dict, grounding_by_case_and_tool: dict) -> dict:
    grounding_rates_by_tool = {}
    for case_id, grounding_by_tool in grounding_by_case_and_tool.items():
        for tool, grounding in grounding_by_tool.items():
            rate = grounding.get("grounding_rate")
            if rate is not None:
                grounding_rates_by_tool.setdefault(tool, []).append(rate)

    per_tool = {}
    for tool, stats in scorecard["per_tool"].items():
        merged_stats = dict(stats)
        rates = grounding_rates_by_tool.get(tool)
        if rates:
            merged_stats["grounding_rate"] = sum(rates) / len(rates)
        per_tool[tool] = merged_stats

    return {"per_tool": per_tool, "human_llm_agreement": scorecard["human_llm_agreement"]}


def render_report_markdown(scorecard: dict) -> str:
    lines = [
        "# Aletheore PR-Review Benchmark — Results",
        "",
        "| Tool | Hit | Partial | Miss | False Positives | Avg Actionability | Grounding Rate |",
        "|---|---|---|---|---|---|---|",
    ]
    for tool, stats in scorecard["per_tool"].items():
        avg_actionability = (
            stats["actionability_total"] / stats["actionability_count"]
            if stats.get("actionability_count") else "n/a"
        )
        grounding_rate = stats.get("grounding_rate", "n/a")
        lines.append(
            f"| {tool} | {stats.get('hit', 0)} | {stats.get('partial', 0)} | "
            f"{stats.get('miss', 0)} | {stats.get('false_positive_count', 0)} | "
            f"{avg_actionability} | {grounding_rate} |"
        )

    lines += [
        "",
        "## Human/LLM judge agreement",
        "",
        f"- Recall agreement: {scorecard['human_llm_agreement'].get('recall', 'n/a')}",
        f"- Actionability agreement: {scorecard['human_llm_agreement'].get('actionability', 'n/a')}",
    ]
    return "\n".join(lines)
