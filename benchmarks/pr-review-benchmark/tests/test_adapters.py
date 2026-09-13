import sys

from scripts.adapters import (
    aletheore_adapter,
    pr_agent_adapter,
    deepsource_adapter,
    sourcery_adapter,
    greptile_adapter,
)


class _FakeCompletedProcess:
    def __init__(self, stdout):
        self.stdout = stdout


def test_aletheore_adapter_filters_pr_comments_to_aletheore_bot(tmp_path):
    case = {"repo": {"pr_url": "https://github.com/example/repo/pull/1"}}
    captured = {}

    def fake_fetch(pr_url):
        captured["pr_url"] = pr_url
        return [
            {"path": "x.py", "line": 1, "body": "aletheore finding", "user": {"login": "aletheore[bot]"}},
            {"path": "y.py", "line": 2, "body": "other bot", "user": {"login": "deepsource-io[bot]"}},
        ]

    result = aletheore_adapter(tmp_path, case, fetch_pr_comments=fake_fetch)
    assert captured["pr_url"] == "https://github.com/example/repo/pull/1"
    assert result == [
        {"path": "x.py", "line": 1, "body": "aletheore finding", "user": {"login": "aletheore[bot]"}},
    ]


def test_deepsource_adapter_filters_pr_comments_to_deepsource_bot(tmp_path):
    case = {"repo": {"pr_url": "https://github.com/example/repo/pull/1"}}

    def fake_fetch(pr_url):
        return [
            {"path": "x.py", "line": 1, "body": "aletheore finding", "user": {"login": "aletheore[bot]"}},
            {"path": "y.py", "line": 2, "body": "deepsource finding", "user": {"login": "deepsource-io[bot]"}},
        ]

    result = deepsource_adapter(tmp_path, case, fetch_pr_comments=fake_fetch)
    assert result == [
        {"path": "y.py", "line": 2, "body": "deepsource finding", "user": {"login": "deepsource-io[bot]"}},
    ]


def test_pr_agent_adapter_invokes_cli_with_deepseek_flash_model_and_fetches_review(tmp_path):
    calls = []

    def fake_runner(args, **kwargs):
        calls.append(args)
        return _FakeCompletedProcess("")

    captured = {}

    def fake_fetch_review(pr_url):
        captured["pr_url"] = pr_url
        return {"comment_body": "## PR Reviewer Guide", "changed_files": ["src/flask/cli.py"]}

    case = {"repo": {"pr_url": "https://github.com/example/repo/pull/1"}}
    result = pr_agent_adapter(tmp_path, case, runner=fake_runner, fetch_review=fake_fetch_review)

    assert calls == [[
        sys.executable, "-m", "pr_agent.cli",
        "--pr_url", "https://github.com/example/repo/pull/1",
        "review",
        "--config.model=deepseek/deepseek-v4-flash",
    ]]
    assert captured["pr_url"] == "https://github.com/example/repo/pull/1"
    assert result == {"comment_body": "## PR Reviewer Guide", "changed_files": ["src/flask/cli.py"]}


def test_sourcery_adapter_filters_review_comments_to_sourcery_bot(tmp_path):
    case = {"repo": {"pr_url": "https://github.com/example/repo/pull/1"}}

    def fake_fetch(pr_url):
        return [
            {"path": "x.py", "line": 1, "body": "sourcery finding", "user": {"login": "sourcery-ai[bot]"}},
            {"path": "y.py", "line": 2, "body": "other bot", "user": {"login": "korbit-ai[bot]"}},
        ]

    result = sourcery_adapter(tmp_path, case, fetch_pr_review_comments=fake_fetch)
    assert result == [
        {"path": "x.py", "line": 1, "body": "sourcery finding", "user": {"login": "sourcery-ai[bot]"}},
    ]


def test_greptile_adapter_fetches_and_filters_both_comment_surfaces(tmp_path):
    case = {"repo": {"pr_url": "https://github.com/example/repo/pull/1"}}
    captured = {}

    def fake_fetch_issue(pr_url):
        captured["issue_pr_url"] = pr_url
        return [
            {"body": "greptile summary", "user": {"login": "greptile-apps[bot]"}},
            {"body": "unrelated", "user": {"login": "someone-else"}},
        ]

    def fake_fetch_review(pr_url):
        captured["review_pr_url"] = pr_url
        return [
            {"path": "x.py", "line": 1, "body": "greptile line finding", "user": {"login": "greptile-apps[bot]"}},
            {"path": "y.py", "line": 2, "body": "other bot", "user": {"login": "bito-code-review[bot]"}},
        ]

    result = greptile_adapter(
        tmp_path, case, fetch_pr_comments=fake_fetch_issue, fetch_pr_review_comments=fake_fetch_review
    )
    assert captured["issue_pr_url"] == "https://github.com/example/repo/pull/1"
    assert captured["review_pr_url"] == "https://github.com/example/repo/pull/1"
    assert result == {
        "issue_comments": [{"body": "greptile summary", "user": {"login": "greptile-apps[bot]"}}],
        "review_comments": [
            {"path": "x.py", "line": 1, "body": "greptile line finding", "user": {"login": "greptile-apps[bot]"}},
        ],
    }
