"""A request value that leaves a helper as a yielded value, a destructured tuple or a block argument still reaches the sink."""
import pytest

from aletheore.flow.discover import discover_repo

PY_GEN = "import os\nfrom flask import Flask, request\napp = Flask(__name__)\ndef items(v):\n    for p in v.split(','):\n        yield {x}\n@app.route('/r')\ndef r():\n    for it in items(request.args['q']):\n        os.system('echo ' + it)\n    return 'ok'\n"
JS_GEN = "const express=require('express');const cp=require('child_process');const app=express();\nfunction* items(v){for(const p of v.split(',')) yield {x};}\napp.get('/r',(req,res)=>{for(const it of items(req.query.q)){cp.exec('echo '+it);}res.send('ok');});\n"
PHP_GEN = "<?php\nfunction items($v){foreach(explode(',', $v) as $p){yield {x};}}\nforeach(items($_GET['q']) as $it){system('echo '.$it);}\n"
CS_GEN = "using Microsoft.AspNetCore.Mvc;using System.Diagnostics;using System.Collections.Generic;\npublic class C:Controller{\n  static IEnumerable<string> Items(string v){foreach(var p in v.Split(',')) yield return {x};}\n  [HttpGet(\"/r\")] public string R(string q){foreach(var it in Items(q)){Process.Start(\"sh\",\"-c echo \"+it);}return \"ok\";}\n}\n"
RB_YIELD = "require 'sinatra'\ndef items(v); v.split(',').each {{ |s| yield {x} }}; end\nget '/r' do\n  items(params[:q]) {{ |s| system(\"echo \"+s) }}\n  'ok'\nend\n"
PHP_LIST = "<?php\nfunction parse($v){{return explode(',', $v);}}\nlist($a,$b)=parse({src});\nsystem('echo '.$b);\n"
CS_TUPLE = "using Microsoft.AspNetCore.Mvc;using System.Diagnostics;\npublic class C:Controller{{\n  static (string,string) Parse(string v){{var p=v.Split(',');return ({a},{b});}}\n  [HttpGet(\"/r\")] public string R(string q){{var (a,b)=Parse(q);Process.Start(\"sh\",\"-c echo \"+b);return \"ok\";}}\n}}\n"


def _scan(tmp_path, name, text):
    (tmp_path / name).write_text(text)
    return discover_repo(tmp_path)


@pytest.mark.parametrize("name,tpl,tainted,safe", [
    ("app.py", PY_GEN, "p.strip()", "'x'"),
    ("app.js", JS_GEN, "p.trim()", "'x'"),
    ("a.php", PHP_GEN, "trim($p)", "'x'"),
    ("C.cs", CS_GEN, "p.Trim()", "\"x\""),
])
def test_generator_yield_reaches_the_caller(tmp_path, name, tpl, tainted, safe):
    assert len(_scan(tmp_path, name, tpl.replace("{x}", tainted))) == 1


@pytest.mark.parametrize("name,tpl,safe", [
    ("app.py", PY_GEN, "'x'"),
    ("app.js", JS_GEN, "'x'"),
    ("a.php", PHP_GEN, "'x'"),
    ("C.cs", CS_GEN, "\"x\""),
])
def test_generator_that_yields_constants_is_quiet(tmp_path, name, tpl, safe):
    assert _scan(tmp_path, name, tpl.replace("{x}", safe)) == []


def test_ruby_yield_runs_the_callers_block(tmp_path):
    assert len(_scan(tmp_path, "app.rb", RB_YIELD.format(x="s.strip"))) == 1


def test_ruby_yield_of_a_constant_is_quiet(tmp_path):
    assert _scan(tmp_path, "app.rb", RB_YIELD.format(x="'x'")) == []


def test_php_list_destructuring_binds_each_name(tmp_path):
    assert len(_scan(tmp_path, "a.php", PHP_LIST.format(src="$_GET['q']"))) == 1
    assert _scan(tmp_path, "a.php", PHP_LIST.format(src="'a,b'")) == []


def test_csharp_tuple_deconstruction_follows_the_element(tmp_path):
    assert len(_scan(tmp_path, "C.cs", CS_TUPLE.format(a="p[0]", b="p[1]"))) == 1
    assert _scan(tmp_path, "C.cs", CS_TUPLE.format(a="p[0]", b="\"x\"")) == []
