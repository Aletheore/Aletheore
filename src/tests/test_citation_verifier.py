import json

from aletheore.citation_verifier import (
    citation_verification_section,
    extract_citations,
    load_verifiable_evidence,
    local_line_count_fetcher,
    verify_citations,
)
from aletheore.evidence import EVIDENCE_VERSION


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
        "line_bounds_checked": 0,
    }


def test_verify_citations_without_fetch_line_count_ignores_fabricated_lines():
    # Documents the existing, acknowledged limitation: without a real line
    # count to check against, a citation naming a real file but a
    # fabricated line number is still reported as verified.
    text = "Issue found at `server/routes/billing.ts:99999`."
    result = verify_citations(text, make_evidence())

    assert result["all_verified"] is True


def test_verify_citations_with_fetch_line_count_catches_a_fabricated_line():
    # Closes the gap above when a real line count is available: this is
    # the same category of bug confirmed in Flash Review on a real PR (see
    # flash_review.py's _line_citation_content_matches) - a citation
    # naming a real file but a line beyond its real length is a fabricated
    # citation, not a verified one.
    text = "Issue found at `server/routes/billing.ts:99999`."
    result = verify_citations(text, make_evidence(), fetch_line_count=lambda path: 200)

    assert result["all_verified"] is False
    assert result["unverified"] == [{"file": "server/routes/billing.ts", "line": 99999}]


def test_verify_citations_with_fetch_line_count_keeps_a_real_line():
    text = "Issue found at `server/routes/billing.ts:142`."
    result = verify_citations(text, make_evidence(), fetch_line_count=lambda path: 200)

    assert result["all_verified"] is True
    assert result["verified"] == [{"file": "server/routes/billing.ts", "line": 142}]


def test_verify_citations_with_fetch_line_count_returning_none_skips_bounds_check():
    # A fetcher that can't determine a file's line count (e.g. a fetch
    # failure) must not turn into a false "unverified" - the citation
    # falls back to file-existence-only, same as without a fetcher at all.
    text = "Issue found at `server/routes/billing.ts:99999`."
    result = verify_citations(text, make_evidence(), fetch_line_count=lambda path: None)

    assert result["all_verified"] is True


def test_extract_citations_finds_extensionless_files_from_the_scan_inventory():
    # Dockerfile:12 was previously invisible: _CITATION_PATTERN requires a
    # dot-extension, so such a claim was neither verified nor flagged.
    text = "The base image is pinned at `Dockerfile:12` and the target at `ops/Makefile:8`."

    citations = extract_citations(text, {"Dockerfile", "ops/Makefile"})

    assert {"file": "Dockerfile", "line": 12} in citations
    assert {"file": "ops/Makefile", "line": 8} in citations


def test_extract_citations_without_inventory_still_ignores_extensionless_files():
    assert extract_citations("see `Dockerfile:12`") == []


def test_extract_citations_does_not_match_prose_or_urls_as_extensionless_files():
    # The extensionless pattern is built only from real scanned paths, so
    # it must not start matching host:port or "step 3:12"-style text.
    text = "Deployed to http://internal-host:8080 at step 3:12 yesterday."

    assert extract_citations(text, {"Dockerfile"}) == []


def test_extract_citations_does_not_duplicate_a_path_matched_by_both_patterns():
    text = "See `app.py:5`."

    assert extract_citations(text, {"app.py"}) == [{"file": "app.py", "line": 5}]


def test_verify_citations_rejects_line_zero_in_a_real_file():
    # Only the upper bound was guarded, so a citation at line 0 - which
    # points at nothing in any file - counted as verified. Uses a path that
    # really is in the inventory, so the rejection can only come from the
    # line number itself.
    result = verify_citations("See `app/auth.py:0`.", make_evidence())

    assert result["all_verified"] is False
    assert result["unverified"] == [{"file": "app/auth.py", "line": 0}]


def test_verify_citations_verifies_a_real_extensionless_file():
    evidence = {"repository": {"modules": [{"path": "Dockerfile"}]}}

    result = verify_citations("Pinned at `Dockerfile:3`.", evidence)

    assert result["all_verified"] is True
    assert result["total_citations"] == 1


def test_verify_citations_flags_an_unknown_extensionless_file_is_not_extracted():
    # A path that isn't in the inventory can't be matched by the
    # inventory-built pattern at all, so it stays out of the count rather
    # than being reported as a failure - the honest outcome is "we saw no
    # citation here", not "we checked and it failed".
    result = verify_citations("See `Jenkinsfile:4`.", make_evidence())

    assert result["total_citations"] == 0


def _repo_with_evidence(tmp_path, files: dict[str, str]):
    repo_path = tmp_path / "repo"
    (repo_path / ".aletheore").mkdir(parents=True)
    for name, content in files.items():
        (repo_path / name).write_text(content)
    evidence = {
        "aletheore_version": EVIDENCE_VERSION,
        "repository": {"modules": [{"path": p} for p in files]},
    }
    (repo_path / ".aletheore" / "air.json").write_text(json.dumps(evidence))
    return repo_path


def test_local_line_count_fetcher_counts_real_lines_and_rejects_escapes(tmp_path):
    repo_path = _repo_with_evidence(tmp_path, {"app.py": "one\ntwo\nthree\n"})
    fetch = local_line_count_fetcher(repo_path)

    assert fetch("app.py") == 3
    assert fetch("nope.py") is None
    # A path escape must never be read, and must degrade to "skip the bounds
    # check" rather than to a false "unverified".
    assert fetch("../../../../etc/passwd") is None


def test_load_verifiable_evidence_rejects_evidence_with_no_file_inventory(tmp_path):
    repo_path = tmp_path / "repo"
    (repo_path / ".aletheore").mkdir(parents=True)
    (repo_path / ".aletheore" / "air.json").write_text(json.dumps({"managed_evidence": True}))

    assert load_verifiable_evidence(repo_path) is None


def test_load_verifiable_evidence_rejects_an_incompatible_schema_version(tmp_path):
    repo_path = tmp_path / "repo"
    (repo_path / ".aletheore").mkdir(parents=True)
    evidence = {
        "aletheore_version": "9.9.9",
        "repository": {"modules": [{"path": "app.py"}]},
    }
    (repo_path / ".aletheore" / "air.json").write_text(json.dumps(evidence))

    assert load_verifiable_evidence(repo_path) is None


def test_citation_verification_section_reports_verified_and_unverified(tmp_path):
    repo_path = _repo_with_evidence(tmp_path, {"app.py": "one\ntwo\nthree\n"})

    section = citation_verification_section(
        "The bug is at `app.py:2`, also see `ghost.py:1`.", repo_path
    )

    assert "Citation Verification" in section
    assert "1 of 2" in section
    assert "1 citation(s) could not be verified" in section
    assert "`ghost.py:1`" in section


def test_citation_verification_section_unavailable_without_file_inventory(tmp_path):
    repo_path = tmp_path / "repo"
    (repo_path / ".aletheore").mkdir(parents=True)
    (repo_path / ".aletheore" / "air.json").write_text(json.dumps({"managed_evidence": True}))

    section = citation_verification_section("See `app.py:2`.", repo_path)

    assert "Not available for this run" in section
    assert "could not be verified" not in section
