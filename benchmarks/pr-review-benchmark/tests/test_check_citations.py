from scripts.check_citations import verify_findings_against_checkout


def make_checkout(tmp_path):
    (tmp_path / "app.py").write_text("line1\nline2\nline3\n")
    return tmp_path


def test_verify_findings_marks_valid_file_and_line_as_verified(tmp_path):
    checkout = make_checkout(tmp_path)
    findings = [{"file": "app.py", "line": 2, "message": "ok", "severity": None}]
    result = verify_findings_against_checkout(findings, checkout)
    assert result == {
        "total_findings": 1,
        "verified": findings,
        "unverified": [],
        "grounding_rate": 1.0,
    }


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
    assert result == {
        "total_findings": 0,
        "verified": [],
        "unverified": [],
        "grounding_rate": None,
    }


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
