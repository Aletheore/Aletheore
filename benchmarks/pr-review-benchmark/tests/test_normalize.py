from scripts.normalize import (
    normalize_aletheore,
    normalize_pr_agent,
    normalize_deepsource,
    normalize_bito,
    normalize_korbit,
    normalize_sourcery,
    normalize_greptile,
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




def test_normalize_bito_extracts_title_and_detail_from_real_finding():
    # Real captured excerpt from https://github.com/apache/superset/
    # pull/43729 (an unrelated, real public PR checked while verifying
    # Bito's actual comment format for this benchmark - Bito is not yet
    # installed on this project's own scratch repo).
    body = (
        "<div>\n\n\n<div id=\"suggestion\">\n"
        "<div id=\"issue\"><b>Nested describe in test</b></div>\n"
        "<div id=\"fix\">\n\n"
        "This `describe` block is nested inside the `test('boundary label "
        "alignment is dropped...')` callback (which opens at line 2843 and "
        "closes at line 2955). jest-circus throws `Cannot nest describe "
        "inside a test`, so this file fails to run. Move the block to "
        "module scope and relocate the `monthData` fixture it references "
        "(currently scoped to the callback).\n</div>\n\n\n</div>\n\n\n\n\n"
        "<small><i>Code Review Run #ea91a1</i></small>\n</div>\n\n---\n"
        "Should Bito avoid suggestions like this for future reviews? "
        "(<a href=https://alpha.bito.ai/home/ai-agents/review-rules>Manage "
        "Rules</a>)\n- [ ] Yes, avoid them"
    )
    raw_comments = [{
        "path": "superset-frontend/plugins/plugin-chart-echarts/test/Timeseries/transformProps.test.ts",
        "line": 3216,
        "body": body,
    }]
    findings = normalize_bito(raw_comments)
    assert len(findings) == 1
    assert findings[0]["file"] == (
        "superset-frontend/plugins/plugin-chart-echarts/test/Timeseries/transformProps.test.ts"
    )
    assert findings[0]["line"] == 3216
    assert findings[0]["message"].startswith("Nested describe in test:")
    assert "Move the block to module scope" in findings[0]["message"]


def test_normalize_bito_excludes_its_own_reply_comments():
    # Bito posts a second comment per finding, on the same path/line,
    # elaborating on its own suggestion - marked with this HTML comment.
    # Real excerpt from the same PR as the finding above.
    reply_body = (
        "<!-- Bito Reply -->\nMoving the `describe` block to the module "
        "scope is the correct approach to resolve the nesting error."
    )
    raw_comments = [{"path": "x.ts", "line": 1, "body": reply_body}]
    assert normalize_bito(raw_comments) == []


def test_normalize_korbit_extracts_title_and_category_from_real_finding():
    # Real captured excerpt from https://github.com/apache/superset/
    # pull/35832 (an unrelated, real public PR checked while verifying
    # Korbit's actual comment format - `line` was null on every real
    # finding sampled, hence the fallback to `original_line` tested below
    # rather than here).
    body = (
        "### Missing limit validation constraints "
        '<sub>![category Security](https://img.shields.io/badge/'
        "Security-e11d48)</sub>\n\n<details>\n  <summary>Tell me more"
        "</summary>\n\n###### What is the issue?\nThe limit field has a "
        "default value in the JSON schema but lacks validation "
        "constraints to prevent potential abuse or system overload.\n"
    )
    raw_comments = [{"path": "superset/reports/schemas.py", "body": body}]
    findings = normalize_korbit(raw_comments)
    assert findings == [{
        "file": "superset/reports/schemas.py",
        "line": None,
        "message": "Missing limit validation constraints",
        "severity": "Security",
    }]


def test_normalize_korbit_falls_back_to_original_line():
    body = "### Some title <sub>![category Performance](url)</sub>"
    raw_comments = [{"path": "app.py", "original_line": 12, "body": body}]
    findings = normalize_korbit(raw_comments)
    assert findings[0]["line"] == 12


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
