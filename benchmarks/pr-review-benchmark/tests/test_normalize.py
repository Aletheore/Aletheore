from scripts.normalize import (
    normalize_aletheore,
    normalize_pr_agent,
    normalize_deepsource,
    normalize_sourcery,
    normalize_greptile,
)


def test_normalize_aletheore_reads_findings_from_review_comments():
    # Real bug found and fixed 2026-09-13: a genuine Aletheore finding
    # posts as a per-line PR review comment (path/line, same shape as
    # every other tool in this benchmark), not as inline citations in the
    # issue comment's prose - the issue comment is reduced to a summary
    # ("N finding(s) posted as inline review comment(s) below."). Real
    # excerpt captured from this benchmark's own 2026-09-13 run (case
    # 009-cobra-completions-args-mutation, PR #309) - the old
    # issue-comments-only adapter silently scored this as 0 findings.
    raw = {
        "issue_comments": [{
            "body": "1 finding(s) posted as inline review comment(s) below.",
        }],
        "review_comments": [{
            "path": "benchmark-sandbox/009-cobra-completions-args-mutation/completions.go",
            "line": 320,
            "body": (
                "trimmedArgs used to be a defensive copy of args; the changed code "
                "aliases it directly instead, so a later mutation of trimmedArgs can "
                "corrupt args's backing array.\n\n"
                "```\n"
                "Restore the copy (allocate trimmedArgs and `copy(trimmedArgs, args)`) "
                "before mutating trimmedArgs.\n"
                "```\n\n"
                "_Reply `/dismiss` (optionally with a reason) if this isn't helpful - "
                "Aletheore won't raise it again on this repo._"
            ),
        }],
    }
    findings = normalize_aletheore(raw)
    assert len(findings) == 1
    assert findings[0]["file"] == "benchmark-sandbox/009-cobra-completions-args-mutation/completions.go"
    assert findings[0]["line"] == 320
    assert "aliases it directly instead" in findings[0]["message"]
    # The suggestion's replacement code must not end up in `message`, or
    # check_citations.py's content-grounding check quote-verifies it
    # against the *current* (different) code and fails by construction.
    assert "Restore the copy" not in findings[0]["message"]


def test_normalize_aletheore_falls_back_to_issue_comment_citations():
    # Defensive fallback, not the real current path: a citation embedded
    # directly in the issue comment's own prose (the format this benchmark
    # was originally built around) is still picked up if review_comments
    # is empty - so a future format change reverting to prose citations
    # doesn't silently go invisible here the way the review-comment
    # format change did.
    raw = {
        "issue_comments": [{
            "body": (
                "This endpoint has no auth check at `app/routes.py:42`, which allows "
                "unauthenticated access.\n\n"
                "Unrelated paragraph with no citation."
            ),
        }],
        "review_comments": [],
    }
    findings = normalize_aletheore(raw)
    assert findings == [{
        "file": "app/routes.py",
        "line": 42,
        "message": (
            "This endpoint has no auth check at `app/routes.py:42`, which allows "
            "unauthenticated access."
        ),
        "severity": None,
    }]


def test_normalize_aletheore_does_not_double_count_the_same_location():
    # If a location somehow appears in both surfaces (not the real current
    # shape, but not guaranteed to stay that way), it must be counted once.
    raw = {
        "issue_comments": [{
            "body": "Also flagged at `app/routes.py:42` in this paragraph.",
        }],
        "review_comments": [{
            "path": "app/routes.py", "line": 42, "body": "Real per-line finding.",
        }],
    }
    findings = normalize_aletheore(raw)
    assert len(findings) == 1
    assert findings[0]["message"] == "Real per-line finding."


def test_normalize_aletheore_returns_no_findings_when_nothing_was_flagged():
    # "No issues found in this diff." (the real message for a genuine
    # zero-finding review) has no review comments and no citation to
    # extract from the issue comment - must produce an empty list, not
    # crash on either source being absent/empty.
    raw = {
        "issue_comments": [{"body": "No issues found in this diff."}],
        "review_comments": [],
    }
    assert normalize_aletheore(raw) == []


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




def test_normalize_sourcery_extracts_category_and_message_from_real_finding():
    # Real captured excerpt from https://github.com/genomehubs/kinfin/
    # pull/116 (an unrelated, real public PR checked while verifying
    # Sourcery's actual comment format - most real PRs sampled while
    # verifying this had Sourcery either rate-limited or reporting a clean
    # "Approved" review with no per-line comments at all, which is why
    # this fixture is the one real finding found, not a representative
    # sample of Sourcery's full range of category labels).
    body = (
        "**nitpick:** The updated README documents partition ID "
        "`fb1a511e4761d2e9`, but the example clustering configuration and "
        "status fixture use `804b707a77993f0b`; following the documented "
        "resolve result or status commands therefore returns a missing "
        "status instead of the provided example data.\n\n"
        "**Triggers:** When users follow the README examples against the "
        "checked-in example dataset."
    )
    raw_comments = [{"path": "src/api/examples/README.md", "body": body}]
    findings = normalize_sourcery(raw_comments)
    assert len(findings) == 1
    assert findings[0]["file"] == "src/api/examples/README.md"
    assert findings[0]["line"] is None
    assert findings[0]["severity"] == "nitpick"
    assert findings[0]["message"].startswith("nitpick: The updated README")


def test_normalize_sourcery_falls_back_to_raw_body_without_category_prefix():
    raw_comments = [{"path": "x.py", "line": 5, "body": "Just a plain comment, no category prefix."}]
    findings = normalize_sourcery(raw_comments)
    assert findings == [{
        "file": "x.py",
        "line": 5,
        "message": "Just a plain comment, no category prefix.",
        "severity": None,
    }]


def test_normalize_greptile_extracts_priority_and_title_from_review_comments():
    # Real captured excerpt from https://github.com/moltis-org/moltis/
    # pull/1266 (an unrelated, real public PR checked while verifying
    # Greptile's actual comment format).
    body = (
        '<a href="#"><img alt="P1" src="https://greptile-static-assets.'
        's3.amazonaws.com/badges/p1.svg?v=9" align="top"></a> '
        "**Restoration Overwrites New Selection**\n\n"
        "During session restoration, this disables only the reasoning "
        "button while the model selector remains active."
    )
    raw = {"review_comments": [{
        "path": "crates/web/ui/src/reasoning-toggle.ts", "line": 112, "body": body,
    }]}
    findings = normalize_greptile(raw)
    assert findings == [{
        "file": "crates/web/ui/src/reasoning-toggle.ts",
        "line": 112,
        "message": "Restoration Overwrites New Selection",
        "severity": "P1",
    }]


def test_normalize_greptile_ignores_the_issue_comment_summary():
    # The prose summary (issue_comments) is deliberately not turned into
    # findings - see normalize_greptile's own docstring for why.
    raw = {
        "issue_comments": [{"body": "<h3>Greptile Summary</h3>\n\nThis PR does X."}],
        "review_comments": [],
    }
    assert normalize_greptile(raw) == []
