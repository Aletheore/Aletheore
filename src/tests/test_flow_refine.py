import json
from unittest.mock import MagicMock, patch

from aletheore.flow import refine
from aletheore.flow.refine import cwes_from_metadata, refine_semgrep_results
from aletheore.static_analysis.semgrep_scanner import _relative_path, check_semgrep

JAVA = """import javax.servlet.http.HttpServletRequest;
public class T {
    static void sink(Object o) {}
    void safe(HttpServletRequest request) throws Exception {
        String p = request.getParameter("p");
        String x = "const";
        sink(x);
    }
    void tainted(HttpServletRequest request) throws Exception {
        String p = request.getParameter("p");
        String x = p;
        sink(x);
    }
}
"""


def _item(tmp_path, call_line, cwe="CWE-78: OS Command Injection", path="T.java", rule="r.rule"):
    line_text = JAVA.splitlines()[call_line - 1]
    col = line_text.index("sink(") + 1
    return {
        "check_id": rule,
        "path": str(tmp_path / path),
        "start": {"line": call_line, "col": col},
        "end": {"line": call_line, "col": col + len("sink(x)")},
        "extra": {"severity": "WARNING", "message": "m", "metadata": {"cwe": [cwe]}},
    }


def test_cwes_from_metadata_reads_strings_lists_and_garbage():
    assert cwes_from_metadata({"cwe": "CWE-89: SQLi"}) == {89}
    assert cwes_from_metadata({"cwe": ["CWE-79: XSS", "CWE-80: x"]}) == {79, 80}
    assert cwes_from_metadata({"cwe": None}) == set()
    assert cwes_from_metadata(None) == set()
    assert cwes_from_metadata("nonsense") == set()


def test_provably_safe_finding_is_suppressed_and_a_tainted_one_is_kept(tmp_path):
    (tmp_path / "T.java").write_text(JAVA)
    safe, tainted = _item(tmp_path, 7), _item(tmp_path, 12)

    kept, suppressed = refine_semgrep_results(tmp_path, [safe, tainted], _relative_path)

    assert kept == [tainted]
    assert len(suppressed) == 1
    assert suppressed[0]["path"] == "T.java" and suppressed[0]["line"] == 7
    assert suppressed[0]["cwe"] == ["CWE-78"] and suppressed[0]["reason"]


def test_findings_the_engine_does_not_cover_are_left_alone(tmp_path):
    (tmp_path / "T.java").write_text(JAVA)
    (tmp_path / "a.py").write_text("x = 1\n")
    no_flow_class = _item(tmp_path, 7, cwe="CWE-327: Broken crypto")  # not a data-flow weakness
    xxe_config = _item(tmp_path, 7, cwe="CWE-611: XML External Entity")  # the constant setting is the flaw
    not_java = _item(tmp_path, 7, path="a.py")
    no_cwe = dict(_item(tmp_path, 7), extra={"severity": "WARNING", "metadata": {}})

    kept, suppressed = refine_semgrep_results(tmp_path, [no_flow_class, xxe_config, not_java, no_cwe], _relative_path)

    assert kept == [no_flow_class, xxe_config, not_java, no_cwe] and suppressed == []


def test_refinement_can_be_switched_off(tmp_path, monkeypatch):
    (tmp_path / "T.java").write_text(JAVA)
    monkeypatch.setenv("ALETHEORE_DISABLE_FLOW_REFINEMENT", "1")
    item = _item(tmp_path, 7)

    kept, suppressed = refine_semgrep_results(tmp_path, [item], _relative_path)

    assert kept == [item] and suppressed == []


def test_an_engine_failure_keeps_the_finding(tmp_path):
    (tmp_path / "T.java").write_text(JAVA)
    item = _item(tmp_path, 7)

    with patch.object(refine, "validate_finding", side_effect=RuntimeError("boom")):
        kept, suppressed = refine_semgrep_results(tmp_path, [item], _relative_path)

    assert kept == [item] and suppressed == []


def test_check_semgrep_drops_the_suppressed_finding_and_reports_it(tmp_path):
    (tmp_path / "T.java").write_text(JAVA)
    payload = {"results": [_item(tmp_path, 7), _item(tmp_path, 12)]}
    result = MagicMock(returncode=1, stdout=json.dumps(payload), stderr="")

    with patch("aletheore.static_analysis.semgrep_scanner.shutil.which", return_value="/usr/bin/semgrep"), \
         patch("aletheore.static_analysis.semgrep_scanner.subprocess.run", return_value=result):
        out = check_semgrep(tmp_path)

    assert [f["line"] for f in out["findings"]] == [12]
    assert [s["line"] for s in out["suppressed"]] == [7]
