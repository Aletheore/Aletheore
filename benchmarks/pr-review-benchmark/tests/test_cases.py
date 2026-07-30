import pytest
from scripts.cases import load_case, load_repo_pointer, load_ground_truth


def make_case(tmp_path, category="injected_bug"):
    case_dir = tmp_path / "001-example"
    case_dir.mkdir()
    (case_dir / "repo.txt").write_text(
        "repo_url=https://example.com/repo.git\nbase_commit=abc123\n"
    )
    (case_dir / "pr.diff").write_text("diff --git a/x.py b/x.py\n")
    (case_dir / "ground_truth.yaml").write_text(
        "case_id: 001-example\n"
        "language: python\n"
        f"category: {category}\n"
        "bug_type: sql-injection\n"
        "expected_file: x.py\n"
        "expected_line: 10\n"
        "fix_reference: null\n"
        "description: test case\n"
    )
    return case_dir


def test_load_repo_pointer_parses_key_value_pairs(tmp_path):
    case_dir = make_case(tmp_path)
    pointer = load_repo_pointer(case_dir)
    assert pointer == {"repo_url": "https://example.com/repo.git", "base_commit": "abc123"}


def test_load_repo_pointer_requires_repo_url_and_base_commit(tmp_path):
    case_dir = tmp_path / "002-bad"
    case_dir.mkdir()
    (case_dir / "repo.txt").write_text("repo_url=https://example.com/repo.git\n")
    with pytest.raises(ValueError, match="repo_url and base_commit"):
        load_repo_pointer(case_dir)


def test_load_ground_truth_rejects_unknown_category(tmp_path):
    case_dir = make_case(tmp_path, category="not_a_real_category")
    with pytest.raises(ValueError, match="category must be one of"):
        load_ground_truth(case_dir)


def test_load_case_returns_combined_case_dict(tmp_path):
    case_dir = make_case(tmp_path)
    case = load_case(case_dir)
    assert case["case_id"] == "001-example"
    assert case["repo"]["base_commit"] == "abc123"
    assert case["ground_truth"]["category"] == "injected_bug"
    assert case["diff_path"] == case_dir / "pr.diff"
