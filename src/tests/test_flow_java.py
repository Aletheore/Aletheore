"""Soundness and precision tests for the Java flow refinement.

Each case is a method body with a `sink(...)` call. `droppable` means the engine proves the sink
value safe (or the sink unreachable) so the finding may be dropped; everything else must be kept.
The "must keep" cases are the important ones: a wrong drop is a missed vulnerability.
"""

import re
import textwrap

import pytest

from aletheore.flow.java_index import JavaIndex
from aletheore.flow.validate import validate_finding

HEADER = """
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.Cookie;
import java.util.*;
public class T {
    static void sink(Object o) {}
%(extra)s
    void m(HttpServletRequest request, String arg0, int arg1) throws Exception {
        String p = request.getParameter("p");
%(body)s
    }
}
"""


def verdict(tmp_path, body, extra="", cwes=frozenset({78}), sink="sink(x)"):
    src = HEADER % {"body": textwrap.indent(textwrap.dedent(body).strip("\n"), "        "), "extra": textwrap.indent(textwrap.dedent(extra), "    ")}
    (tmp_path / "T.java").write_text(src)
    start = src.index(sink)
    line = src.count("\n", 0, start) + 1
    col = start - (src.rfind("\n", 0, start) + 1) + 1
    end = start + len(sink)
    eline = src.count("\n", 0, end) + 1
    ecol = end - (src.rfind("\n", 0, end) + 1) + 1
    return validate_finding(tmp_path, "T.java", (line, col), (eline, ecol), set(cwes), JavaIndex(tmp_path)).kind


DROP = ("safe", "unreachable")

SAFE_CASES = {
    "constant": 'String x = "ls";\nsink(x);',
    "overwritten": 'String x = p;\nx = "const";\nsink(x);',
    "dead ternary arm": 'int n = 106;\nString x = (7 * 18) + n > 200 ? "safe" : p;\nsink(x);',
    "dead switch arms": 'String g = "ABC";\nchar c = g.charAt(1);\nString x;\nswitch (c) {\n case \'A\': x = p; break;\n case \'B\': x = "bob"; break;\n default: x = p;\n}\nsink(x);',
    "list: tainted element removed": 'List<String> l = new ArrayList<String>();\nl.add("safe");\nl.add(p);\nl.add("more");\nl.remove(0);\nString x = l.get(1);\nsink(x);',
    "map: safe key read": 'Map<String,Object> m = new HashMap<String,Object>();\nm.put("a", "safe");\nm.put("b", p);\nString x = (String) m.get("a");\nsink(x);',
    "helper returns constant": 'String x = h(p);\nsink(x);',
    "sink in dead branch": 'String x = p;\nif (false) {\n sink(x);\n}',
    "encoder matching the sink class": 'String x = org.owasp.esapi.ESAPI.encoder().encodeForOS(null, p);\nsink(x);',
    "parsed number": 'String x = String.valueOf(Integer.parseInt(p));\nsink(x);',
    "both branches constant": 'String x;\nif (p.length() > 3) { x = "a"; } else { x = "b"; }\nsink(x);',
    "string builder of constants": 'StringBuilder b = new StringBuilder("a");\nb.append("b");\nString x = b.toString();\nsink(x);',
    "cast helper object": 'String x = new Wrapper().fixed(p);\nsink(x);',
}
SAFE_EXTRA = {
    "helper returns constant": 'String h(String s) { return "k"; }',
    "cast helper object": 'static class Wrapper { String fixed(String s) { return "const"; } }',
}

KEEP_CASES = {
    "direct request value": 'String x = p;\nsink(x);',
    "live ternary arm": 'int n = 106;\nString x = (7 * 42) - n > 200 ? "safe" : p;\nsink(x);',
    "live switch arm": 'String g = "ABC";\nchar c = g.charAt(0);\nString x;\nswitch (c) {\n case \'A\': x = p; break;\n default: x = "bob";\n}\nsink(x);',
    "switch on unknown selector": 'String x;\nswitch (arg1) {\n case 1: x = p; break;\n default: x = "bob";\n}\nsink(x);',
    "list: tainted element read": 'List<String> l = new ArrayList<String>();\nl.add("safe");\nl.add(p);\nString x = l.get(1);\nsink(x);',
    "list: tainted element kept after other removal": 'List<String> l = new ArrayList<String>();\nl.add(p);\nl.add("safe");\nl.remove(1);\nString x = l.get(0);\nsink(x);',
    "list mutated under a condition": 'List<String> l = new ArrayList<String>();\nl.add("safe");\nif (arg1 > 3) { l.add(p); }\nString x = l.get(0);\nsink(x);',
    "list removal under a condition": 'List<String> l = new ArrayList<String>();\nl.add(p);\nif (arg1 > 3) { l.remove(0); }\nString x = l.size() > 0 ? l.get(0) : "none";\nsink(x);',
    "list passed to an unknown method": 'List<String> l = new ArrayList<String>();\nl.add("safe");\nmystery(l, p);\nString x = l.get(0);\nsink(x);',
    "map tainted key read": 'Map<String,Object> m = new HashMap<String,Object>();\nm.put("a", "safe");\nm.put("b", p);\nString x = (String) m.get("b");\nsink(x);',
    "map put under condition": 'Map<String,Object> m = new HashMap<String,Object>();\nm.put("a", "safe");\nif (arg1 > 1) { m.put("a", p); }\nString x = (String) m.get("a");\nsink(x);',
    "helper returns its argument": 'String x = h(p);\nsink(x);',
    "helper chain returns its argument": 'String x = h(h(p));\nsink(x);',
    "wrong encoder for the sink class": 'String x = org.owasp.esapi.ESAPI.encoder().encodeForHTML(p);\nsink(x);',
    "string builder with tainted append": 'StringBuilder b = new StringBuilder("a");\nb.append(p);\nString x = b.toString();\nsink(x);',
    "string builder replace with tainted": 'StringBuilder b = new StringBuilder("abc");\nb.replace(0, 1, p);\nString x = b.toString();\nsink(x);',
    "loop over request cookies": 'String x = "none";\nfor (Cookie c : request.getCookies()) { x = c.getValue(); }\nsink(x);',
    "tainted in try, constant in catch": 'String x;\ntry { x = p; } catch (Exception e) { x = "c"; }\nsink(x);',
    "method parameter": 'String x = arg0;\nsink(x);',
    "unknown library chain": 'String x = org.springframework.web.context.request.RequestContextHolder.currentRequestAttributes().getRequest().getParameter("a");\nsink(x);',
    "field set elsewhere": 'String x = this.field;\nsink(x);',
    "array with tainted element": 'String[] x = {"sh", "-c", "echo " + p};\nsink(x);',
    "concatenation with request value": 'String x = "ls " + p;\nsink(x);',
    "through a library decode": 'String x = java.net.URLDecoder.decode(p, "UTF-8");\nsink(x);',
    "through a request wrapper class": 'String x = new Wrapper(request).get("a");\nsink(x);',
    "assignment under a condition": 'String x = "c";\nif (arg1 > 2) { x = p; }\nsink(x);',
    "while loop reassigns": 'String x = "c";\nint i = 0;\nwhile (i < arg1) { x = p; i++; }\nsink(x);',
    "recursion terminates": 'String x = rec(p);\nsink(x);',
    "request object passed to a helper": 'Object x = request;\nsink(x);',
    "encoder for a different sink class": 'String x = org.owasp.esapi.ESAPI.encoder().encodeForSQL(null, p);\nsink(x);',
}
KEEP_EXTRA = {
    "helper returns its argument": 'String h(String s) { return s; }',
    "helper chain returns its argument": 'String h(String s) { String r = s; return r; }',
    "through a request wrapper class": 'static class Wrapper { HttpServletRequest r; Wrapper(HttpServletRequest r) { this.r = r; } String get(String k) { return r.getParameter(k); } }',
    "recursion terminates": 'String rec(String s) { return rec(s); }',
    "field set elsewhere": 'String field;',
}


def test_the_right_encoder_for_the_sink_class_is_trusted(tmp_path):
    body = 'String x = org.owasp.esapi.ESAPI.encoder().encodeForSQL(null, p);\nsink(x);'
    assert verdict(tmp_path, body, cwes={89}) in DROP


@pytest.mark.parametrize("name", sorted(SAFE_CASES))
def test_provably_safe_flows_are_dropped(tmp_path, name):
    assert verdict(tmp_path, SAFE_CASES[name], SAFE_EXTRA.get(name, "")) in DROP, name


@pytest.mark.parametrize("name", sorted(KEEP_CASES))
def test_possibly_tainted_flows_are_never_dropped(tmp_path, name):
    got = verdict(tmp_path, KEEP_CASES[name], KEEP_EXTRA.get(name, ""), cwes={78})
    assert got not in DROP, f"{name}: engine claimed safe ({got}); a real flow would be silently dropped"
