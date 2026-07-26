import json
from scripts.run_case import run_case
from tests.test_build_case_repo import make_fixture_repo


def test_run_case_writes_raw_and_grounding_output_for_each_tool(tmp_path):
    remote, base_commit, diff_path = make_fixture_repo(tmp_path)

    case_dir = tmp_path / "cases" / "001-example"
    case_dir.mkdir(parents=True)
    (case_dir / "repo.txt").write_text(f"repo_url={remote}\nbase_commit={base_commit}\n")
    diff_path.rename(case_dir / "pr.diff")
    (case_dir / "ground_truth.yaml").write_text(
        "case_id: 001-example\nlanguage: python\ncategory: injected_bug\n"
        "bug_type: test\nexpected_file: x.py\nexpected_line: 1\n"
        "fix_reference: null\ndescription: test\n"
    )

    workdir = tmp_path / "work"
    workdir.mkdir()
    results_dir = tmp_path / "results"

    adapters = {"fake_tool": lambda checkout_dir, case: "finding at `x.py:1`."}
    normalizers = {
        "fake_tool": lambda raw: [{"file": "x.py", "line": 1, "message": raw, "severity": None}]
    }

    result = run_case(case_dir, workdir, results_dir, adapters, normalizers)

    assert result["case_id"] == "001-example"
    raw_path = results_dir / "raw" / "001-example" / "fake_tool.json"
    assert json.loads(raw_path.read_text()) == "finding at `x.py:1`."

    grounding_path = results_dir / "grounding" / "001-example" / "fake_tool.json"
    grounding = json.loads(grounding_path.read_text())
    assert grounding["grounding_rate"] == 1.0
