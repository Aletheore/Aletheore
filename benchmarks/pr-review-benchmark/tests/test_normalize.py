from scripts.normalize import (
    normalize_aletheore,
    normalize_pr_agent,
    normalize_deepsource,
    normalize_coderabbit,
)


def test_normalize_aletheore_extracts_citation_and_paragraph_as_message():
    report = (
        "This endpoint has no auth check at `app/routes.py:42`, which allows "
        "unauthenticated access.\n\n"
        "Unrelated paragraph with no citation."
    )
    findings = normalize_aletheore(report)
    assert findings == [{
        "file": "app/routes.py",
        "line": 42,
        "message": (
            "This endpoint has no auth check at `app/routes.py:42`, which allows "
            "unauthenticated access."
        ),
        "severity": None,
    }]


def test_normalize_pr_agent_reads_code_suggestions():
    raw = {
        "code_suggestions": [
            {
                "relevant_file": "app.py",
                "relevant_line": 10,
                "suggestion_content": "Use a parameterized query here.",
                "label": "possible bug",
            }
        ]
    }
    findings = normalize_pr_agent(raw)
    assert findings == [{
        "file": "app.py",
        "line": 10,
        "message": "Use a parameterized query here.",
        "severity": "possible bug",
    }]


def test_normalize_deepsource_reads_issues():
    raw = {
        "issues": [
            {
                "title": "Unused import",
                "severity": "minor",
                "location": {"path": "app.py", "position": {"begin": {"line": 3}}},
            }
        ]
    }
    findings = normalize_deepsource(raw)
    assert findings == [{
        "file": "app.py",
        "line": 3,
        "message": "Unused import",
        "severity": "minor",
    }]


def test_normalize_coderabbit_reads_github_review_comments():
    raw_comments = [{"path": "app.py", "line": 5, "body": "Missing null check."}]
    findings = normalize_coderabbit(raw_comments)
    assert findings == [{
        "file": "app.py",
        "line": 5,
        "message": "Missing null check.",
        "severity": None,
    }]


def test_normalize_coderabbit_falls_back_to_original_line():
    raw_comments = [{"path": "app.py", "original_line": 9, "body": "Stale comment."}]
    findings = normalize_coderabbit(raw_comments)
    assert findings[0]["line"] == 9
