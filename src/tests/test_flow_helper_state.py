"""Records, dataclasses, shared module-level containers and embedded structs carry a request value to the sink; the same shapes with a constant stay quiet."""
import pytest

from aletheore.flow.discover import discover_repo

PY_DC = "import os\nfrom dataclasses import dataclass\nfrom flask import Flask, request\napp = Flask(__name__)\n@dataclass\nclass Job:\n    cmd: str\n    n: int = 1\n@app.route('/r')\ndef r():\n    j = Job(cmd='echo ' + {v})\n    os.system(j.cmd)\n    return 'ok'\n"
PY_MAP = "import os\nfrom flask import Flask, request\napp = Flask(__name__)\nCFG = {{}}\ndef setup(v):\n    CFG['cmd'] = 'echo ' + v\n@app.route('/r')\ndef r():\n    setup({v})\n    os.system(CFG['cmd'])\n    return 'ok'\n"
JAVA_REC = "import org.springframework.web.bind.annotation.*;\n@RestController public class App {{\n  record Job(String cmd, int n) {{}}\n  @GetMapping(\"/r\") public String r(@RequestParam String q) throws Exception {{ Job j=new Job(\"echo \"+{v}, 1); Runtime.getRuntime().exec(j.cmd()); return \"ok\"; }}\n}}\n"
CS_REC = "using Microsoft.AspNetCore.Mvc;using System.Diagnostics;\npublic record Job(string Cmd,int N);\npublic class C:Controller{{\n  [HttpGet(\"/r\")] public string R(string q){{ var j=new Job(\"echo \"+{v},1); Process.Start(\"sh\",\"-c \"+j.Cmd); return \"ok\"; }}\n}}\n"
RB_MAP = "require 'sinatra'\nCFG = {{}}\ndef setup(v); CFG[:cmd] = \"echo \"+v; end\nget '/r' do\n  setup({v})\n  system(CFG[:cmd])\n  'ok'\nend\n"
GO_EMB = "package main\nimport (\"net/http\";\"os/exec\")\ntype Base struct{{}}\nfunc (b *Base) Run(c string){{ exec.Command(\"sh\",\"-c\",c).Run() }}\ntype Child struct{{ Base }}\nfunc (c *Child) Go(v string){{ c.Run(\"echo \"+v) }}\nfunc main(){{ http.HandleFunc(\"/r\", func(w http.ResponseWriter, r *http.Request){{ (&Child{{}}).Go({v}) }}); http.ListenAndServe(\":80\", nil) }}\n"

CASES = [
    ("app.py", PY_DC, "request.args['q']", "'x'"),
    ("app.py", PY_MAP, "request.args['q']", "'x'"),
    ("App.java", JAVA_REC, "q", "\"x\""),
    ("C.cs", CS_REC, "q", "\"x\""),
    ("app.rb", RB_MAP, "params[:q]", "'x'"),
    ("main.go", GO_EMB, "r.URL.Query().Get(\"q\")", "\"x\""),
]


@pytest.mark.parametrize("name,tpl,tainted,safe", CASES)
def test_request_value_reaches_the_sink(tmp_path, name, tpl, tainted, safe):
    (tmp_path / name).write_text(tpl.format(v=tainted))
    assert len(discover_repo(tmp_path)) == 1


@pytest.mark.parametrize("name,tpl,tainted,safe", CASES)
def test_constant_value_stays_quiet(tmp_path, name, tpl, tainted, safe):
    (tmp_path / name).write_text(tpl.format(v=safe))
    assert discover_repo(tmp_path) == []
