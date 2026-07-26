from scripts.build_report import merge_grounding_into_scorecard, render_report_markdown


def test_merge_grounding_into_scorecard_attaches_rate_by_real_tool_name():
    # Scorecard is already keyed by real tool names (de-anonymized by Task 8)
    scorecard = {"per_tool": {"aletheore": {"hit": 1, "miss": 0}}, "human_llm_agreement": {}}
    grounding_by_case_and_tool = {"001": {"aletheore": {"grounding_rate": 1.0}}}

    merged = merge_grounding_into_scorecard(scorecard, grounding_by_case_and_tool)
    assert merged["per_tool"]["aletheore"]["grounding_rate"] == 1.0


def test_merge_grounding_ignores_none_rates_and_averages_real_rates():
    # Tools with zero findings return grounding_rate: None; should be filtered out
    scorecard = {"per_tool": {"aletheore": {"hit": 5, "miss": 0}}, "human_llm_agreement": {}}
    grounding_by_case_and_tool = {
        "001": {"aletheore": {"grounding_rate": None}},  # Clean PR, tool found nothing
        "002": {"aletheore": {"grounding_rate": 0.9}},   # Real rate from actual findings
        "003": {"aletheore": {"grounding_rate": 1.0}},   # Another case with rate
    }

    merged = merge_grounding_into_scorecard(scorecard, grounding_by_case_and_tool)
    # Should average only the real values: (0.9 + 1.0) / 2 = 0.95
    assert merged["per_tool"]["aletheore"]["grounding_rate"] == 0.95


def test_merge_grounding_handles_all_none_rates():
    # Tool has no findings in any case (all None) - should not add grounding_rate key
    scorecard = {"per_tool": {"aletheore": {"hit": 0, "miss": 0}}, "human_llm_agreement": {}}
    grounding_by_case_and_tool = {
        "001": {"aletheore": {"grounding_rate": None}},
        "002": {"aletheore": {"grounding_rate": None}},
    }

    merged = merge_grounding_into_scorecard(scorecard, grounding_by_case_and_tool)
    # No grounding_rate key should be added, render_report_markdown will use "n/a" fallback
    assert "grounding_rate" not in merged["per_tool"]["aletheore"]


def test_render_report_markdown_includes_per_tool_table():
    scorecard = {
        "per_tool": {
            "aletheore": {
                "hit": 10, "partial": 2, "miss": 3,
                "false_positive_count": 1,
                "actionability_total": 45, "actionability_count": 15,
                "grounding_rate": 0.97,
            }
        },
        "human_llm_agreement": {"recall": 0.9, "actionability": 0.8},
    }
    markdown = render_report_markdown(scorecard)
    assert "aletheore" in markdown
    assert "0.97" in markdown
    assert "0.9" in markdown
