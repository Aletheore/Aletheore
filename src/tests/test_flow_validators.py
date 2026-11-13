"""A check wrapped in a function of the program still proves the value, and a replace that deletes everything outside a safe set sanitizes it; weaker versions of both still report."""
import pytest

from aletheore.flow.discover import discover_repo

JS = "const express=require('express');const cp=require('child_process');const app=express();\n{helper}\napp.get('/r',(req,res)=>{{ const v=req.query.q; {use} cp.exec('echo '+v); res.send('ok'); }});\n"
JS_CASES = [
    ("const isOk=v=>/^[a-z0-9]+$/.test(v);", "if(!isOk(v)) return res.sendStatus(400);", 0),
    ("function validate(v){ if(!/^[a-z0-9]+$/.test(v)) throw new Error('bad'); }", "validate(v);", 0),
    ("const isOk=v=>v.length<10;", "if(!isOk(v)) return res.sendStatus(400);", 1),
    ("const isOk=v=>/^[a-z ;]+$/.test(v);", "if(!isOk(v)) return res.sendStatus(400);", 1),
    ("function validate(v){ if(v.length>10) throw new Error('long'); }", "validate(v);", 1),
    ("const isOk=v=>/^[a-z0-9]+$/.test(v);", "isOk(v);", 1),
]


@pytest.mark.parametrize("helper,use,expected", JS_CASES)
def test_js_validator_functions(tmp_path, helper, use, expected):
    (tmp_path / "app.js").write_text(JS.format(helper=helper, use=use))
    assert len(discover_repo(tmp_path)) == expected


SANITIZE = [
    ("app.js", "const express=require('express');const cp=require('child_process');const app=express();\napp.get('/r',(req,res)=>{{ cp.exec('echo '+req.query.q.replace({a})); res.send('ok'); }});\n",
     ("/[^a-z0-9]/g,''", "/[^a-z0-9]/,''", "/[^a-z0-9;]/g,''", "/[<>]/g,''")),
    ("App.java", "import org.springframework.web.bind.annotation.*;\n@RestController public class App {{\n  @GetMapping(\"/r\") public String r(@RequestParam String q) throws Exception {{ Runtime.getRuntime().exec(\"echo \"+q.replaceAll({a})); return \"ok\"; }}\n}}\n",
     ("\"[^a-z0-9]\", \"\"", "\"[^a-z0-9]\", \"_x\"", "\"[^a-z0-9;]\", \"\"", "\"[<>]\", \"\"")),
    ("app.rb", "require 'sinatra'\nget '/r' do\n  system(\"echo \"+params[:q].gsub({a}))\n  'ok'\nend\n",
     ("/[^a-z0-9]/, ''", "/[^a-z0-9]/, 'x'", "/[^a-z0-9;]/, ''", "/[<>]/, ''")),
]


@pytest.mark.parametrize("name,tpl,args", SANITIZE)
def test_stripping_replace_sanitizes(tmp_path, name, tpl, args):
    (tmp_path / name).write_text(tpl.format(a=args[0]))
    assert discover_repo(tmp_path) == []


@pytest.mark.parametrize("name,tpl,args", SANITIZE)
@pytest.mark.parametrize("which", [1, 2, 3])
def test_weaker_replace_still_reports(tmp_path, name, tpl, args, which):
    (tmp_path / name).write_text(tpl.format(a=args[which]))
    assert len(discover_repo(tmp_path)) == 1


OTHERS = [
    ("C.cs", "using Microsoft.AspNetCore.Mvc;using System.Diagnostics;using System.Text.RegularExpressions;\npublic class C:Controller{{\n  static bool Ok(string v)=>{check};\n  [HttpGet(\"/r\")] public string R(string q){{ if(!Ok(q)) return \"no\"; Process.Start(\"sh\",\"-c echo \"+q); return \"ok\"; }}\n}}\n",
     "Regex.IsMatch(v,\"^[a-z0-9]+$\")", "v.Length < 10"),
    ("app.rb", "require 'sinatra'\ndef ok?(v); {check}; end\nget '/r' do\n  v = params[:q]\n  halt 400 unless ok?(v)\n  system(\"echo \"+v)\n  'ok'\nend\n",
     "v.match?(/\\A[a-z0-9]+\\z/)", "v.length < 10"),
    ("main.go", "package main\nimport (\"net/http\";\"os/exec\";\"regexp\")\nvar re = regexp.MustCompile(`^[a-z0-9]+$`)\nfunc ok(v string) bool {{ return {check} }}\nfunc main(){{ http.HandleFunc(\"/r\", func(w http.ResponseWriter, r *http.Request){{ v := r.URL.Query().Get(\"q\"); if !ok(v) {{ return }}; exec.Command(\"sh\",\"-c\",\"echo \"+v).Run() }}); http.ListenAndServe(\":80\", nil) }}\nvar _ = re\n",
     "re.MatchString(v)", "len(v) < 10"),
    ("App.java", "import org.springframework.web.bind.annotation.*;\n@RestController public class App {{\n  static boolean ok(String v){{ return {check}; }}\n  @GetMapping(\"/r\") public String r(@RequestParam String q) throws Exception {{ if(!ok(q)) return \"no\"; Runtime.getRuntime().exec(\"echo \"+q); return \"ok\"; }}\n}}\n",
     "v.matches(\"[a-z0-9]+\")", "v.length() < 10"),
    ("a.php", "<?php\nfunction ok($v){{ return {check}; }}\n$v=$_GET['q'];\nif(!ok($v)){{ exit; }}\nsystem('echo '.$v);\n",
     "preg_match('/^[a-z0-9]+$/', $v) === 1", "strlen($v) < 10"),
]


@pytest.mark.parametrize("name,tpl,strong,weak", OTHERS)
def test_validator_function_proves_its_argument_in_every_language(tmp_path, name, tpl, strong, weak):
    (tmp_path / name).write_text(tpl.format(check=strong))
    assert discover_repo(tmp_path) == []


@pytest.mark.parametrize("name,tpl,strong,weak", OTHERS)
def test_length_only_validator_still_reports(tmp_path, name, tpl, strong, weak):
    (tmp_path / name).write_text(tpl.format(check=weak))
    assert len(discover_repo(tmp_path)) == 1


def test_java_map_of_lookup_is_a_constant(tmp_path):
    (tmp_path / "App.java").write_text("import org.springframework.web.bind.annotation.*;\nimport java.util.*;\n@RestController public class App {\n  static final Map<String,String> CMDS=Map.of(\"a\",\"echo a\",\"b\",\"echo b\");\n  @GetMapping(\"/r\") public String r(@RequestParam String q) throws Exception { Runtime.getRuntime().exec(CMDS.get(q)); return \"ok\"; }\n}\n")
    assert discover_repo(tmp_path) == []
