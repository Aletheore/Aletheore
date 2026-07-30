import json
from scripts.run_case import run_case, _strip_sandbox_prefix
from tests.test_build_case_repo import make_fixture_repo


def test_strip_sandbox_prefix_removes_case_scoped_monorepo_prefix():
    # All 25 real cases share one scratch repo (see README Step 1); each
    # case's files live under benchmark-sandbox/<case-id>/ within it to
    # avoid collisions between concurrently-open case PRs. Tools cite paths
    # relative to that scratch repo, but the grounding check runs against a
    # standalone checkout of the case's own real repo -- so this prefix
    # must be stripped before verify_findings_against_checkout() runs.
    assert _strip_sandbox_prefix(
        "benchmark-sandbox/001-flask-cli-key-quote/src/flask/cli.py",
        "001-flask-cli-key-quote",
    ) == "src/flask/cli.py"


def test_strip_sandbox_prefix_leaves_unprefixed_paths_unchanged():
    assert _strip_sandbox_prefix("src/flask/cli.py", "001-flask-cli-key-quote") == "src/flask/cli.py"


def test_strip_sandbox_prefix_handles_none():
    assert _strip_sandbox_prefix(None, "001-flask-cli-key-quote") is None


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


def test_run_case_strips_sandbox_prefix_before_grounding_check(tmp_path):
    # Reproduces the real live-run shape: tools cite paths prefixed with
    # benchmark-sandbox/<case-id>/ (the shared scratch repo convention),
    # which would falsely fail the grounding check against the case's own
    # standalone checkout if left unstripped.
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

    adapters = {"fake_tool": lambda checkout_dir, case: "raw"}
    normalizers = {
        "fake_tool": lambda raw: [
            {"file": "benchmark-sandbox/001-example/x.py", "line": 1, "message": raw, "severity": None}
        ]
    }

    run_case(case_dir, workdir, results_dir, adapters, normalizers)

    grounding_path = results_dir / "grounding" / "001-example" / "fake_tool.json"
    grounding = json.loads(grounding_path.read_text())
    assert grounding["grounding_rate"] == 1.0
    assert grounding["verified"][0]["file"] == "x.py"
