"""Execute dashboard renderers: an unmeasured result must never look clean."""

import json
import re
import shutil
import subprocess

import pytest

from aletheore.dashboard import DASHBOARD_HTML, build_app, build_evidence_summary
from starlette.testclient import TestClient
from tests.test_dashboard import make_evidence


def render(function, data):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed to execute the dashboard JavaScript")
    names = [
        "escapeHtml", "severityLabel", "checkStatus", "checkSummary", "checkDetail", "checkNotice",
        "renderSecurity", "renderVulnerabilities", "renderLicenses", "renderEndpoints", "renderStaticAnalysis",
    ]
    functions = []
    for name in names:
        match = re.search(r"function " + name + r"\([^\n]*\) \{.*?\n\}", DASHBOARD_HTML, re.DOTALL)
        assert match, name
        functions.append(match[0])
    script = """const elements = {};
const document = {getElementById(id) {return elements[id] ||= {textContent: '', innerHTML: ''};}};
const STATIC_ANALYSIS_SEVERITY_ORDER = {blocker: 0, critical: 1, major: 2, minor: 3, info: 4};
"""
    script += "\n".join(functions)
    script += f"\n{function}({json.dumps(data)});\nconsole.log(JSON.stringify(elements));"
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=10, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize("name,prefix,extra", [
    ("renderVulnerabilities", "vulnerabilities", {}),
    ("renderLicenses", "licenses", {"repo_license": {"category": "unknown"}}),
    ("renderEndpoints", "endpoints", {"endpoints": []}),
    ("renderStaticAnalysis", "static-analysis", {"tools_run": [], "tools_skipped": []}),
])
@pytest.mark.parametrize("reason", ["skipped by configuration", "could not read manifest: <script>alert(1)</script>", None])
def test_unrun_checks_have_no_clean_or_zero_claim(name, prefix, extra, reason):
    result = render(name, {"checked": False, "reason": reason, "findings": [], **extra})
    assert result[prefix + "-summary"]["textContent"] == "Not checked"
    body = result[prefix]["innerHTML"]
    assert body
    assert "<script>" not in body
    for misleading in ("No known vulnerabilities", "permissive license", "No API endpoints", "No findings"):
        assert misleading not in body


@pytest.mark.parametrize("name,prefix,extra", [
    ("renderVulnerabilities", "vulnerabilities", {}),
    ("renderLicenses", "licenses", {"repo_license": {"category": "MIT"}}),
    ("renderEndpoints", "endpoints", {"endpoints": []}),
    ("renderStaticAnalysis", "static-analysis", {"tools_run": ["check"], "tools_skipped": []}),
])
def test_completed_empty_checks_can_report_zero(name, prefix, extra):
    result = render(name, {"checked": True, "findings": [], **extra})
    assert result[prefix + "-summary"]["textContent"].startswith("0 ")
    assert "Not checked" not in result[prefix]["innerHTML"]


def test_static_analysis_wrapper_with_no_completed_checks_is_not_checked():
    result = render("renderStaticAnalysis", {
        "checked": True, "findings": [], "tools_run": [],
        "tools_skipped": [{"tool": "example", "reason": "unavailable"}],
    })
    assert result["static-analysis-summary"]["textContent"] == "Not checked"
    assert "0 of 1 checks ran" in result["static-analysis-coverage"]["innerHTML"]


def test_static_analysis_partial_coverage_is_explicit_with_no_findings():
    result = render("renderStaticAnalysis", {
        "checked": True, "findings": [], "tools_run": ["completed"],
        "tools_skipped": [{"tool": "other", "reason": "unavailable"}],
    })
    assert "limited coverage" in result["static-analysis-summary"]["textContent"]
    assert "results cover completed checks only" in result["static-analysis"]["innerHTML"]
    assert "1 of 2 checks ran" in result["static-analysis-coverage"]["innerHTML"]


def test_unfinished_check_keeps_available_findings_visible():
    result = render("renderVulnerabilities", {
        "checked": False, "reason": "lookup unavailable",
        "findings": [{"package": "example", "installed_version": "1", "advisory_id": "EXAMPLE-1"}],
    })
    assert result["vulnerabilities-summary"]["textContent"] == "Not checked"
    assert "lookup unavailable" in result["vulnerabilities"]["innerHTML"]
    assert "EXAMPLE-1" in result["vulnerabilities"]["innerHTML"]


def test_partial_checks_keep_findings_and_escape_coverage_notes():
    result = render("renderVulnerabilities", {
        "checked": True, "note": "Only <b>some</b> manifests checked",
        "unreadable_manifest_dirs": ["private"],
        "findings": [{"package": "example", "installed_version": "1", "advisory_id": "EXAMPLE-1"}],
    })
    assert "limited coverage" in result["vulnerabilities-summary"]["textContent"]
    body = result["vulnerabilities"]["innerHTML"]
    assert "EXAMPLE-1" in body
    assert "&lt;b&gt;some&lt;/b&gt;" in body
    assert "1 manifest directories could not be read" in body


def test_security_overview_and_secret_history_do_not_fake_measurements():
    result = render("renderSecurity", {
        "secrets": {"real_findings": 0, "total_findings": 0, "history_findings": 0, "history_scanned_commits": 0},
        "vulnerabilities": {"checked": False, "finding_count": 0},
    })
    assert "No commits scanned" in result["security"]["innerHTML"]
    assert "Vulnerabilities</span><span>Not checked" in result["security"]["innerHTML"]


def test_secret_history_timeout_and_partial_vulnerabilities_remain_visible():
    result = render("renderSecurity", {
        "secrets": {"real_findings": 0, "total_findings": 0, "history_findings": 2,
                    "history_scanned_commits": 5, "history_scan_timed_out": True},
        "vulnerabilities": {"checked": True, "finding_count": 1, "note": "Only some manifests checked"},
    })
    body = result["security"]["innerHTML"]
    assert "2 in 5 scanned commits (limited coverage: timed out)" in body
    assert "1 (limited coverage)" in body


def test_summary_keeps_coverage_and_static_skip_details():
    evidence = make_evidence("2026-10-26T12:00:00+00:00")
    for name in ("dependency_vulnerabilities", "dependency_licenses"):
        evidence["security"][name].update(note="Limited manifests", unreadable_manifest_dirs=["nested"])
    evidence["security"]["static_analysis"].update(checked=False, reason="disabled")
    summary = build_evidence_summary(evidence)
    for name in ("vulnerabilities", "licenses"):
        assert summary["security"][name]["note"] == "Limited manifests"
        assert summary["security"][name]["unreadable_manifest_dirs"] == ["nested"]
    assert summary["security"]["static_analysis"]["reason"] == "disabled"


def test_only_demo_marker_enables_sample_guide(tmp_path, monkeypatch):
    from aletheore.demo import DEMO_MARKER

    monkeypatch.setattr("aletheore.dashboard.read_evidence", lambda _: make_evidence("2026-10-26T12:00:00+00:00"))
    client = TestClient(build_app(tmp_path), base_url="http://127.0.0.1")
    assert client.get("/api/evidence").json()["demo"] is False
    (tmp_path / ".aletheore").mkdir()
    marker = tmp_path / ".aletheore" / "demo"
    marker.write_text("not an Aletheore demo")
    assert client.get("/api/evidence").json()["demo"] is False
    marker.write_text(DEMO_MARKER)
    assert client.get("/api/evidence").json()["demo"] is True
