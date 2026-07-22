from aletheore.citation_verifier import extract_citations, verify_citations


def make_evidence() -> dict:
    return {
        "repository": {
            "modules": [
                {"path": "server/routes/billing.ts"},
                {"path": "app/auth.py"},
            ],
            "unparseable_files": [
                {"path": "vendor/minified.js"},
            ],
        }
    }


def test_extract_citations_finds_file_line_pairs():
    text = (
        "The checkout route depends on `server/routes/billing.ts:142` "
        "without exception handling. See also app/auth.py:7 for context."
    )
    citations = extract_citations(text)
    assert {"file": "server/routes/billing.ts", "line": 142} in citations
    assert {"file": "app/auth.py", "line": 7} in citations
    assert len(citations) == 2


def test_extract_citations_returns_empty_for_no_citations():
    assert extract_citations("This report has no file references at all.") == []


def test_verify_citations_marks_known_file_as_verified():
    text = "Issue found at `server/routes/billing.ts:142`."
    result = verify_citations(text, make_evidence())

    assert result["total_citations"] == 1
    assert result["verified"] == [{"file": "server/routes/billing.ts", "line": 142}]
    assert result["unverified"] == []
    assert result["all_verified"] is True


def test_verify_citations_marks_unknown_file_as_unverified():
    text = "Issue found at `made/up/path.py:99`."
    result = verify_citations(text, make_evidence())

    assert result["total_citations"] == 1
    assert result["verified"] == []
    assert result["unverified"] == [{"file": "made/up/path.py", "line": 99}]
    assert result["all_verified"] is False


def test_verify_citations_checks_unparseable_files_too():
    text = "See vendor/minified.js:1 for the bundled output."
    result = verify_citations(text, make_evidence())

    assert result["all_verified"] is True


def test_verify_citations_handles_a_mix_of_real_and_hallucinated_citations():
    text = (
        "Real finding at `app/auth.py:7`. "
        "Hallucinated finding at `nonexistent/ghost.py:1000`."
    )
    result = verify_citations(text, make_evidence())

    assert result["total_citations"] == 2
    assert len(result["verified"]) == 1
    assert len(result["unverified"]) == 1
    assert result["all_verified"] is False


def test_verify_citations_handles_report_with_no_citations():
    result = verify_citations("General commentary, no file references.", make_evidence())

    assert result == {
        "total_citations": 0,
        "verified": [],
        "unverified": [],
        "all_verified": True,
    }
