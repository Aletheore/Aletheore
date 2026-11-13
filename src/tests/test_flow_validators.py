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
