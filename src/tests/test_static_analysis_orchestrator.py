from unittest.mock import patch

import aletheore.static_analysis as static_analysis_module
from aletheore.static_analysis import check_static_analysis


def _checked(findings):
    return {"checked": True, "reason": None, "findings": findings}


def _skipped(reason):
    return {"checked": False, "reason": reason, "findings": []}


# _SCANNERS/_OPTIONAL_SCANNERS are tuples of function references captured
# at import time - patching the individual check_X names in the module
# namespace (e.g. "aletheore.static_analysis.check_semgrep") does not
# change what's already inside those tuples, so the tuples themselves have
# to be replaced. check_sonarqube is looked up fresh by name on every call
# instead (see check_static_analysis's own body), which is why that one
# still works patched individually - see the last test below.
def _patch_required_scanners(monkeypatch, semgrep=None, gosec=None, bandit=None, trivy=None, pmd=None):
    monkeypatch.setattr(
        static_analysis_module,
        "_SCANNERS",
        (
            ("semgrep", semgrep or (lambda repo_path: _checked([]))),
            ("gosec", gosec or (lambda repo_path: _checked([]))),
            ("bandit", bandit or (lambda repo_path: _checked([]))),
            ("trivy", trivy or (lambda repo_path: _checked([]))),
            ("pmd", pmd or (lambda repo_path: _checked([]))),
        ),
    )


def _patch_optional_scanners(monkeypatch, bearer=None, joern=None):
    monkeypatch.setattr(
        static_analysis_module,
        "_OPTIONAL_SCANNERS",
        (
            (
                "bearer",
                bearer or (lambda repo_path: _checked([])),
                "skipped (opt-in - pass --check-bearer to include it; useful but "
                "can take significantly longer than the other scanners on a large repo)",
            ),
            (
                "joern",
                joern or (lambda repo_path: _checked([])),
                "skipped (opt-in - pass --check-joern to include it; requires Joern installed "
                "separately, and a CPG build is real per-scan JVM/parsing cost, not a fast "
                "stateless subprocess call like the other scanners here)",
            ),
        ),
    )


def test_check_static_analysis_aggregates_tools_run_and_skipped(tmp_path, monkeypatch):
    semgrep_finding = {"tool": "semgrep", "rule_id": "r1", "severity": "major", "type": "bug", "path": "a.py", "line": 1, "message": "m"}
    bandit_finding = {"tool": "bandit", "rule_id": "B602", "severity": "critical", "type": "vulnerability", "path": "b.py", "line": 2, "message": "m"}
    trivy_finding = {"tool": "trivy", "rule_id": "openai-api-key", "severity": "critical", "type": "privacy", "path": "c.env", "line": 1, "message": "m"}
    pmd_finding = {"tool": "pmd", "rule_id": "NullAssignment", "severity": "major", "type": "bug", "path": "d.java", "line": 3, "message": "m"}

    _patch_required_scanners(
        monkeypatch,
        semgrep=lambda repo_path: _checked([semgrep_finding]),
        bandit=lambda repo_path: _checked([bandit_finding]),
        trivy=lambda repo_path: _checked([trivy_finding]),
        pmd=lambda repo_path: _checked([pmd_finding]),
    )
    _patch_optional_scanners(monkeypatch)

    with patch("aletheore.static_analysis.check_sonarqube", return_value=_skipped("SonarQube not configured (set SONARQUBE_HOST_URL to enable)")):
        # run_bearer/run_joern default to False - both are opt-in (real
        # cost gaps found live: Bearer's non-linear-looking full-repo
        # runtime, Joern's real per-scan CPG-build cost). Trivy/PMD are
        # not opt-in - both in _SCANNERS above, on by default like
        # semgrep/gosec/bandit (real timing data justified each: 2026-09-21).
        result = check_static_analysis(tmp_path)

    assert result["checked"] is True
    assert sorted(result["tools_run"]) == ["bandit", "gosec", "pmd", "semgrep", "trivy"]
    assert result["tools_skipped"] == [
        {
            "tool": "bearer",
            "reason": "skipped (opt-in - pass --check-bearer to include it; useful but "
            "can take significantly longer than the other scanners on a large repo)",
        },
        {
            "tool": "joern",
            "reason": "skipped (opt-in - pass --check-joern to include it; requires Joern installed "
            "separately, and a CPG build is real per-scan JVM/parsing cost, not a fast "
            "stateless subprocess call like the other scanners here)",
        },
        {"tool": "sonarqube", "reason": "SonarQube not configured (set SONARQUBE_HOST_URL to enable)"},
    ]
    assert result["findings"] == [semgrep_finding, bandit_finding, trivy_finding, pmd_finding]


def test_check_static_analysis_runs_bearer_when_opted_in(tmp_path, monkeypatch):
    bearer_finding = {"tool": "bearer", "rule_id": "python_lang_logger", "severity": "minor", "type": "privacy", "path": "a.py", "line": 1, "message": "m"}
    calls = []

    def bearer_scanner(repo_path):
        calls.append(repo_path)
        return _checked([bearer_finding])

    _patch_required_scanners(monkeypatch)
    _patch_optional_scanners(monkeypatch, bearer=bearer_scanner)

    with patch("aletheore.static_analysis.check_sonarqube", return_value=_checked([])):
        result = check_static_analysis(tmp_path, run_bearer=True)

    assert calls == [tmp_path]
    assert "bearer" in result["tools_run"]
    assert bearer_finding in result["findings"]


def test_check_static_analysis_runs_joern_when_opted_in(tmp_path, monkeypatch):
    joern_finding = {"tool": "joern", "rule_id": "asymmetric-cache-trust-go", "severity": "critical", "type": "vulnerability", "path": "a.go", "line": 1, "message": "m"}
    calls = []

    def joern_scanner(repo_path):
        calls.append(repo_path)
        return _checked([joern_finding])

    _patch_required_scanners(monkeypatch)
    _patch_optional_scanners(monkeypatch, joern=joern_scanner)

    with patch("aletheore.static_analysis.check_sonarqube", return_value=_checked([])):
        result = check_static_analysis(tmp_path, run_joern=True)

    assert calls == [tmp_path]
    assert "joern" in result["tools_run"]
    assert joern_finding in result["findings"]


def test_check_static_analysis_survives_a_scanner_raising_unexpectedly(tmp_path, monkeypatch):
    # Real bug found via audit (2026-09-21): nothing guarded scanner(repo_path)
    # against an unexpected exception (as opposed to the graceful
    # {"checked": False, ...} every scanner already returns for its own
    # EXPECTED failure modes) - one scanner's parsing bug on malformed-but-
    # valid tool output used to abort the whole static-analysis pass,
    # dropping every other scanner's real findings along with it.
    semgrep_finding = {"tool": "semgrep", "rule_id": "r1", "severity": "major", "type": "bug", "path": "a.py", "line": 1, "message": "m"}

    def raising_bandit(repo_path):
        raise AttributeError("'NoneType' object has no attribute 'get'")

    _patch_required_scanners(
        monkeypatch,
        semgrep=lambda repo_path: _checked([semgrep_finding]),
        bandit=raising_bandit,
    )
    _patch_optional_scanners(monkeypatch)

    with patch("aletheore.static_analysis.check_sonarqube", return_value=_skipped("SonarQube not configured (set SONARQUBE_HOST_URL to enable)")):
        result = check_static_analysis(tmp_path)

    assert result["checked"] is True
    assert "semgrep" in result["tools_run"]
    assert semgrep_finding in result["findings"]
    assert "bandit" not in result["tools_run"]
    bandit_skip = next(s for s in result["tools_skipped"] if s["tool"] == "bandit")
    assert "AttributeError" in bandit_skip["reason"]


def test_trivy_is_wired_into_the_real_always_on_scanners_tuple():
    # Real regression this guards against: Trivy briefly lived in
    # _OPTIONAL_SCANNERS during development, before real timing data
    # (2026-09-21) justified moving it to always-on like semgrep/gosec/
    # bandit. Every test above patches _SCANNERS away entirely, so none of
    # them would catch Trivy silently sliding back into _OPTIONAL_SCANNERS
    # (or being dropped altogether) - this checks the real, unpatched tuple.
    names = [name for name, _ in static_analysis_module._SCANNERS]
    assert "trivy" in names
    optional_names = [name for name, _, _ in static_analysis_module._OPTIONAL_SCANNERS]
    assert "trivy" not in optional_names


def test_pmd_is_wired_into_the_real_always_on_scanners_tuple():
    # Same regression class as Trivy's own version of this test - real
    # timing data (2.74s/gson, 5.0s/commons-lang, 2026-09-21) justified
    # PMD always-on, not opt-in.
    names = [name for name, _ in static_analysis_module._SCANNERS]
    assert "pmd" in names
    optional_names = [name for name, _, _ in static_analysis_module._OPTIONAL_SCANNERS]
    assert "pmd" not in optional_names


def test_check_static_analysis_passes_sonarqube_host_url_through(tmp_path, monkeypatch):
    _patch_required_scanners(monkeypatch)
    _patch_optional_scanners(monkeypatch)

    with patch("aletheore.static_analysis.check_sonarqube", return_value=_checked([])) as mock_sonarqube:
        check_static_analysis(tmp_path, sonarqube_host_url="http://localhost:9000")

    mock_sonarqube.assert_called_once_with(tmp_path, host_url="http://localhost:9000")


def test_check_static_analysis_adds_a_content_fingerprint_to_findings_with_a_real_line(tmp_path, monkeypatch):
    # Real gap this closes (PR #888): history.py's diffing keyed a
    # static-analysis finding's identity on its exact line number, so an
    # unrelated edit earlier in the same file that shifts every later line
    # down made every finding below it look "new" (and the old line
    # "resolved") even though nothing about the finding itself changed.
    # A content fingerprint - computed here, once, while the scanner's
    # checkout is on disk - gives history.py something to match on instead
    # of the line number. See history.py's _static_analysis_identity.
    (tmp_path / "a.py").write_text("one\ntwo\nthree\nfour\nfive\n")
    finding = {"tool": "semgrep", "rule_id": "r1", "severity": "major", "type": "bug", "path": "a.py", "line": 3, "message": "m"}

    _patch_required_scanners(monkeypatch, semgrep=lambda repo_path: _checked([finding]))
    _patch_optional_scanners(monkeypatch)

    with patch("aletheore.static_analysis.check_sonarqube", return_value=_checked([])):
        result = check_static_analysis(tmp_path)

    assert len(result["findings"]) == 1
    fingerprint = result["findings"][0]["content_fingerprint"]
    assert isinstance(fingerprint, str) and fingerprint


def test_check_static_analysis_fingerprint_is_stable_across_a_line_shift(tmp_path, monkeypatch):
    # The whole point: the SAME finding (same 3-line window of real source
    # around it) at a DIFFERENT line number must fingerprint identically -
    # that's what lets history.py recognize it as unmoved rather than as a
    # brand new finding plus a resolved old one.
    (tmp_path / "a.py").write_text("one\ntwo\nthree\nfour\nfive\n")
    (tmp_path / "b.py").write_text("zero\nzero\none\ntwo\nthree\nfour\nfive\n")
    finding_at_3 = {"tool": "semgrep", "rule_id": "r1", "severity": "major", "type": "bug", "path": "a.py", "line": 3, "message": "m"}
    finding_at_5 = {"tool": "semgrep", "rule_id": "r1", "severity": "major", "type": "bug", "path": "b.py", "line": 5, "message": "m"}

    _patch_required_scanners(monkeypatch, semgrep=lambda repo_path: _checked([finding_at_3, finding_at_5]))
    _patch_optional_scanners(monkeypatch)

    with patch("aletheore.static_analysis.check_sonarqube", return_value=_checked([])):
        result = check_static_analysis(tmp_path)

    fp_at_3, fp_at_5 = (f["content_fingerprint"] for f in result["findings"])
    assert fp_at_3 == fp_at_5


def test_check_static_analysis_fingerprint_differs_for_different_content(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("one\ntwo\nthree\nfour\nfive\n")
    (tmp_path / "b.py").write_text("one\ntwo\nTHREE-DIFFERENT\nfour\nfive\n")
    finding_a = {"tool": "semgrep", "rule_id": "r1", "severity": "major", "type": "bug", "path": "a.py", "line": 3, "message": "m"}
    finding_b = {"tool": "semgrep", "rule_id": "r1", "severity": "major", "type": "bug", "path": "b.py", "line": 3, "message": "m"}

    _patch_required_scanners(monkeypatch, semgrep=lambda repo_path: _checked([finding_a, finding_b]))
    _patch_optional_scanners(monkeypatch)

    with patch("aletheore.static_analysis.check_sonarqube", return_value=_checked([])):
        result = check_static_analysis(tmp_path)

    fp_a, fp_b = (f["content_fingerprint"] for f in result["findings"])
    assert fp_a != fp_b


def test_check_static_analysis_no_fingerprint_when_line_is_not_real(tmp_path, monkeypatch):
    # Same misconfig-finding-with-no-real-line case jobs.py's
    # _static_analysis_annotations already special-cases: nothing to hash
    # against, so no fingerprint is attached, and history.py's identity
    # falls back to the old (tool, rule_id, path, line) behavior for it.
    finding = {"tool": "trivy", "rule_id": "no-healthcheck", "severity": "minor", "type": "misconfig", "path": "Dockerfile", "line": 0, "message": "m"}

    _patch_required_scanners(monkeypatch, trivy=lambda repo_path: _checked([finding]))
    _patch_optional_scanners(monkeypatch)

    with patch("aletheore.static_analysis.check_sonarqube", return_value=_checked([])):
        result = check_static_analysis(tmp_path)

    assert result["findings"][0].get("content_fingerprint") is None


def test_check_static_analysis_no_fingerprint_when_file_is_unreadable(tmp_path, monkeypatch):
    # The finding's own path doesn't exist on disk at all (e.g. a stale
    # finding from a cache, or a path the scanner reported relative to a
    # different root) - must not crash the whole scan over it.
    finding = {"tool": "semgrep", "rule_id": "r1", "severity": "major", "type": "bug", "path": "missing.py", "line": 3, "message": "m"}

    _patch_required_scanners(monkeypatch, semgrep=lambda repo_path: _checked([finding]))
    _patch_optional_scanners(monkeypatch)

    with patch("aletheore.static_analysis.check_sonarqube", return_value=_checked([])):
        result = check_static_analysis(tmp_path)

    assert result["findings"][0].get("content_fingerprint") is None
