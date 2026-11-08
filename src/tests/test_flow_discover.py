"""Discover mode: tainted flows into sinks are found (across files), and the near misses that
produced false positives on real projects are not reported."""

import pytest

from aletheore.flow.discover import discover_repo
from aletheore.static_analysis import _drop_duplicate_flow_findings
from aletheore.static_analysis.flow_scanner import check_flow

CONTROLLER = """
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.sql.*;
import java.io.*;
import java.nio.file.*;
public class C {
%s
}
"""


def scan(tmp_path, body, extra_files=None):
    (tmp_path / "C.java").write_text(CONTROLLER % body)
    for name, text in (extra_files or {}).items():
        (tmp_path / name).write_text(text)
    return discover_repo(tmp_path)


def kinds(findings):
    return sorted({f["rule_id"].split(".")[-1] for f in findings})


def test_sql_injection_through_a_helper_in_another_file_is_found_at_the_sink(tmp_path):
    body = '''
    void get(HttpServletRequest request, Connection conn) throws Exception {
        String name = request.getParameter("n");
        new Dao().find(conn, "name = '" + name + "'");
    }'''
    dao = '''
import java.sql.*;
class Dao {
    void find(Connection conn, String where) throws Exception {
        Statement s = conn.createStatement();
        s.executeQuery("SELECT * FROM t WHERE " + where);
    }
}'''
    found = scan(tmp_path, body, {"Dao.java": dao})
    assert kinds(found) == ["sql-injection"]
    assert found[0]["path"] == "Dao.java" and found[0]["cwe"] == ["CWE-89"]


@pytest.mark.parametrize("name, body", {
    "constant query": 'void f(HttpServletRequest r, Statement s) throws Exception { s.executeQuery("select 1"); }',
    "encoder for the wrong class": 'void f(HttpServletRequest r, Statement s) throws Exception { String x = org.owasp.esapi.ESAPI.encoder().encodeForHTML(r.getParameter("a")); s.executeQuery("select " + x); }',
    "number parsed first": 'void f(HttpServletRequest r, Statement s) throws Exception { int id = Integer.parseInt(r.getParameter("a")); s.executeQuery("select " + id); }',
    "parameter bound, not concatenated": 'void f(HttpServletRequest r, Connection c) throws Exception { PreparedStatement p = c.prepareStatement("select ? "); p.setString(1, r.getParameter("a")); }',
}.items())
def test_safe_sql_shapes_are_not_reported(tmp_path, name, body):
    # encodeForHTML is the wrong encoder for SQL, so that case IS reported: assert accordingly.
    found = scan(tmp_path, body)
    if name == "encoder for the wrong class":
        assert kinds(found) == ["sql-injection"]
    else:
        assert found == [], name


def test_a_tainted_path_is_reported_where_it_is_used_not_where_it_is_built(tmp_path):
    body = '''
    void a(HttpServletRequest r) throws Exception { new File(r.getParameter("p")).getName(); }
    void b(HttpServletRequest r) throws Exception { Paths.get(r.getParameter("p")); }
    void c(HttpServletRequest r) throws Exception { new FileInputStream(r.getParameter("p")); }
    void d(HttpServletRequest r) throws Exception { File f = new File(r.getParameter("p")); f.delete(); }
    void e(HttpServletRequest r) throws Exception { Files.readAllBytes(Paths.get(r.getParameter("p"))); }'''
    lines = sorted(f["line"] for f in scan(tmp_path, body))
    src = (tmp_path / "C.java").read_text().splitlines()
    flagged = [src[n - 1].strip().split("(")[0] for n in lines]
    assert flagged == ["void c", "void d", "void e"], flagged  # a and b only build a path


def test_file_content_is_not_mistaken_for_a_path(tmp_path):
    body = 'void f(HttpServletRequest r) throws Exception { Files.write(Paths.get("/etc/app.conf"), r.getParameter("x").getBytes()); }'
    assert scan(tmp_path, body) == []


def test_ldap_and_execute_need_a_known_receiver_type(tmp_path):
    body = '''
    void a(HttpServletRequest r, Svc service) throws Exception { service.search("u", r.getParameter("t")); }
    void b(HttpServletRequest r, Svc service) throws Exception { service.execute(r.getParameter("t")); }
    void c(HttpServletRequest r, javax.naming.directory.DirContext ctx) throws Exception { ctx.search("o=x", "(uid=" + r.getParameter("u") + ")", null); }'''
    found = scan(tmp_path, body, {"Svc.java": "class Svc { void search(String a, String b) {} void execute(String s) {} }"})
    assert kinds(found) == ["ldap-injection"] and len(found) == 1


def test_trust_boundary_needs_a_tainted_string_not_a_bound_object(tmp_path):
    body = '''
    void a(HttpServletRequest r, javax.servlet.http.HttpSession s) { s.setAttribute("user", r.getParameter("u")); }
    void b(@ModelAttribute Account acct, javax.servlet.http.HttpSession s) { s.setAttribute("acct", acct); }'''
    found = scan(tmp_path, body)
    assert len(found) == 1 and found[0]["rule_id"].endswith("trust-boundary")


def test_annotated_request_parameters_are_sources(tmp_path):
    body = 'void f(@RequestParam("q") String q, Statement s) throws Exception { s.executeQuery("select " + q); }'
    assert kinds(scan(tmp_path, body)) == ["sql-injection"]


def test_a_request_wrapper_class_in_another_file_is_followed(tmp_path):
    body = '''
    void f(HttpServletRequest request, Statement s) throws Exception {
        Wrapper w = new Wrapper(request);
        s.executeQuery("select " + w.get("a"));
    }'''
    wrapper = '''
import javax.servlet.http.HttpServletRequest;
class Wrapper { HttpServletRequest r; Wrapper(HttpServletRequest r) { this.r = r; } String get(String k) { return r.getParameter(k); } }'''
    assert kinds(scan(tmp_path, body, {"Wrapper.java": wrapper})) == ["sql-injection"]


def test_check_flow_reports_not_applicable_without_java_and_can_be_disabled(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("nothing to analyze\n")
    assert check_flow(tmp_path)["not_applicable"] == "no Java, Python, JavaScript, PHP, Go, C#, Ruby, Kotlin or Rust source files"
    monkeypatch.setenv("ALETHEORE_DISABLE_FLOW_DISCOVERY", "1")
    assert check_flow(tmp_path)["checked"] is False


def test_check_flow_findings_have_the_scanner_finding_shape(tmp_path):
    scan(tmp_path, 'void f(HttpServletRequest r, Statement s) throws Exception { s.executeQuery("a" + r.getParameter("x")); }')
    result = check_flow(tmp_path)
    f = result["findings"][0]
    assert result["checked"] is True
    assert {"tool", "rule_id", "severity", "type", "path", "line", "message", "cwe"} <= set(f)
    assert f["tool"] == "aletheore-flow" and f["type"] == "vulnerability" and f["cwe"] == ["CWE-89"]


def test_flow_findings_that_semgrep_already_reported_are_dropped():
    semgrep = {"tool": "semgrep", "rule_id": "r", "path": "A.java", "line": 10, "cwe": ["CWE-89"]}
    same = {"tool": "aletheore-flow", "rule_id": "flow.java.sql-injection", "path": "A.java", "line": 12, "cwe": ["CWE-89"]}
    other_class = {"tool": "aletheore-flow", "rule_id": "flow.java.xss", "path": "A.java", "line": 11, "cwe": ["CWE-79"]}
    far = {"tool": "aletheore-flow", "rule_id": "flow.java.sql-injection", "path": "A.java", "line": 40, "cwe": ["CWE-89"]}

    kept = _drop_duplicate_flow_findings([semgrep, same, other_class, far])

    assert kept == [semgrep, other_class, far]


def test_sink_beyond_the_old_prefilter_cap_is_still_found(tmp_path):
    from aletheore.flow.discover_py import discover_python
    from aletheore.flow.discover import discover_repo
    body = "# padding\n" * 250_000 + "from flask import request\nimport os\ndef h():\n    os.system(request.args.get('c'))\n"
    (tmp_path / "big.py").write_text(body)
    assert len(body) > 2_000_000
    assert any("command-injection" in f["rule_id"] for f in discover_repo(tmp_path))


def test_budget_exhaustion_is_reported_not_silent(tmp_path, monkeypatch):
    from aletheore.flow.py_interp import BudgetExceeded, PyInterp
    from aletheore.static_analysis.flow_scanner import check_flow

    def boom(self):
        raise BudgetExceeded
    monkeypatch.setattr(PyInterp, "_tick", boom)
    (tmp_path / "a.py").write_text("from flask import request\nimport os\n\ndef h():\n    a = 1\n    b = 2\n    c = 3\n    os.system(request.args.get('c'))\n")
    result = check_flow(tmp_path)
    assert result.get("incomplete_files") == ["a.py"]


def test_go_split_on_empty_separator_does_not_crash(tmp_path):
    from aletheore.flow.discover import discover_repo
    (tmp_path / "m.go").write_text('package main\nimport ("net/http"; "strings"; "os")\nfunc h(w http.ResponseWriter, r *http.Request) {\n  parts := strings.Split("abc", "")\n  os.Open(r.FormValue("f") + parts[0])\n}\n')
    assert any("path-traversal" in f["rule_id"] for f in discover_repo(tmp_path))


def test_an_unexpected_error_in_one_entry_point_does_not_lose_the_scan(tmp_path, monkeypatch):
    from aletheore.flow.py_interp import PyInterp
    from aletheore.static_analysis.flow_scanner import check_flow
    real = PyInterp.exec

    def flaky(self, n, fr):
        if n is not None and n.type == "block" and b"boom" in n.text:
            raise ValueError("an unmodelled construct")
        return real(self, n, fr)
    monkeypatch.setattr(PyInterp, "exec", flaky)
    (tmp_path / "a.py").write_text("from flask import request\nimport os\n\ndef bad():\n    boom = 1\n\ndef good():\n    os.system(request.args.get('c'))\n")
    result = check_flow(tmp_path)
    assert any("command-injection" in f["rule_id"] for f in result["findings"])
    assert result.get("incomplete_files") == ["a.py"]
