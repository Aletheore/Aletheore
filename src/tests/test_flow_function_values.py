"""Functions used as values: assigned to a name, passed as an argument, kept in a table or an attribute, built by
getattr or functools.partial. Calling the value runs the function, so a sink inside it sees the call's arguments."""
from pathlib import Path

from aletheore.flow.discover import discover_repo

HEAD = "import os, shlex\nfrom flask import Flask, request\napp = Flask(__name__)\n\ndef sink(x):\n    os.system('echo ' + x)\n\n"
ROUTE = "@app.route('/a')\ndef a():\n"


def _lines(tmp_path: Path, body: str) -> list[int]:
    (tmp_path / "app.py").write_text(HEAD + body)
    return sorted(f["line"] for f in discover_repo(tmp_path) if f["rule_id"].endswith("command-injection"))


def test_direct_call_is_the_baseline(tmp_path):
    assert _lines(tmp_path, ROUTE + "    sink(request.args.get('x'))\n") == [6]


def test_function_assigned_to_a_name(tmp_path):
    assert _lines(tmp_path, ROUTE + "    cb = sink\n    cb(request.args.get('x'))\n") == [6]


def test_function_passed_as_an_argument(tmp_path):
    assert _lines(tmp_path, "def run(fn, v):\n    fn(v)\n\n" + ROUTE + "    run(sink, request.args.get('x'))\n") == [6]


def test_function_in_a_table(tmp_path):
    assert _lines(tmp_path, "TABLE = {'go': sink}\n\n" + ROUTE + "    TABLE['go'](request.args.get('x'))\n") == [6]


def test_function_chosen_from_a_table_by_the_request(tmp_path):
    body = "TABLE = {'go': sink}\n\n" + ROUTE + "    TABLE[request.args.get('k')](request.args.get('x'))\n"
    assert _lines(tmp_path, body) == [6]


def test_function_stored_in_an_attribute(tmp_path):
    body = ("class H:\n    def __init__(self, fn):\n        self.fn = fn\n    def go(self, v):\n        self.fn(v)\n\n"
            + ROUTE + "    H(sink).go(request.args.get('x'))\n")
    assert _lines(tmp_path, body) == [6]


def test_bound_method_value(tmp_path):
    body = ("class K:\n    def run(self, x):\n        os.system('echo ' + x)\n\n"
            + ROUTE + "    f = K().run\n    f(request.args.get('x'))\n")
    assert _lines(tmp_path, body) == [10]


def test_getattr_builds_a_method_value(tmp_path):
    body = ("class K:\n    def run(self, x):\n        os.system('echo ' + x)\n\n"
            + ROUTE + "    getattr(K(), 'run')(request.args.get('x'))\n")
    assert _lines(tmp_path, body) == [10]


def test_functools_partial(tmp_path):
    body = "import functools\n\n" + ROUTE + "    f = functools.partial(sink)\n    f(request.args.get('x'))\n"
    assert _lines(tmp_path, body) == [6]


# ---- must stay quiet

def test_a_function_that_does_not_use_its_argument_is_quiet(tmp_path):
    body = "def clean(x):\n    pass\n\n" + ROUTE + "    cb = clean\n    cb(request.args.get('x'))\n"
    assert _lines(tmp_path, body) == []


def test_a_quoting_function_called_through_a_value_is_quiet(tmp_path):
    body = "def quote(x):\n    return shlex.quote(x)\n\n" + ROUTE + "    q = quote\n    os.system('echo ' + q(request.args.get('x')))\n"
    assert _lines(tmp_path, body) == []


def test_a_constant_argument_through_a_table_is_quiet(tmp_path):
    body = "TABLE = {'go': sink}\n\n" + ROUTE + "    TABLE[request.args.get('k')]('fixed')\n"
    assert _lines(tmp_path, body) == []


def test_a_function_value_passed_to_a_sink_is_not_tainted(tmp_path):
    body = ROUTE + "    cb = sink\n    os.system('echo ' + str(cb))\n"
    assert _lines(tmp_path, body) == []


# ---- JavaScript

JS_HEAD = ("const express = require('express');\nconst { exec } = require('child_process');\nconst app = express();\n"
           "function sink(x) { exec('echo ' + x); }\n")
JS_ROUTE = "app.get('/a', (req, res) => {\n"


def _js(tmp_path: Path, body: str) -> list[int]:
    (tmp_path / "app.js").write_text(JS_HEAD + body)
    return sorted(f["line"] for f in discover_repo(tmp_path) if f["rule_id"].endswith("command-injection"))


def test_js_direct_call_is_the_baseline(tmp_path):
    assert _js(tmp_path, JS_ROUTE + "  sink(req.query.x);\n});\n") == [4]


def test_js_function_assigned_to_a_name(tmp_path):
    assert _js(tmp_path, JS_ROUTE + "  const cb = sink;\n  cb(req.query.x);\n});\n") == [4]


def test_js_function_passed_as_an_argument(tmp_path):
    assert _js(tmp_path, "function run(fn, v) { fn(v); }\n" + JS_ROUTE + "  run(sink, req.query.x);\n});\n") == [4]


def test_js_inline_callback_receives_the_argument(tmp_path):
    body = "function after(cb, v) { cb(v); }\n" + JS_ROUTE + "  after(function (z) { exec('echo ' + z); }, req.query.x);\n});\n"
    assert _js(tmp_path, body) == [7]


def test_js_function_in_an_object(tmp_path):
    assert _js(tmp_path, "const T = { go: sink };\n" + JS_ROUTE + "  T.go(req.query.x);\n});\n") == [4]


def test_js_function_chosen_from_an_object_by_the_request(tmp_path):
    assert _js(tmp_path, "const T = { go: sink };\n" + JS_ROUTE + "  T[req.query.k](req.query.x);\n});\n") == [4]


def test_js_function_kept_in_a_field(tmp_path):
    body = "class H { constructor(fn) { this.fn = fn; } go(v) { this.fn(v); } }\n" + JS_ROUTE + "  new H(sink).go(req.query.x);\n});\n"
    assert _js(tmp_path, body) == [4]


def test_js_method_taken_as_a_value(tmp_path):
    body = "class K { run(x) { exec('echo ' + x); } }\n" + JS_ROUTE + "  const f = new K().run;\n  f(req.query.x);\n});\n"
    assert _js(tmp_path, body) == [5]


def test_js_bind_call_and_apply(tmp_path):
    assert _js(tmp_path, JS_ROUTE + "  const f = sink.bind(null);\n  f(req.query.x);\n});\n") == [4]
    assert _js(tmp_path, JS_ROUTE + "  sink.call(null, req.query.x);\n});\n") == [4]
    assert _js(tmp_path, JS_ROUTE + "  sink.apply(null, [req.query.x]);\n});\n") == [4]


def test_js_foreach_hands_each_element_to_the_callback(tmp_path):
    assert _js(tmp_path, JS_ROUTE + "  req.body.items.forEach(i => exec('echo ' + i));\n});\n") == [6]
    assert _js(tmp_path, "const L = [sink];\n" + JS_ROUTE + "  L.forEach(f => f(req.query.x));\n});\n") == [4]


def test_js_promise_then_hands_the_value_to_a_named_function(tmp_path):
    assert _js(tmp_path, JS_ROUTE + "  Promise.resolve(req.query.x).then(sink);\n});\n") == [4]


def test_js_function_that_ignores_its_argument_is_quiet(tmp_path):
    assert _js(tmp_path, "function clean(x) { }\n" + JS_ROUTE + "  const cb = clean;\n  cb(req.query.x);\n});\n") == []


def test_js_constant_argument_through_a_table_is_quiet(tmp_path):
    assert _js(tmp_path, "const T = { go: sink };\n" + JS_ROUTE + "  T[req.query.k]('fixed');\n});\n") == []


def test_js_foreach_over_constants_is_quiet(tmp_path):
    assert _js(tmp_path, "const L = ['a', 'b'];\n" + JS_ROUTE + "  L.forEach(i => exec('echo ' + i));\n});\n") == []


# ---- Go

GO_HEAD = 'package main\nimport ("net/http"; "os/exec")\nfunc sink(x string) { exec.Command("sh", "-c", "echo "+x).Run() }\n'
GO_H = 'func h(w http.ResponseWriter, r *http.Request) {\n'
GO_Q = 'r.URL.Query().Get("x")'


def _go(tmp_path: Path, body: str) -> list[int]:
    (tmp_path / "a.go").write_text(GO_HEAD + body)
    return sorted(f["line"] for f in discover_repo(tmp_path) if f["rule_id"].endswith("command-injection"))


def test_go_function_assigned_to_a_name(tmp_path):
    assert _go(tmp_path, GO_H + f'\tcb := sink\n\tcb({GO_Q})\n}}\n') == [3]


def test_go_function_passed_as_an_argument(tmp_path):
    assert _go(tmp_path, 'func run(fn func(string), v string) { fn(v) }\n' + GO_H + f'\trun(sink, {GO_Q})\n}}\n') == [3]


def test_go_function_in_a_package_level_table(tmp_path):
    body = 'var table = map[string]func(string){"go": sink}\n' + GO_H + f'\ttable["go"]({GO_Q})\n}}\n'
    assert _go(tmp_path, body) == [3]
    body = 'var table = map[string]func(string){"go": sink}\n' + GO_H + f'\ttable[{GO_Q}]({GO_Q})\n}}\n'
    assert _go(tmp_path, body) == [3]


def test_go_function_in_a_slice(tmp_path):
    body = 'var fs = []func(string){sink}\n' + GO_H + f'\tfor _, f := range fs {{\n\t\tf({GO_Q})\n\t}}\n}}\n'
    assert _go(tmp_path, body) == [3]


def test_go_function_kept_in_a_struct_field(tmp_path):
    body = 'type S struct{ fn func(string) }\n' + GO_H + f'\ts := S{{fn: sink}}\n\ts.fn({GO_Q})\n}}\n'
    assert _go(tmp_path, body) == [3]


def test_go_method_value(tmp_path):
    body = 'type K struct{}\nfunc (k K) Run(x string) { exec.Command("sh", "-c", x).Run() }\n' + GO_H + f'\tf := K{{}}.Run\n\tf({GO_Q})\n}}\n'
    assert _go(tmp_path, body) == [5]


def test_go_function_that_ignores_its_argument_is_quiet(tmp_path):
    assert _go(tmp_path, 'func clean(x string) {}\n' + GO_H + f'\tcb := clean\n\tcb({GO_Q})\n}}\n') == []


def test_go_constant_argument_through_a_table_is_quiet(tmp_path):
    body = 'var table = map[string]func(string){"go": sink}\n' + GO_H + f'\ttable[{GO_Q}]("fixed")\n}}\n'
    assert _go(tmp_path, body) == []


# ---- Ruby, PHP, Java, Kotlin, C#, Rust

def _cmd(tmp_path: Path, name: str, src: str) -> list[int]:
    (tmp_path / name).write_text(src)
    return sorted(f["line"] for f in discover_repo(tmp_path) if f["rule_id"].endswith("command-injection"))


RB = "require 'sinatra'\n\ndef sink(x)\n  system(\"echo \" + x)\nend\n\nget '/a' do\n"


def test_ruby_method_object_and_send(tmp_path):
    assert _cmd(tmp_path, "app.rb", RB + "  method(:sink).call(params[:x])\nend\n") == [4]
    assert _cmd(tmp_path, "app.rb", RB + "  send(:sink, params[:x])\nend\n") == [4]


def test_ruby_lambda_in_a_table_and_method_passed_as_block(tmp_path):
    body = "T = { go: ->(x) { system(\"echo \" + x) } }\nget '/a' do\n  T[:go].call(params[:x])\nend\n"
    assert _cmd(tmp_path, "app.rb", "require 'sinatra'\n\n" + body) == [3]
    assert _cmd(tmp_path, "app.rb", RB + "  [params[:x]].each(&method(:sink))\nend\n") == [4]


def test_ruby_method_that_ignores_its_argument_is_quiet(tmp_path):
    assert _cmd(tmp_path, "app.rb", RB + "  method(:other).call(params[:x])\nend\n") == []


PHP = "<?php\nfunction sink($x) { system('echo ' . $x); }\n"


def test_php_string_callable_and_call_user_func(tmp_path):
    assert _cmd(tmp_path, "a.php", PHP + "$f = 'sink';\n$f($_GET['x']);\n") == [2]
    assert _cmd(tmp_path, "a.php", PHP + "call_user_func('sink', $_GET['x']);\n") == [2]
    assert _cmd(tmp_path, "a.php", PHP + "call_user_func_array('sink', [$_GET['x']]);\n") == [2]


def test_php_array_map_table_and_method_callable(tmp_path):
    assert _cmd(tmp_path, "a.php", PHP + "array_map('sink', [$_GET['x']]);\n") == [2]
    assert _cmd(tmp_path, "a.php", PHP + "$t = ['go' => 'sink'];\n$t['go']($_GET['x']);\n") == [2]
    body = "class K { function run($x) { system('echo ' . $x); } }\n$f = [new K, 'run'];\n$f($_GET['x']);\n"
    assert _cmd(tmp_path, "a.php", PHP + body) == [3]


def test_php_first_class_callable(tmp_path):
    assert _cmd(tmp_path, "a.php", PHP + "$f = sink(...);\n$f($_GET['x']);\n") == [2]


def test_php_callable_that_ignores_its_argument_is_quiet(tmp_path):
    assert _cmd(tmp_path, "a.php", PHP + "function clean($x) {}\n$f = 'clean';\n$f($_GET['x']);\n") == []


JAVA_HEAD = ("import javax.servlet.http.*;\nimport java.util.*;\nimport java.util.function.*;\npublic class A extends HttpServlet {\n"
             "  static void sink(String x) throws Exception { Runtime.getRuntime().exec(\"echo \" + x); }\n"
             "  void run(String x) throws Exception { Runtime.getRuntime().exec(\"echo \" + x); }\n"
             "  protected void doGet(HttpServletRequest req, HttpServletResponse res) throws Exception {\n    String v = req.getParameter(\"x\");\n")
JAVA_TAIL = "  }\n  static void sinkQ(String x) { try { sink(x); } catch (Exception e) {} }\n  void runQ(String x) { try { run(x); } catch (Exception e) {} }\n}\n"


def _java(tmp_path: Path, body: str) -> list[int]:
    return _cmd(tmp_path, "A.java", JAVA_HEAD + body + JAVA_TAIL)


def test_java_lambda_stored_and_called_later(tmp_path):
    assert _java(tmp_path, "    Consumer<String> c = s -> { try { sink(s); } catch (Exception e) {} };\n    c.accept(v);\n") == [5]
    assert _java(tmp_path, "    Function<String,String> f = s -> { try { sink(s); } catch (Exception e) {} return s; };\n    f.apply(v);\n") == [5]


def test_java_method_references(tmp_path):
    assert _java(tmp_path, "    Consumer<String> c = A::sinkQ;\n    c.accept(v);\n") == [5]
    assert _java(tmp_path, "    Consumer<String> c = this::runQ;\n    c.accept(v);\n") == [6]


def test_java_lambda_in_a_map_and_foreach(tmp_path):
    body = "    Map<String, Consumer<String>> m = new HashMap<>();\n    m.put(\"go\", s -> { try { sink(s); } catch (Exception e) {} });\n    m.get(\"go\").accept(v);\n"
    assert _java(tmp_path, body) == [5]
    assert _java(tmp_path, "    List.of(v).forEach(s -> { try { sink(s); } catch (Exception e) {} });\n") == [5]


def test_java_lambda_that_ignores_its_argument_is_quiet(tmp_path):
    assert _java(tmp_path, "    Consumer<String> c = s -> { };\n    c.accept(v);\n") == []


KT_HEAD = ('import org.springframework.web.bind.annotation.*\nfun sink(x: String) { Runtime.getRuntime().exec("echo " + x) }\n'
           'fun run2(f: (String) -> Unit, v: String) { f(v) }\n@RestController\nclass A {\n  @GetMapping("/a")\n  fun h(@RequestParam x: String): String {\n')
KT_TAIL = '    return ""\n  }\n}\n'


def test_kotlin_function_references(tmp_path):
    assert _cmd(tmp_path, "A.kt", KT_HEAD + "    val f = ::sink\n    f(x)\n" + KT_TAIL) == [2]
    assert _cmd(tmp_path, "A.kt", KT_HEAD + "    run2(::sink, x)\n" + KT_TAIL) == [2]
    assert _cmd(tmp_path, "A.kt", KT_HEAD + "    listOf(x).forEach(::sink)\n" + KT_TAIL) == [2]
    assert _cmd(tmp_path, "A.kt", KT_HEAD + "    val m = mapOf(\"go\" to ::sink)\n    m[\"go\"]!!(x)\n" + KT_TAIL) == [2]


def test_kotlin_function_that_ignores_its_argument_is_quiet(tmp_path):
    assert _cmd(tmp_path, "A.kt", KT_HEAD + "    val f = { s: String -> }\n    f(x)\n" + KT_TAIL) == []


CS_HEAD = ('using System;\nusing System.Collections.Generic;\nusing System.Diagnostics;\nusing Microsoft.AspNetCore.Mvc;\npublic class A : Controller {\n'
           '  static void Sink(string x) { Process.Start("sh", "-c echo " + x); }\n  void Run(string x) { Process.Start("sh", "-c echo " + x); }\n'
           '  [HttpGet("/a")]\n  public IActionResult H(string x) {\n')
CS_TAIL = "    return Ok();\n  }\n}\n"


def test_csharp_method_group_invoke_and_this_group(tmp_path):
    assert _cmd(tmp_path, "A.cs", CS_HEAD + "    Action<string> f = Sink;\n    f(x);\n" + CS_TAIL) == [6]
    assert _cmd(tmp_path, "A.cs", CS_HEAD + "    Action<string> f = s => Sink(s);\n    f.Invoke(x);\n" + CS_TAIL) == [6]
    assert _cmd(tmp_path, "A.cs", CS_HEAD + "    Action<string> f = this.Run;\n    f(x);\n" + CS_TAIL) == [7]


def test_csharp_dictionary_of_delegates_and_foreach(tmp_path):
    body = "    var m = new Dictionary<string, Action<string>>();\n    m[\"go\"] = Sink;\n    m[\"go\"](x);\n"
    assert _cmd(tmp_path, "A.cs", CS_HEAD + body + CS_TAIL) == [6]
    body = "    var l = new List<string>();\n    l.Add(x);\n    l.ForEach(Sink);\n"
    assert _cmd(tmp_path, "A.cs", CS_HEAD + body + CS_TAIL) == [6]


def test_csharp_delegate_that_ignores_its_argument_is_quiet(tmp_path):
    assert _cmd(tmp_path, "A.cs", CS_HEAD + "    Action<string> f = s => { };\n    f(x);\n" + CS_TAIL) == []


RS_HEAD = ('use actix_web::{get, web, HttpResponse};\nuse std::process::Command;\nuse std::collections::HashMap;\n'
           'fn sink(x: &str) { Command::new("sh").arg("-c").arg(format!("echo {}", x)).output().unwrap(); }\n'
           'fn run2(f: fn(&str), v: &str) { f(v); }\n#[get("/a")]\nasync fn h(q: web::Query<HashMap<String, String>>) -> HttpResponse {\n'
           '    let x = q.get("x").unwrap().clone();\n')
RS_TAIL = "    HttpResponse::Ok().finish()\n}\n"


def test_rust_function_pointers(tmp_path):
    assert _cmd(tmp_path, "main.rs", RS_HEAD + "    let f: fn(&str) = sink;\n    f(&x);\n" + RS_TAIL) == [4]
    assert _cmd(tmp_path, "main.rs", RS_HEAD + "    run2(sink, &x);\n" + RS_TAIL) == [4]


def test_rust_function_in_a_map_and_a_vec(tmp_path):
    body = "    let mut m: HashMap<&str, fn(&str)> = HashMap::new();\n    m.insert(\"go\", sink);\n    m[\"go\"](&x);\n"
    assert _cmd(tmp_path, "main.rs", RS_HEAD + body + RS_TAIL) == [4]
    body = "    let fs: Vec<fn(&str)> = vec![sink];\n    for f in fs { f(&x); }\n"
    assert _cmd(tmp_path, "main.rs", RS_HEAD + body + RS_TAIL) == [4]


def test_rust_closure_that_ignores_its_argument_is_quiet(tmp_path):
    assert _cmd(tmp_path, "main.rs", RS_HEAD + "    let f = |s: &str| {};\n    f(&x);\n" + RS_TAIL) == []


REG = "REG = []\ndef register(f):\n    REG.append(f)\n    return f\n"
DECO = "HANDLERS = {}\ndef on(name):\n    def deco(f):\n        HANDLERS[name] = f\n        return f\n    return deco\n\n"


def test_py_registry_filled_by_a_register_call(tmp_path):
    body = REG + "register(sink)\n\n" + ROUTE + "    for f in REG:\n        f(request.args.get('x'))\n"
    assert _lines(tmp_path, body) == [6]


def test_py_registry_of_safe_functions_stays_quiet(tmp_path):
    body = REG + "def clean(x):\n    pass\nregister(clean)\n\n" + ROUTE + "    for f in REG:\n        f(request.args.get('x'))\n"
    assert _lines(tmp_path, body) == []


def test_py_decorator_registry_with_a_key(tmp_path):
    body = DECO + "@on('go')\ndef go(x):\n    os.system('echo ' + x)\n\n" + ROUTE + "    HANDLERS['go'](request.args.get('x'))\n"
    assert len(_lines(tmp_path, body)) == 1


def test_py_decorator_registry_other_key_stays_quiet(tmp_path):
    body = (DECO + "def clean(x):\n    pass\n\n@on('safe')\ndef c2(x):\n    clean(x)\n\n@on('go')\ndef go(x):\n    os.system('echo ' + x)\n\n"
            + ROUTE + "    HANDLERS['safe'](request.args.get('x'))\n")
    assert _lines(tmp_path, body) == []


def test_py_bare_decorator_registers_the_function(tmp_path):
    body = REG + "@register\ndef go(x):\n    os.system('echo ' + x)\n\n" + ROUTE + "    for f in REG:\n        f(request.args.get('x'))\n"
    assert len(_lines(tmp_path, body)) == 1


def test_py_direct_append_and_item_assignment(tmp_path):
    body = "REG = []\nREG.append(sink)\nT = {}\nT['k'] = sink\n\n" + ROUTE + "    REG[0](request.args.get('x'))\n"
    assert _lines(tmp_path, body) == [6]
    body = "T = {}\nT['k'] = sink\n\n" + ROUTE + "    T['k'](request.args.get('x'))\n"
    assert _lines(tmp_path, body) == [6]


def test_cs_collection_initializers(tmp_path):
    cases = [
        '    var m = new Dictionary<string, Action<string>>{{"go", Sink}};\n    m["go"](x);\n',
        '    var m = new Dictionary<string, Action<string>>{["go"] = Sink};\n    m["go"](x);\n',
        "    new List<string>{x}.ForEach(s => Sink(s));\n",
        "    new List<string>{x}.ForEach(Sink);\n",
    ]
    for body in cases:
        assert _cmd(tmp_path, "A.cs", CS_HEAD + body + CS_TAIL) == [6], body


def test_cs_collection_initializer_of_safe_values_stays_quiet(tmp_path):
    body = '    var m = new Dictionary<string, string>{{"a", "1"}};\n    Sink(m["a"]);\n    new List<string>{"k"}.ForEach(s => Sink(s));\n'
    assert _cmd(tmp_path, "A.cs", CS_HEAD + body + CS_TAIL) == []


XREG = "HANDLERS = {}\ndef on(name):\n    def deco(f):\n        HANDLERS[name] = f\n        return f\n    return deco\n"
XROUTE = ("from flask import Flask, request\nfrom registry import HANDLERS\napp = Flask(__name__)\n@app.route('/a')\ndef a():\n"
          "    HANDLERS['go'](request.args.get('x'))\n")


def _two_files(tmp_path, files):
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    return sorted(f["line"] for f in discover_repo(tmp_path) if f["rule_id"].endswith("command-injection"))


def test_py_decorator_registry_filled_from_another_module(tmp_path):
    files = {"registry.py": XREG, "app.py": XROUTE,
             "handlers.py": "import os\nfrom registry import on\n\n@on('go')\ndef go(x):\n    os.system('echo ' + x)\n"}
    assert _two_files(tmp_path, files) == [6]


def test_py_registry_filled_through_a_module_attribute(tmp_path):
    files = {"registry.py": XREG, "app.py": XROUTE,
             "handlers.py": "import os\nimport registry as r\n\n@r.on('go')\ndef go(x):\n    os.system('echo ' + x)\n"}
    assert _two_files(tmp_path, files) == [6]


def test_py_cross_module_registry_other_key_stays_quiet(tmp_path):
    files = {"registry.py": XREG, "app.py": XROUTE,
             "handlers.py": "import os\nfrom registry import on\n\n@on('go')\ndef safe(x):\n    pass\n\n@on('other')\ndef bad(x):\n    os.system('echo ' + x)\n"}
    assert _two_files(tmp_path, files) == []


def test_py_cross_module_register_call(tmp_path):
    reg = "REG = []\ndef register(f):\n    REG.append(f)\n    return f\n"
    route = "from flask import Flask, request\nfrom registry import REG\napp = Flask(__name__)\n@app.route('/a')\ndef a():\n    for f in REG:\n        f(request.args.get('x'))\n"
    hand = "import os\nfrom registry import register\n\ndef sink(x):\n    os.system('echo ' + x)\n\nregister(sink)\n"
    assert _two_files(tmp_path, {"registry.py": reg, "app.py": route, "handlers.py": hand}) == [5]
