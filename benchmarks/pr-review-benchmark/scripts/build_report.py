"""Merges the automated grounding check (Task 4, real tool names, no
blinding needed) into the blind-scored scorecard (Task 8, label-keyed)
after each case's mapping has been revealed, and renders the final
report table."""


def merge_grounding_into_scorecard(scorecard: dict, grounding_by_case_and_tool: dict, case_label_maps: dict) -> dict:
    real_name_by_label = {}
    for case_id, label_to_tool in case_label_maps.items():
        for label, tool in label_to_tool.items():
            real_name_by_label[label] = tool

    grounding_rates_by_tool = {}
    for case_id, grounding_by_tool in grounding_by_case_and_tool.items():
        for tool, grounding in grounding_by_tool.items():
            grounding_rates_by_tool.setdefault(tool, []).append(grounding["grounding_rate"])

    per_tool = {}
    for label, stats in scorecard["per_tool"].items():
        real_name = real_name_by_label.get(label, label)
        merged_stats = dict(stats)
        rates = grounding_rates_by_tool.get(real_name)
        if rates:
            merged_stats["grounding_rate"] = sum(rates) / len(rates)
        per_tool[real_name] = merged_stats

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
