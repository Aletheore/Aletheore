from scripts.normalize import (
    normalize_aletheore,
    normalize_pr_agent,
    normalize_deepsource,
)


def test_normalize_aletheore_extracts_citations_from_bot_pr_comments():
    # Aletheore's hosted Flash Review posts findings as a GitHub PR comment
    # from aletheore[bot] (fetched and bot-filtered the same way as
    # DeepSource's, in scripts/adapters.py), not a whole CLI `audit` report.
    # As of 2026-07-26 real PRs have only produced a scan-timeout error
    # comment from this bot, not yet a successful finding-bearing one --
    # this citation-extraction approach (carried over from the old
    # whole-report-text parsing) needs re-verification against a real
    # successful comment before it's fully trusted.
    raw_comments = [{
        "body": (
            "This endpoint has no auth check at `app/routes.py:42`, which allows "
            "unauthenticated access.\n\n"
            "Unrelated paragraph with no citation."
        ),
    }]
    findings = normalize_aletheore(raw_comments)
    assert findings == [{
        "file": "app/routes.py",
        "line": 42,
        "message": (
            "This endpoint has no auth check at `app/routes.py:42`, which allows "
            "unauthenticated access."
        ),
        "severity": None,
    }]


def test_normalize_aletheore_excludes_suggestion_from_message():
    # Real captured excerpt from https://github.com/ArihantK15/
    # proctor-browser/pull/214 (case 016-flask-sql-injection-user-lookup).
    # The suggestion's quoted replacement query ("...WHERE username = ?")
    # must not end up in `message`, or check_citations.py's content-
    # grounding check quote-verifies it against the *current* (different,
    # unparameterized) code and fails by construction -- see
    # scripts/check_citations.py's own docstring on this exact trap.
    raw_comments = [{
        "body": (
            "- `benchmark-sandbox/016-flask-sql-injection-user-lookup/"
            "src/flask/helpers.py:655` — SQL injection vulnerability: "
            "user-supplied username is concatenated directly into the SQL "
            "query string without parameterization. An attacker can "
            "inject arbitrary SQL.\n"
            "  ```\n"
            "  Use a parameterized query, e.g.: return \"SELECT id, "
            "username, email FROM users WHERE username = ?\" and pass "
            "username as a parameter to the database cursor.\n"
            "  ```"
        ),
    }]
    findings = normalize_aletheore(raw_comments)
    assert len(findings) == 1
    assert findings[0]["file"] == (
        "benchmark-sandbox/016-flask-sql-injection-user-lookup/src/flask/helpers.py"
    )
    assert findings[0]["line"] == 655
    assert (
        "SELECT id, username, email FROM users WHERE username = ?"
        not in findings[0]["message"]
    )
    assert "SQL injection vulnerability" in findings[0]["message"]


def test_normalize_pr_agent_reads_recommended_focus_areas_from_real_comment():
    # Real PR-Agent 0.39.0 `review` output does not print JSON to stdout and
    # does not emit a `code_suggestions` list (that key belongs to PR-Agent's
    # separate `improve` command). It posts a single markdown/HTML "PR
    # Reviewer Guide" comment to the PR. This fixture is a trimmed real
    # excerpt captured 2026-07-26 from
    # https://github.com/ArihantK15/proctor-browser/pull/213 (DeepSeek
    # backend, case 001-flask-cli-key-quote).
    comment_body = (
        "## PR Reviewer Guide \U0001F50D\n\n"
        "<table>\n<tr><td>⚡&nbsp;<strong>Recommended focus areas for "
        "review</strong><br><br>\n\n"
        "<details><summary><a href='https://github.com/ArihantK15/"
        "proctor-browser/pull/213/files#diff-13436a0a884b1daeb413962b7346560"
        "fbbba4d319274c148ed9113077ebb2b6fR796-R797'><strong>Possible typo"
        "</strong></a>\n\n"
        "The error message in the `_validate_key` function on line 797 is "
        "missing a closing double quote for the `--key` option.\n"
        "</summary>\n\n"
        "```python\nif is_context:\n```\n\n"
        "</details>\n\n</td></tr>\n</table>"
    )
    raw = {
        "comment_body": comment_body,
        "changed_files": ["src/flask/cli.py"],
    }
    findings = normalize_pr_agent(raw)
    assert findings == [{
        "file": "src/flask/cli.py",
        "line": 797,
        "message": (
            "The error message in the `_validate_key` function on line 797 "
            "is missing a closing double quote for the `--key` option."
        ),
        "severity": "Possible typo",
    }]


def test_normalize_pr_agent_leaves_file_unattributed_for_multi_file_prs():
    comment_body = (
        "<details><summary><a href='https://github.com/x/y/pull/1/files"
        "#diff-deadbeefR10-R12'><strong>Bug</strong></a>\n\n"
        "Some message.\n</summary>\n\n</details>"
    )
    raw = {"comment_body": comment_body, "changed_files": ["a.py", "b.py"]}
    findings = normalize_pr_agent(raw)
    assert findings[0]["file"] is None
    assert findings[0]["line"] == 12


def test_normalize_deepsource_reads_real_github_pr_review_comments():
    # DeepSource's GitHub App posts findings as ordinary GitHub PR *review*
    # comments (path/line/body), not via a separate run_id-keyed issues API
    # returning {"issues": [...]}. This
    # fixture is a trimmed real excerpt captured 2026-07-26 from
    # https://github.com/ArihantK15/proctor-browser/pull/214 (case
    # 016-flask-sql-injection-user-lookup); the finding title and severity
    # are embedded in the HTML body rather than separate JSON fields.
    body = (
        '<!-- DeepSource: id=Q2hlY2tJc3N1ZTpwcXJ3bGF3cmc= -->\n'
        '<h3><picture>\n'
        '<source media="(prefers-color-scheme: dark)" '
        'srcset="https://static.deepsource.com/comment_artifacts/dark/'
        'severity_indicator_major.svg?v=2"/>\n'
        '<img src="https://static.deepsource.com/comment_artifacts/light/'
        'severity_indicator_major.svg?v=2" height="14" hspace="8"/>\n'
        '</picture>Possible SQL injection vector through string-based query '
        'construction.</h3>\n'
        '<div>...</div>\n\n<br/>\n\n'
        'Constructing SQL query using user provided data is insecure.'
    )
    raw_comments = [{
        "path": "benchmark-sandbox/016-flask-sql-injection-user-lookup/src/flask/helpers.py",
        "line": 652,
        "body": body,
    }]
    findings = normalize_deepsource(raw_comments)
    assert findings == [{
        "file": "benchmark-sandbox/016-flask-sql-injection-user-lookup/src/flask/helpers.py",
        "line": 652,
        "message": "Possible SQL injection vector through string-based query construction.",
        "severity": "major",
    }]


def test_normalize_deepsource_falls_back_to_original_line():
    body = "<h3><picture></picture>Some title</h3>"
    raw_comments = [{"path": "app.py", "original_line": 9, "body": body}]
    findings = normalize_deepsource(raw_comments)
    assert findings[0]["line"] == 9


