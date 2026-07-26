import yaml
from scripts.scoring_template import build_blank_scorecard, write_blank_scorecard


def test_build_blank_scorecard_has_one_entry_per_label():
    card = build_blank_scorecard("001-example", ["Tool A", "Tool B"])
    assert card == {
        "case_id": "001-example",
        "scores": {
            "Tool A": {"recall": None, "false_positives": [], "actionability": None},
            "Tool B": {"recall": None, "false_positives": [], "actionability": None},
        },
    }


def test_write_blank_scorecard_writes_valid_yaml(tmp_path):
    out_path = tmp_path / "001-example.yaml"
    write_blank_scorecard("001-example", ["Tool A"], out_path)
    loaded = yaml.safe_load(out_path.read_text())
    assert loaded["case_id"] == "001-example"
    assert loaded["scores"]["Tool A"]["recall"] is None
