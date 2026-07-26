from scripts.adapters import (
    aletheore_adapter,
    pr_agent_adapter,
    deepsource_adapter,
    coderabbit_adapter,
)


class _FakeCompletedProcess:
    def __init__(self, stdout):
        self.stdout = stdout


def test_aletheore_adapter_invokes_audit_against_checkout_dir(tmp_path):
    calls = []

    def fake_runner(args, **kwargs):
        calls.append(args)
        return _FakeCompletedProcess("report text")

    result = aletheore_adapter(tmp_path, case={}, runner=fake_runner)
    assert calls == [["aletheore", "audit", str(tmp_path)]]
    assert result == "report text"


def test_pr_agent_adapter_invokes_cli_with_pr_url(tmp_path):
    calls = []

    def fake_runner(args, **kwargs):
        calls.append(args)
        return _FakeCompletedProcess('{"code_suggestions": []}')

    case = {"repo": {"pr_url": "https://github.com/example/repo/pull/1"}}
    result = pr_agent_adapter(tmp_path, case, runner=fake_runner)
    assert calls == [[
        "python", "-m", "pr_agent.cli",
        "--pr_url", "https://github.com/example/repo/pull/1", "review",
    ]]
    assert result == {"code_suggestions": []}


def test_deepsource_adapter_calls_fetch_issues_with_run_id(tmp_path):
    case = {"repo": {"deepsource_run_id": "run-42"}}
    captured = {}

    def fake_fetch(run_id):
        captured["run_id"] = run_id
        return {"issues": []}

    result = deepsource_adapter(tmp_path, case, fetch_issues=fake_fetch)
    assert captured["run_id"] == "run-42"
    assert result == {"issues": []}


def test_coderabbit_adapter_calls_fetch_pr_comments_with_pr_url(tmp_path):
    case = {"repo": {"pr_url": "https://github.com/example/repo/pull/1"}}
    captured = {}

    def fake_fetch(pr_url):
        captured["pr_url"] = pr_url
        return [{"path": "x.py", "line": 1, "body": "comment"}]

    result = coderabbit_adapter(tmp_path, case, fetch_pr_comments=fake_fetch)
    assert captured["pr_url"] == "https://github.com/example/repo/pull/1"
    assert result == [{"path": "x.py", "line": 1, "body": "comment"}]
