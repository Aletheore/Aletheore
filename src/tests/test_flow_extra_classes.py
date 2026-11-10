"""The shared sink catalogue (extra_sinks): template injection, regex injection, unsafe reflection, expression
language and NoSQL injection, and ORM / query-builder raw-SQL APIs, in every language. Each vulnerable case
has a safe twin that must stay quiet for that class."""
from pathlib import Path

import pytest

from aletheore.flow.discover import discover_repo


def _wrap(lang: str, body: str) -> tuple[str, str]:
    if lang == "py":
        return "app.py", ("import re, jinja2\nimport pandas as pd\nfrom jinja2 import Environment\nfrom sqlalchemy import text\n"
                          "from flask import Flask, request, render_template_string\nfrom werkzeug.utils import secure_filename\napp = Flask(__name__)\n\n"
                          "@app.route('/x')\ndef x():\n    v = request.args.get('q')\n" + "".join("    " + ln + "\n" for ln in body.split("\n")) + "    return 'ok'\n")
    if lang == "js":
        return "server.js", ("const express = require('express');\nconst _ = require('lodash');\nconst ejs = require('ejs');\n"
                             "const app = express();\napp.get('/x', (req, res) => {\n  const v = req.query.q;\n  " + body + "\n  res.json({});\n});\n")
    if lang == "php":
        return "index.php", "<?php\n$v = $_GET['q'];\n" + body + "\n"
    if lang == "java":
        return "src/main/java/A.java", ("import org.springframework.web.bind.annotation.*;\nimport java.util.regex.Pattern;\n"
                                        "@RestController\npublic class A {\n  @GetMapping(\"/x\")\n  public String x(@RequestParam String v) throws Exception {\n    "
                                        + body + "\n    return \"ok\";\n  }\n}\n")
    if lang == "kt":
        return "src/main/kotlin/A.kt", ("import org.springframework.web.bind.annotation.*\n@RestController\nclass A {\n    @GetMapping(\"/x\")\n"
                                        "    fun x(@RequestParam v: String): String {\n        " + body + "\n        return \"ok\"\n    }\n}\n")
    if lang == "cs":
        return "Controllers/AController.cs", ("using System;\nusing System.Text.RegularExpressions;\nusing Microsoft.AspNetCore.Mvc;\n"
                                              "public class AController : Controller {\n    public IActionResult Index(string v) {\n        " + body
                                              + "\n        return Ok();\n    }\n}\n")
    if lang == "go":
        return "main.go", ("package main\n\nimport (\n\t\"net/http\"\n\t\"text/template\"\n\tsq \"github.com/Masterminds/squirrel\"\n)\n\n"
                           "func h(w http.ResponseWriter, r *http.Request) {\n\tv := r.FormValue(\"q\")\n\t" + body + "\n}\n")
    if lang == "rb":
        return "app/controllers/a_controller.rb", ("class AController < ApplicationController\n  def show\n    v = params[:q]\n    " + body + "\n  end\nend\n")
    if lang == "rs":
        return "src/main.rs", ("use actix_web::{web, HttpResponse};\nuse std::collections::HashMap;\n"
                               "async fn h(query: web::Query<HashMap<String, String>>) -> HttpResponse {\n    let v = query.get(\"q\").unwrap();\n    "
                               + body + "\n    HttpResponse::Ok().finish()\n}\n")
    raise ValueError(lang)


# (language, sink code, rule slug, vulnerable?)
CASES = [
    # --- SQL through ORM / query-builder raw APIs
    ("py", "pd.read_sql(\"SELECT * FROM t WHERE a = '%s'\" % v, None)", "sql-injection", True),
    ("py", "conn.fetch(\"SELECT * FROM t WHERE a = '\" + v + \"'\")", "sql-injection", True),
    ("py", "conn.fetch(\"SELECT * FROM t WHERE a = $1\", v)", "sql-injection", False),
    ("js", "prisma.$queryRawUnsafe(\"SELECT * FROM u WHERE n = '\" + v + \"'\");", "sql-injection", True),
    ("js", "prisma.$queryRawUnsafe(\"SELECT * FROM u WHERE n = $1\", v);", "sql-injection", False),
    ("js", "knex('u').whereRaw(\"n = '\" + v + \"'\");", "sql-injection", True),
    ("js", "knex('u').whereRaw('n = ?', [v]);", "sql-injection", False),
    ("js", "repo.createQueryBuilder('u').where(\"u.n = '\" + v + \"'\").getMany();", "sql-injection", True),
    ("js", "repo.createQueryBuilder('u').where('u.n = :n', { n: v }).getMany();", "sql-injection", False),
    ("cs", "conn.Query<string>(\"SELECT n FROM u WHERE n = '\" + v + \"'\");", "sql-injection", True),
    ("cs", "conn.Query<string>(\"SELECT n FROM u WHERE n = @n\", new { n = v });", "sql-injection", False),
    ("rb", "User.order(v)", "sql-injection", True),
    ("rb", "User.where(\"name = '#{v}'\")", "sql-injection", True),
    ("rb", "User.where(name: v)", "sql-injection", False),
    ("go", "_ = sq.Expr(\"n = '\" + v + \"'\")", "sql-injection", True),
    ("go", "_ = sq.Expr(\"n = ?\", v)", "sql-injection", False),
    # --- server-side template injection
    ("py", "jinja2.Template(v).render()", "template-injection", True),
    ("py", "Environment().from_string(v).render()", "template-injection", True),
    ("py", "render_template_string('Hello ' + v)", "template-injection", True),
    ("py", "render_template_string('Hello {{ n }}', n=v)", "template-injection", False),
    ("js", "ejs.render(v, {});", "template-injection", True),
    ("js", "ejs.render('<p><%= n %></p>', { n: v });", "template-injection", False),
    ("js", "_.template('Hi ' + v)({});", "template-injection", True),
    ("php", "$twig->createTemplate($v);", "template-injection", True),
    ("php", "$twig->createTemplate('Hello {{ n }}');", "template-injection", False),
    ("java", "org.apache.velocity.app.Velocity.evaluate(ctx, w, \"t\", v);", "template-injection", True),
    ("cs", "var t = Scriban.Template.Parse(v);", "template-injection", True),
    ("cs", "var t = Scriban.Template.Parse(\"Hello {{ n }}\");", "template-injection", False),
    ("go", "t, _ := template.New(\"x\").Parse(v)\n\t_ = t", "template-injection", True),
    ("go", "t, _ := template.New(\"x\").Parse(\"Hello {{.}}\")\n\t_ = t", "template-injection", False),
    ("rb", "ERB.new(v).result(binding)", "template-injection", True),
    ("rb", "ERB.new(\"Hello <%= 1 %>\").result(binding)", "template-injection", False),
    ("rs", "let _ = tera::Tera::one_off(v, &tera::Context::new(), true);", "template-injection", True),
    ("rs", "let _ = tera::Tera::one_off(\"Hello\", &tera::Context::new(), true);", "template-injection", False),
    # --- regex injection
    ("py", "re.compile(v)", "regex-injection", True),
    ("py", "re.compile(re.escape(v))", "regex-injection", False),
    ("js", "new RegExp(v);", "regex-injection", True),
    ("js", "new RegExp(_.escapeRegExp(v));", "regex-injection", False),
    ("php", "preg_match($v, 'abc');", "regex-injection", True),
    ("php", "preg_match('/' . preg_quote($v, '/') . '/', 'abc');", "regex-injection", False),
    ("java", "Pattern.compile(v);", "regex-injection", True),
    ("java", "Pattern.compile(Pattern.quote(v));", "regex-injection", False),
    ("kt", "Regex(v)", "regex-injection", True),
    ("kt", "Regex(Regex.escape(v))", "regex-injection", False),
    ("cs", "var r = new Regex(v);", "regex-injection", True),
    ("cs", "var r = new Regex(Regex.Escape(v));", "regex-injection", False),
    ("cs", "Regex.IsMatch(\"abc\", v);", "regex-injection", True),
    ("rb", "Regexp.new(v)", "regex-injection", True),
    ("rb", "Regexp.new(Regexp.escape(v))", "regex-injection", False),
    # --- unsafe reflection
    ("java", "Class.forName(v);", "unsafe-reflection", True),
    ("java", "Class.forName(\"com.example.A\");", "unsafe-reflection", False),
    ("kt", "Class.forName(v)", "unsafe-reflection", True),
    ("cs", "var t = Type.GetType(v);", "unsafe-reflection", True),
    ("cs", "var t = Type.GetType(\"System.String\");", "unsafe-reflection", False),
    ("php", "call_user_func($v);", "unsafe-reflection", True),
    ("php", "call_user_func('strtolower', $v);", "unsafe-reflection", False),
    ("rb", "Object.const_get(v)", "unsafe-reflection", True),
    ("rb", "current_user.send(v)", "unsafe-reflection", True),
    ("rb", "current_user.send(:name)", "unsafe-reflection", False),
    # --- expression language
    ("java", "new org.springframework.expression.spel.standard.SpelExpressionParser().parseExpression(v).getValue();", None, True),
    ("java", "ognl.Ognl.getValue(v, ctx, root);", "expression-injection", True),
    ("kt", "org.mvel2.MVEL.eval(v)", "expression-injection", True),
    # --- CORS origin reflection
    ("js", "res.setHeader('Access-Control-Allow-Origin', req.headers.origin);", "cors-origin-reflection", True),
    ("js", "res.setHeader('Access-Control-Allow-Origin', 'https://app.example.com');", "cors-origin-reflection", False),
    ("py", "resp = make_response('x')\nresp.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin')", "cors-origin-reflection", True),
    ("py", "resp = make_response('x')\nresp.headers['Access-Control-Allow-Origin'] = 'https://a.example'", "cors-origin-reflection", False),
    ("java", "resp.setHeader(\"Access-Control-Allow-Origin\", v);", "cors-origin-reflection", True),
    ("java", "resp.setHeader(\"X-Frame-Options\", v);", "cors-origin-reflection", False),
    ("php", "header(\"Access-Control-Allow-Origin: \" . $v);", "cors-origin-reflection", True),
    ("php", "header(\"Access-Control-Allow-Origin: https://a.example\");", "cors-origin-reflection", False),
    ("go", "w.Header().Set(\"Access-Control-Allow-Origin\", v)", "cors-origin-reflection", True),
    ("cs", "Response.Headers.Add(\"Access-Control-Allow-Origin\", v);", "cors-origin-reflection", True),
    ("rb", "response.headers[\"Access-Control-Allow-Origin\"] = v", "cors-origin-reflection", True),
    ("rb", "response.headers[\"Access-Control-Allow-Origin\"] = \"https://a.example\"", "cors-origin-reflection", False),
    ("kt", "resp.setHeader(\"Access-Control-Allow-Origin\", v)", "cors-origin-reflection", True),
    ("java", "if (v != null && checkURL(v) == null) { return \"no\"; }\n    resp.setHeader(\"Access-Control-Allow-Origin\", v);", "cors-origin-reflection", False),
    ("js", "if (!ALLOWED.includes(req.headers.origin)) return res.end();\n  res.setHeader('Access-Control-Allow-Origin', req.headers.origin);", "cors-origin-reflection", False),
    ("py", "o = request.headers.get('Origin')\nif o not in ALLOWED:\n    return 'no'\nresp = make_response('x')\nresp.headers['Access-Control-Allow-Origin'] = o", "cors-origin-reflection", False),
    # --- argument injection into a fixed program
    ("py", "import subprocess\nsubprocess.run(['git', 'clone', v])", "argument-injection", True),
    ("py", "import subprocess\nsubprocess.run(['git', 'clone', '--', v])", "argument-injection", False),
    ("py", "import subprocess\nsubprocess.run(['git', 'clone', '--depth=' + v])", "argument-injection", False),
    ("py", "import subprocess\nsubprocess.run(['echo', v])", "argument-injection", False),
    ("js", "const cp = require('child_process');\n  cp.execFile('git', ['clone', v]);", "argument-injection", True),
    ("js", "const cp = require('child_process');\n  cp.execFile('git', ['clone', '--', v]);", "argument-injection", False),
    ("go", "_ = exec.Command(\"git\", \"clone\", v)", "argument-injection", True),
    ("go", "_ = exec.Command(\"git\", \"clone\", \"--\", v)", "argument-injection", False),
    ("java", "new ProcessBuilder(\"git\", \"clone\", v).start();", "argument-injection", True),
    ("java", "new ProcessBuilder(\"git\", \"clone\", \"--\", v).start();", "argument-injection", False),
    ("kt", "ProcessBuilder(\"git\", \"clone\", v).start()", "argument-injection", True),
    ("rb", "system(\"git\", \"clone\", v)", "argument-injection", True),
    ("rb", "system(\"git\", \"clone\", \"--\", v)", "argument-injection", False),
    ("cs", "System.Diagnostics.Process.Start(\"git\", \"clone \" + v);", "argument-injection", True),
    ("cs", "System.Diagnostics.Process.Start(\"notepad\", \"clone \" + v);", "argument-injection", False),
    # --- mass assignment
    ("js", "User.create(req.body);", "mass-assignment", True),
    ("js", "User.create({ name: v });", "mass-assignment", False),
    ("js", "Object.assign(user, req.body);", "mass-assignment", True),
    ("js", "prisma.user.create({ data: req.body });", "mass-assignment", True),
    ("js", "prisma.user.create({ data: { name: v } });", "mass-assignment", False),
    ("js", "new User(req.body).save();", "mass-assignment", True),
    ("py", "User(**request.json)", "mass-assignment", True),
    ("py", "User(name=request.json['name'])", "mass-assignment", False),
    ("rb", "User.new(params.permit!)", "mass-assignment", True),
    ("rb", "User.new(params.require(:user).permit(:name))", "mass-assignment", False),
    # --- prototype pollution
    ("js", "store[req.body.a][req.body.b] = v;", "prototype-pollution", True),
    ("js", "store[req.body.a] = v;", "prototype-pollution", False),
    ("js", "store['users'][req.body.b] = v;", "prototype-pollution", False),
    # --- uploads saved under the client's file name
    ("py", "f = request.files['f']\nf.save('/srv/uploads/' + f.filename)", "path-traversal", True),
    ("py", "f = request.files['f']\nf.save('/srv/uploads/' + secure_filename(f.filename))", "path-traversal", False),
    ("js", "req.files.f.mv('/srv/uploads/' + req.files.f.name);", "path-traversal", True),
    # --- XPath (JS)
    ("js", "const xpath = require('xpath');\n  xpath.select(\"//user[name='\" + v + \"']\", doc);", "xpath-injection", True),
    ("rb", "v =~ /#{params[:p]}/", "regex-injection", True),
    ("rb", "v =~ /\\A[a-z]+\\z/", "regex-injection", False),
    ("py", "getattr(handlers, v)()", "unsafe-reflection", True),
    ("py", "getattr(handlers, 'ping')()", "unsafe-reflection", False),
    ("py", "x = getattr(obj, v, None)", "unsafe-reflection", False),
    # --- NoSQL (pymongo)
    ("py", "db.users.find_one(request.json)", "nosql-injection", True),
    ("py", "db.users.find_one({'$where': \"this.name == '\" + v + \"'\"})", "nosql-injection", True),
    ("py", "db.users.find_one({'name': v})", "nosql-injection", False),
    # --- NoSQL
    ("java", "org.bson.Document.parse(v);", "nosql-injection", True),
    ("java", "org.bson.Document.parse(\"{a: 1}\");", "nosql-injection", False),
    ("cs", "var d = MongoDB.Bson.BsonDocument.Parse(v);", "nosql-injection", True),
]


@pytest.mark.parametrize("lang,body,slug,vulnerable", CASES, ids=[f"{c[0]}-{c[2]}-{'v' if c[3] else 's'}-{i}" for i, c in enumerate(CASES)])
def test_extra_class(tmp_path: Path, lang, body, slug, vulnerable):
    rel, src = _wrap(lang, body)
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(src)
    slugs = {f["rule_id"].split(".")[-1] for f in discover_repo(tmp_path)}
    if slug is None:  # SpEL: reported as code or expression injection, either is right
        assert slugs & {"expression-injection", "code-injection"}, slugs
    elif vulnerable:
        assert slug in slugs, slugs
    else:
        assert slug not in slugs, slugs
