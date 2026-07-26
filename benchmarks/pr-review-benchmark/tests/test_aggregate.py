import json
import yaml
from scripts.aggregate import load_manual_scores, load_llm_scores, build_scorecard


def test_load_manual_scores_reads_all_scored_yaml_files(tmp_path):
    scored_dir = tmp_path / "scored"
    scored_dir.mkdir()
    (scored_dir / "001.yaml").write_text(yaml.safe_dump({
        "case_id": "001",
        "scores": {"Tool A": {"recall": "hit", "false_positives": [], "actionability": 4}},
    }))
    scores = load_manual_scores(tmp_path)
    assert scores == {"001": {"Tool A": {"recall": "hit", "false_positives": [], "actionability": 4}}}


def test_load_llm_scores_reads_all_llm_judged_json_files(tmp_path):
    llm_dir = tmp_path / "llm_judged"
    llm_dir.mkdir()
    (llm_dir / "001.json").write_text(json.dumps({
        "Tool A": {"recall": "hit", "false_positives": [], "actionability": 4}
    }))
    scores = load_llm_scores(tmp_path)
    assert scores == {"001": {"Tool A": {"recall": "hit", "false_positives": [], "actionability": 4}}}


def test_build_scorecard_counts_recall_buckets_and_false_positives():
    manual_scores = {
        "001": {"Tool A": {"recall": "hit", "false_positives": [], "actionability": 5}},
        "002": {"Tool A": {"recall": "miss", "false_positives": ["noise"], "actionability": 2}},
    }
    scorecard = build_scorecard(manual_scores, llm_scores={})
    assert scorecard["per_tool"]["Tool A"]["hit"] == 1
    assert scorecard["per_tool"]["Tool A"]["miss"] == 1
    assert scorecard["per_tool"]["Tool A"]["false_positive_count"] == 1
    assert scorecard["per_tool"]["Tool A"]["actionability_total"] == 7
    assert scorecard["per_tool"]["Tool A"]["actionability_count"] == 2


def test_build_scorecard_computes_human_llm_agreement_rate():
    manual_scores = {
        "001": {"Tool A": {"recall": "hit", "false_positives": [], "actionability": 4}},
        "002": {"Tool A": {"recall": "miss", "false_positives": [], "actionability": 2}},
    }
    llm_scores = {
        "001": {"Tool A": {"recall": "hit", "actionability": 4}},
        "002": {"Tool A": {"recall": "hit", "actionability": 2}},
    }
    scorecard = build_scorecard(manual_scores, llm_scores)
    assert scorecard["human_llm_agreement"]["recall"] == 0.5
    assert scorecard["human_llm_agreement"]["actionability"] == 1.0


def test_build_scorecard_handles_no_llm_scores_at_all():
    manual_scores = {"001": {"Tool A": {"recall": "hit", "false_positives": [], "actionability": 4}}}
    scorecard = build_scorecard(manual_scores, llm_scores={})
    assert scorecard["human_llm_agreement"]["recall"] is None
    assert scorecard["human_llm_agreement"]["actionability"] is None


def test_build_scorecard_ignores_llm_scores_for_unscored_manual_cases():
    # Case 001: real matching comparison (both scored the same)
    # Case 002: LLM scored but manual is None (unscored) — should not count as comparison
    manual_scores = {
        "001": {"Tool A": {"recall": "hit", "false_positives": [], "actionability": 4}},
        "002": {"Tool A": {"recall": None, "false_positives": [], "actionability": None}},
    }
    llm_scores = {
        "001": {"Tool A": {"recall": "hit", "actionability": 4}},
        "002": {"Tool A": {"recall": "hit", "actionability": 5}},
    }
    scorecard = build_scorecard(manual_scores, llm_scores)
    # Agreement rate should reflect only case 001 (the real comparison)
    assert scorecard["human_llm_agreement"]["recall"] == 1.0
    assert scorecard["human_llm_agreement"]["actionability"] == 1.0
