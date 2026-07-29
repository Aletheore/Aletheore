from scripts.check_citations import verify_findings_against_checkout


def make_checkout(tmp_path):
    (tmp_path / "app.py").write_text("line1\nline2\nline3\n")
    return tmp_path


def test_verify_findings_marks_valid_file_and_line_as_verified(tmp_path):
    checkout = make_checkout(tmp_path)
    findings = [{"file": "app.py", "line": 2, "message": "ok", "severity": None}]
    result = verify_findings_against_checkout(findings, checkout)
    assert result["total_findings"] == 1
    assert result["verified"] == findings
    assert result["unverified"] == []
    assert result["grounding_rate"] == 1.0
    # "ok" is under the 8-char quote floor and isn't quoted anyway, so this
    # finding has no verbatim anchor and can't be scored for content.
    assert result["content_uncheckable"] == findings
    assert result["content_grounding_rate"] is None


def test_verify_findings_marks_missing_file_as_unverified(tmp_path):
    checkout = make_checkout(tmp_path)
    findings = [{"file": "ghost.py", "line": 1, "message": "hallucinated", "severity": None}]
    result = verify_findings_against_checkout(findings, checkout)
    assert result["verified"] == []
    assert result["unverified"] == findings
    assert result["grounding_rate"] == 0.0


def test_verify_findings_marks_out_of_bounds_line_as_unverified(tmp_path):
    checkout = make_checkout(tmp_path)
    findings = [{"file": "app.py", "line": 99, "message": "bad line", "severity": None}]
    result = verify_findings_against_checkout(findings, checkout)
    assert result["unverified"] == findings


def test_verify_findings_treats_missing_file_key_as_unverified(tmp_path):
    checkout = make_checkout(tmp_path)
    findings = [{"file": None, "line": None, "message": "vague comment", "severity": None}]
    result = verify_findings_against_checkout(findings, checkout)
    assert result["unverified"] == findings


def test_verify_findings_handles_empty_findings_list(tmp_path):
    checkout = make_checkout(tmp_path)
    result = verify_findings_against_checkout([], checkout)
    assert result["total_findings"] == 0
    assert result["verified"] == []
    assert result["unverified"] == []
    assert result["grounding_rate"] is None
    assert result["content_grounding_rate"] is None


def test_verify_findings_rejects_absolute_path_outside_checkout(tmp_path):
    checkout = make_checkout(tmp_path)
    # Absolute path that escapes checkout_dir
    findings = [{"file": "/etc/hosts", "line": 1, "message": "absolute path", "severity": None}]
    result = verify_findings_against_checkout(findings, checkout)
    assert result["verified"] == []
    assert result["unverified"] == findings
    assert result["grounding_rate"] == 0.0


def test_verify_findings_rejects_traversal_path_outside_checkout(tmp_path):
    checkout = make_checkout(tmp_path)
    # Path traversal that escapes checkout_dir
    findings = [{"file": "../../../../../../etc/hosts", "line": 1, "message": "traversal", "severity": None}]
    result = verify_findings_against_checkout(findings, checkout)
    assert result["verified"] == []
    assert result["unverified"] == findings
    assert result["grounding_rate"] == 0.0


def _checkout_with_bug(tmp_path):
    lines = ["filler"] * 20
    lines[9] = "return 'SELECT * FROM users WHERE name = ' + name"  # line 10
    (tmp_path / "db.py").write_text("\n".join(lines) + "\n")
    return tmp_path


def test_content_grounding_passes_when_the_quote_is_at_the_cited_line(tmp_path):
    checkout = _checkout_with_bug(tmp_path)
    findings = [
        {
            "file": "db.py",
            "line": 10,
            "message": "Concatenation here: 'SELECT * FROM users WHERE name = '",
            "severity": None,
        }
    ]

    result = verify_findings_against_checkout(findings, checkout)

    assert result["content_verified"] == findings
    assert result["content_grounding_rate"] == 1.0


def test_content_grounding_fails_when_the_quote_is_nowhere_near_the_cited_line(tmp_path):
    # The distinguishing case: location grounding passes (real file, real
    # line) while content grounding correctly fails. A checker that only
    # measured location would score this a perfect 1.0.
    checkout = _checkout_with_bug(tmp_path)
    findings = [
        {
            "file": "db.py",
            "line": 1,
            "message": "Concatenation here: 'SELECT * FROM users WHERE name = '",
            "severity": None,
        }
    ]

    result = verify_findings_against_checkout(findings, checkout)

    assert result["grounding_rate"] == 1.0
    assert result["content_unverified"] == findings
    assert result["content_grounding_rate"] == 0.0


def test_content_grounding_excludes_findings_with_nothing_quoted(tmp_path):
    # A correctly abstract finding quotes nothing verbatim. Scoring it as a
    # failure would punish good writing; scoring it as a pass would inflate
    # the rate. It belongs in neither side of the ratio.
    checkout = _checkout_with_bug(tmp_path)
    findings = [
        {"file": "db.py", "line": 10, "message": "SQL injection via string building", "severity": None},
        {
            "file": "db.py",
            "line": 10,
            "message": "Look at 'SELECT * FROM users WHERE name = '",
            "severity": None,
        },
    ]

    result = verify_findings_against_checkout(findings, checkout)

    assert result["content_uncheckable"] == [findings[0]]
    assert result["content_verified"] == [findings[1]]
    assert result["content_grounding_rate"] == 1.0


def test_content_grounding_ignores_a_suggested_fix(tmp_path):
    # A suggestion is the code the tool wants written, so searching for it
    # in the code as it stands can only fail. Mistaking one for the other
    # was a real production bug that silently discarded correct findings.
    checkout = _checkout_with_bug(tmp_path)
    findings = [
        {
            "file": "db.py",
            "line": 10,
            "message": "SQL injection here",
            "suggestion": "use 'SELECT * FROM users WHERE name = ?' with a bound parameter",
            "severity": None,
        }
    ]

    result = verify_findings_against_checkout(findings, checkout)

    assert result["content_uncheckable"] == findings
    assert result["content_unverified"] == []


def test_content_grounding_is_not_scored_for_a_finding_that_failed_location(tmp_path):
    checkout = _checkout_with_bug(tmp_path)
    findings = [
        {"file": "ghost.py", "line": 1, "message": "quotes 'something long enough'", "severity": None}
    ]

    result = verify_findings_against_checkout(findings, checkout)

    assert result["unverified"] == findings
    assert result["content_verified"] == []
    assert result["content_unverified"] == []
    assert result["content_grounding_rate"] is None
