"""Regressions for defects found by independent adversarial testing: sinks that were missing, values that did not flow
through constructors, closures, out parameters or aliases, and validating guards that were ignored."""

import pytest

from aletheore.flow.discover import discover_repo


def run(tmp_path, name, src):
    (tmp_path / name).write_text(src)
    return sorted({f["rule_id"].split(".")[-1] for f in discover_repo(tmp_path)})


JAVA = "import javax.servlet.http.*;\nimport java.util.*;\nimport java.util.regex.*;\npublic class S extends HttpServlet {\n%s\n}\n"


def java(body, extra=""):
    return JAVA % (extra + "\n  protected void doGet(HttpServletRequest req, HttpServletResponse resp) throws Exception {\n" + body + "\n  }")


JAVA_VULN = {
    "redirect": ('resp.sendRedirect(req.getParameter("n"));', "open-redirect"),
    "ssrf url": ('new java.net.URL(req.getParameter("u")).openConnection();', "ssrf"),
    "script eval": ('javax.script.ScriptEngine e = null; e.eval("x=" + req.getParameter("r"));', "code-injection"),
    "deserialize": ('new java.io.ObjectInputStream(req.getInputStream());', "insecure-deserialization"),
    "lambda thread": ('String f = req.getParameter("f"); new Thread(() -> { new java.io.File("/t/" + f).delete(); new java.io.FileInputStream("/t/" + f); }).start();', "path-traversal"),
    "constructor set field": ('go(req.getParameter("q"));', "sql-injection"),
}


@pytest.mark.parametrize("case", sorted(JAVA_VULN))
def test_java_vulnerable(tmp_path, case):
    body, kind = JAVA_VULN[case]
    extra = ""
    if case == "constructor set field":
        extra = "  private Db db;\n  public S() { db = new Db(); }\n  void go(String q) throws Exception { db.run(\"select \" + q); }\n  static class Db { void run(String q) throws Exception { java.sql.DriverManager.getConnection(\"x\").createStatement().executeQuery(q); } }"
    assert kind in run(tmp_path, "S.java", java(body, extra))


JAVA_SAFE = {
    "regex allowlist": ('String c = req.getParameter("c"); if (!V.matcher(c).matches()) { return; } java.sql.DriverManager.getConnection("x").createStatement().executeQuery("select " + c);', "  static final Pattern V = Pattern.compile(\"^[A-Z]{2}-[0-9]{1,4}$\");"),
    "path tokens excluded": ('String n = req.getParameter("n"); if (n.contains("..") || n.contains("/") || n.contains("\\\\")) { return; } new java.io.FileInputStream("/srv/" + n);', ""),
    "constant program no shell": ('new ProcessBuilder("convert", req.getParameter("a"), "out.png").start();', ""),
    "list allowlist": ('String t = req.getParameter("t"); if (!Arrays.asList("a", "b").contains(t)) { return; } java.sql.DriverManager.getConnection("x").createStatement().executeQuery("select " + t);', ""),
}


@pytest.mark.parametrize("case", sorted(JAVA_SAFE))
def test_java_safe(tmp_path, case):
    body, extra = JAVA_SAFE[case]
    assert run(tmp_path, "S.java", java(body, extra)) == []


def test_java_regex_with_a_quote_is_not_a_guard(tmp_path):
    body = 'String c = req.getParameter("c"); if (!c.matches("^[A-Za-z\' ]+$")) { return; } java.sql.DriverManager.getConnection("x").createStatement().executeQuery("select " + c);'
    assert "sql-injection" in run(tmp_path, "S.java", java(body))


def test_csharp_out_param_and_route_data(tmp_path):
    src = ('[ApiController]\npublic class C : ControllerBase {\n  string F(out string l) { l = Request.Query["l"].ToString(); return "ok"; }\n'
           '  [HttpGet("/a")]\n  public IActionResult A() { F(out var l); var c = new SqlCommand("select " + l); return Ok(); }\n'
           '  [HttpGet("/b")]\n  public IActionResult B() { var r = RouteData.Values["c"].ToString(); var c = new SqlCommand("select " + r); return Ok(); }\n}\n')
    assert "sql-injection" in run(tmp_path, "C.cs", src)


def test_csharp_regex_guard_and_fixed_host(tmp_path):
    src = ('[ApiController]\npublic class C : ControllerBase {\n  static readonly Regex V = new Regex("^[A-Z]{2}-[0-9]+$");\n'
           '  [HttpGet("/a")]\n  public IActionResult A() { var c = Request.Query["c"].ToString(); if (!V.IsMatch(c)) { return BadRequest(); } var q = new SqlCommand("select " + c); return Ok(); }\n'
           '  [HttpGet("/b")]\n  public async Task<IActionResult> B() { var k = Request.Query["k"].ToString(); var s = await new HttpClient().GetStringAsync("https://cdn.example.com/" + k); return Ok(); }\n}\n')
    assert run(tmp_path, "C.cs", src) == []


def test_go_builder_const_var_and_guard(tmp_path):
    vuln = ('package main\nimport ("net/http"; "os"; "strings")\nfunc h(w http.ResponseWriter, r *http.Request) {\n  f := r.URL.Query().Get("f")\n'
            '  var b strings.Builder\n  b.WriteString("/srv/")\n  b.WriteString(f)\n  os.ReadFile(b.String())\n}\n')
    assert "path-traversal" in run(tmp_path, "m.go", vuln)
    safe = ('package main\nimport ("database/sql"; "net/http"; "regexp")\nvar db *sql.DB\nvar valid = regexp.MustCompile(`^[A-Z]{2}-[0-9]{1,4}$`)\n'
            'func h(w http.ResponseWriter, r *http.Request) {\n  c := r.FormValue("c")\n  if !valid.MatchString(c) { return }\n  db.Query("select " + c)\n}\n')
    assert run(tmp_path / "s", "m.go", safe) == [] if (tmp_path / "s").mkdir() is None else False


def test_js_closure_response_chain_guard_and_shell(tmp_path):
    src = ('const express = require("express"); const { exec, spawn } = require("child_process"); const app = express();\n'
           'function mk(t) { return () => exec("ping " + t); }\n'
           'app.get("/a", (req, res) => { const r = mk(req.query.h); r(); res.send("x"); });\n'
           'app.get("/b", (req, res) => { res.type("html").send(req.query.m); });\n'
           'app.post("/c", (req, res) => { spawn("sh", ["-c", "convert " + req.body.n]); });\n')
    assert {"command-injection", "xss"} <= set(run(tmp_path, "a.js", src))
    safe = ('const express = require("express"); const { exec } = require("child_process"); const app = express();\n'
            'app.post("/t", (req, res) => { const t = req.body.t; if (t !== "stats" && t !== "quota") { return res.status(400).send("no"); } exec("/usr/bin/admin-" + t); });\n')
    d = tmp_path / "safe"
    d.mkdir()
    assert run(d, "a.js", safe) == []


def test_python_alias_response_inheritance(tmp_path):
    src = ('from flask import request, Response\nfrom subprocess import run as go\nfrom importlib import import_module\n'
           'def a():\n    go("ls " + request.args.get("d"), shell=True)\n'
           'def b():\n    return Response("<p>" + request.args.get("m", "") + "</p>", mimetype="text/html")\n'
           'def c():\n    import_module("t." + request.args.get("t"))\n'
           'class Base:\n    def run(self, cur):\n        cur.execute("select " + self.owner)\n'
           'class Child(Base):\n    def __init__(self):\n        self.owner = request.args.get("o")\n')
    assert {"command-injection", "xss", "code-injection", "sql-injection"} <= set(run(tmp_path, "a.py", src))


def test_php_static_property_bound_execute_and_else_guard(tmp_path):
    vuln = "<?php\nclass C { public static $r; }\nC::$r = $_SERVER['HTTP_X'];\nshell_exec('chown ' . C::$r);\n"
    assert "command-injection" in run(tmp_path, "a.php", vuln)
    d1, d2 = tmp_path / "p", tmp_path / "q"
    d1.mkdir(); d2.mkdir()
    assert run(d1, "a.php", "<?php\n$s = $pdo->prepare('select * from t where n like ?');\n$s->execute(['%' . $_GET['q'] . '%']);\n") == []
    assert run(d2, "a.php", "<?php\n$p = $_GET['p'] ?? 'home';\nif (!in_array($p, ['a', 'b'], true)) { $p = 'a'; }\ninclude 'pages/' . $p . '.php';\n") == []


def test_fixed_host_prefix_is_not_ssrf_but_variable_host_is(tmp_path):
    safe = 'import requests\nfrom flask import request\ndef f():\n    requests.get("https://api.example.com/v1/" + request.args.get("id"))\n'
    assert run(tmp_path, "a.py", safe) == []
    d = tmp_path / "v"
    d.mkdir()
    assert "ssrf" in run(d, "a.py", 'import requests\nfrom flask import request\ndef f():\n    requests.get("https://" + request.args.get("h") + "/x")\n')


def test_fastapi_dependency_and_json_return_are_not_xss(tmp_path):
    src = ('from fastapi import APIRouter, Depends\nrouter = APIRouter()\n\n@router.get("/menu")\ndef get_menu(db=Depends(get_db), q: str = ""):\n'
           '    return db.query(1).all()\n\n@router.get("/echo")\ndef echo(msg: str):\n    return {"m": msg}\n')
    assert run(tmp_path, "a.py", src) == []


def test_laravel_request_and_raw_query(tmp_path):
    src = ("<?php\nclass U { public function login(Request $request) {\n  return DB::table('u')->whereRaw(\"n='\" . $request->input('n') . \"'\")->first();\n } }\n")
    assert "sql-injection" in run(tmp_path, "U.php", src)
    d = tmp_path / "ok"
    d.mkdir()
    assert run(d, "U.php", "<?php\nclass U { public function login(Request $request) {\n  return DB::table('u')->where('n', $request->input('n'))->first();\n } }\n") == []
