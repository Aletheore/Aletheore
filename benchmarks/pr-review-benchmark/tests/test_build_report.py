from scripts.build_report import merge_grounding_into_scorecard, render_report_markdown


def test_merge_grounding_into_scorecard_attaches_rate_by_real_tool_name():
    scorecard = {"per_tool": {"Tool A": {"hit": 1, "miss": 0}}, "human_llm_agreement": {}}
    grounding_by_case_and_tool = {"001": {"aletheore": {"grounding_rate": 1.0}}}
    case_label_maps = {"001": {"Tool A": "aletheore"}}

    merged = merge_grounding_into_scorecard(scorecard, grounding_by_case_and_tool, case_label_maps)
    assert merged["per_tool"]["aletheore"]["grounding_rate"] == 1.0


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
