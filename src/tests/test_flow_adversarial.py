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


def test_csharp_closure_and_ref_argument(tmp_path):
    src = ('class C { void H(HttpRequest request) {\n  string v = request.QueryString["n"];\n  Action run = () => new SqlCommand("select " + v).ExecuteReader();\n  run();\n } }\n')
    assert "sql-injection" in run(tmp_path, "C.cs", src)
    d = tmp_path / "r"
    d.mkdir()
    src2 = ('class C { static void M(ref string a, string b) { a = b; }\n void H(HttpRequest request) {\n  string q = "x";\n  M(ref q, request.QueryString["n"]);\n  new SqlCommand("select " + q).ExecuteReader();\n } }\n')
    assert "sql-injection" in run(d, "C.cs", src2)


def test_python_lambda_callback_is_followed(tmp_path):
    src = "from flask import request\nimport os\ndef invoke(cb):\n    cb()\ndef h():\n    c = request.args.get('c')\n    invoke(lambda: os.system(c))\n"
    assert "command-injection" in run(tmp_path, "a.py", src)


def test_java_matches_without_anchors_is_a_whole_string_test(tmp_path):
    body = 'String c = req.getParameter("c"); if (c == null || !c.matches("[A-Za-z]+")) { return; } java.sql.DriverManager.getConnection("x").createStatement().executeQuery("select \'" + c + "\'");'
    assert run(tmp_path, "S.java", java(body)) == []


def test_php_closure_and_reference_parameter(tmp_path):
    src = ('<?php\nfunction a() { $x = $_GET["c"]; $run = function () use ($x) { shell_exec($x); }; $run(); }\n'
           'function store(&$out, $v) { $out = $v; }\nfunction b() { $x = "date"; store($x, $_GET["c"]); shell_exec($x); }\n')
    assert "command-injection" in run(tmp_path, "a.php", src)
    d1, d2 = tmp_path / "c", tmp_path / "r"
    d1.mkdir(); d2.mkdir()
    assert run(d1, "a.php", '<?php\nfunction a() { $x = "ls"; $run = function () use ($x) { shell_exec($x); }; $run(); }\n') == []
    assert run(d2, "a.php", '<?php\nfunction store(&$out, $v) { $out = $v; }\nfunction b() { $x = "date"; store($x, "pwd"); shell_exec($x); }\n') == []


def test_go_pointer_argument_flows_back(tmp_path):
    vuln = ('package p\nimport ("net/http"; "os/exec")\nfunc h(w http.ResponseWriter, r *http.Request) {\n  x := "date"\n  store(&x, r.FormValue("c"))\n  exec.Command("sh", "-c", x).Run()\n}\n'
            'func store(out *string, v string) { *out = v }\n')
    assert "command-injection" in run(tmp_path, "m.go", vuln)
    safe = vuln.replace('r.FormValue("c")', '"pwd"')
    d = tmp_path / "s"
    d.mkdir()
    assert run(d, "m.go", safe) == []


def test_go_literal_allowlist_before_shell(tmp_path):
    src = ('package p\nimport ("net/http"; "os/exec")\nfunc h(w http.ResponseWriter, r *http.Request) {\n  x := r.FormValue("c")\n  if x != "date" && x != "pwd" { return }\n  exec.Command("sh", "-c", x).Run()\n}\n')
    assert run(tmp_path, "m.go", src) == []


def test_java_unguessable_name_blocks_path_traversal_but_a_plain_suffix_does_not(tmp_path):
    src = ('import javax.servlet.http.*;\npublic class U extends HttpServlet {\n  static final String DIR = "/tmp/up/";\n'
           '  protected void doPost(HttpServletRequest req, HttpServletResponse resp) throws Exception {\n'
           '    String f = req.getParameter("f");\n    String suffix = f.substring(f.lastIndexOf("."));\n'
           '    java.io.File c = new java.io.File(DIR + java.util.UUID.randomUUID() + suffix);\n    c.createNewFile();\n    new java.io.FileOutputStream(c);\n  }\n}\n')
    assert run(tmp_path, "U.java", src) == []
    d = tmp_path / "p"
    d.mkdir()
    assert "path-traversal" in run(d, "U.java", src.replace("java.util.UUID.randomUUID() + suffix", "suffix"))


def test_php_class_of_a_json_only_application_is_not_xss(tmp_path):
    (tmp_path / "public").mkdir()
    (tmp_path / "public" / "index.php").write_text('<?php\nheader("Content-Type: application/json; charset=UTF-8");\n$c = new Ctl();\n$c->go();\n')
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Ctl.php").write_text('<?php\nclass Ctl {\n  function go() { echo json_encode(["q" => $_GET["q"]]); }\n}\n')
    assert "xss" not in {f["rule_id"].split(".")[-1] for f in discover_repo(tmp_path)}
    d = tmp_path / "other"
    d.mkdir()
    assert "xss" in run(d, "page.php", '<?php\nclass P {\n  function go() { echo "<p>" . $_GET["q"] . "</p>"; }\n}\n')


HOST_CASES = {
    "ruby": ('require "sinatra"\nrequire "net/http"\nALLOWED = ["hooks.example.com"]\nget("/w") do\n  u = URI(params[:url])\n  halt 400 unless ALLOWED.include?(u.host)\n  Net::HTTP.get(u)\nend\n', "app.rb"),
    "javascript": ('const express = require("express"); const axios = require("axios"); const app = express();\nconst ALLOWED = ["hooks.example.com"];\n'
                   'app.get("/w", async (req, res) => {\n  const u = req.query.url;\n  if (!ALLOWED.includes(new URL(u).hostname)) { return res.status(400).end(); }\n  await axios.get(u);\n});\n', "a.js"),
    "python": ('import requests\nfrom urllib.parse import urlparse\nfrom flask import request\nALLOWED = ["hooks.example.com"]\ndef f():\n    u = request.args.get("url")\n'
               '    if urlparse(u).hostname not in ALLOWED:\n        return "no"\n    requests.get(u)\n', "a.py"),
    "php": ('<?php\n$allowed = ["hooks.example.com"];\n$u = $_GET["url"];\nif (!in_array(parse_url($u, PHP_URL_HOST), $allowed, true)) { die("no"); }\nfile_get_contents($u);\n', "a.php"),
    "java": ('import javax.servlet.http.*;\nimport java.util.*;\npublic class S extends HttpServlet {\n  static final List<String> OK = Arrays.asList("hooks.example.com");\n'
             '  protected void doGet(HttpServletRequest req, HttpServletResponse resp) throws Exception {\n    String u = req.getParameter("u");\n'
             '    if (!OK.contains(new java.net.URL(u).getHost())) { return; }\n    new java.net.URL(u).openStream();\n  }\n}\n', "S.java"),
    "csharp": ('[ApiController]\npublic class C : ControllerBase {\n  static readonly string[] Ok = new[] { "hooks.example.com" };\n  [HttpGet("/w")]\n'
               '  public async Task<IActionResult> W() { var u = Request.Query["u"].ToString(); if (!Ok.Contains(new Uri(u).Host)) { return BadRequest(); }\n'
               '    var s = await new HttpClient().GetStringAsync(u); return Ok(); }\n}\n', "C.cs"),
}


import pytest as _pytest


@_pytest.mark.parametrize("lang", sorted(HOST_CASES))
def test_host_allowlist_proves_the_url_safe_for_ssrf_only(tmp_path, lang):
    src, name = HOST_CASES[lang]
    assert "ssrf" not in run(tmp_path, name, src)
    d = tmp_path / "unguarded"
    d.mkdir()
    # the same program without the allowlist check is still SSRF
    lines = [l for l in src.splitlines(keepends=True) if "ALLOWED" not in l.split("(")[0] or "=" in l.split("ALLOWED")[0] or "include" not in l]
    unguarded = src
    import re
    unguarded = re.sub(r"(halt 400 unless[^\n]*\n|if \(!ALLOWED[^\n]*\n|if urlparse[^\n]*\n\s*return \"no\"\n|if \(!in_array[^\n]*\n|if \(!OK\.contains[^\n]*\n|if \(!Ok\.Contains[^\n]*\n)", "", src)
    assert "ssrf" in run(d, name, unguarded)
