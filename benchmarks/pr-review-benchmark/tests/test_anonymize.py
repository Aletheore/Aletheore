import json
import random
from scripts.anonymize import assign_labels, write_anonymized_case, reveal_mapping


def test_assign_labels_maps_each_tool_to_a_distinct_label():
    rng = random.Random(42)
    mapping = assign_labels(["aletheore", "pr_agent", "deepsource", "coderabbit"], rng)
    assert set(mapping.keys()) == {"Tool A", "Tool B", "Tool C", "Tool D"}
    assert set(mapping.values()) == {"aletheore", "pr_agent", "deepsource", "coderabbit"}


def test_assign_labels_rejects_more_tools_than_labels():
    rng = random.Random(1)
    try:
        assign_labels(["a", "b", "c", "d", "e"], rng)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_write_anonymized_case_and_reveal_mapping_round_trip(tmp_path):
    rng = random.Random(7)
    findings_by_tool = {
        "aletheore": [{"file": "x.py", "line": 1, "message": "m", "severity": None}],
        "pr_agent": [{"file": "y.py", "line": 2, "message": "n", "severity": None}],
    }
    result = write_anonymized_case("001-example", findings_by_tool, tmp_path, rng)

    anon_files = sorted(p.name for p in result["anon_dir"].iterdir())
    assert len(anon_files) == 2

    revealed = reveal_mapping("001-example", tmp_path)
    assert set(revealed.values()) == {"aletheore", "pr_agent"}

    for label, tool in revealed.items():
        anon_path = result["anon_dir"] / f"{label.replace(' ', '_').lower()}.json"
        assert json.loads(anon_path.read_text()) == findings_by_tool[tool]
