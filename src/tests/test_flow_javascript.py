"""JavaScript and TypeScript discover mode: flows are followed across functions, closures and modules,
and the near misses that produced false positives on real projects are not reported."""

import pytest

from aletheore.flow.discover_js import discover_javascript


def run(tmp_path, files):
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return discover_javascript(tmp_path)


def kinds(found):
    return sorted({f["rule_id"].split(".")[-1] for f in found})


def handler(body, ts=False):
    sig = "(req: Request, res: Response)" if ts else "(req, res)"
    return f"const express = require('express');\nconst app = express();\napp.get('/x', {sig} => {{\n{body}\n}});\n"


TAINTED = {
    "eval of a body field": ("eval(req.body.expr);", "code-injection"),
    "sql in a template string": ("db.query(`SELECT * FROM t WHERE n = '${req.query.n}'`);", "sql-injection"),
    "sql by concatenation": ("connection.query('SELECT ' + req.params.id);", "sql-injection"),
    "destructured value": ("const { id } = req.params;\n db.query('SELECT ' + id);", "sql-injection"),
    "child process": ("const cp = require('child_process');\n cp.exec('ls ' + req.query.d);", "command-injection"),
    "fs read": ("const fs = require('fs');\n fs.readFile(req.query.f, () => {});", "path-traversal"),
    "send file": ("res.sendFile(req.query.f);", "path-traversal"),
    "redirect": ("res.redirect(req.query.next);", "open-redirect"),
    "ssrf": ("const axios = require('axios');\n axios.get(req.query.u);", "ssrf"),
    "reflected html": ("res.send('<h1>' + req.query.q + '</h1>');", "xss"),
    "nosql $where": ("db.collection('c').find({ $where: 'this.a == ' + req.query.a });", "nosql-injection"),
    "tainted in a callback": ("setTimeout(() => {}, 1);\n db.get('x', (err, r) => { res.send('<b>' + req.query.q + '</b>'); });", "xss"),
    "branch": ("let n = 'x';\n if (req.query.m) { n = req.query.a; }\n db.query('SELECT ' + n);", "sql-injection"),
    "array element": ("const parts = ['a'];\n parts.push(req.query.b);\n db.query(parts.join(' '));", "sql-injection"),
    "object property": ("const o = { k: req.query.b };\n db.query('SELECT ' + o.k);", "sql-injection"),
    "session write": ("req.session.user = req.body.user;", "trust-boundary"),
}


@pytest.mark.parametrize("name", sorted(TAINTED))
def test_tainted_flows_are_reported(tmp_path, name):
    body, expected = TAINTED[name]
    assert expected in kinds(run(tmp_path, {"app.js": handler(body)})), name


SAFE = {
    "constant query": "db.query('SELECT 1');",
    "parameterized": "db.query('SELECT * FROM t WHERE n = ?', [req.query.n]);",
    "number first": "db.query('SELECT ' + parseInt(req.query.n, 10));",
    "overwritten": "let n = req.query.n;\n n = 'fixed';\n db.query('SELECT ' + n);",
    "dead branch": "const n = (1 > 2) ? req.query.a : 'safe';\n db.query('SELECT ' + n);",
    "safe key of an object": "const o = { a: 'safe', b: req.query.b };\n db.query('SELECT ' + o.a);",
    "escaped html": "res.send('<b>' + escapeHtml(req.query.q) + '</b>');",
    "encoded url": "res.send('<a href=\"?q=' + encodeURIComponent(req.query.q) + '\">x</a>');",
    "json response": "res.json({ q: req.query.q });",
    "redirect to a constant": "res.redirect('/home');",
    "set content type to json": "res.setHeader('Content-Type', 'application/json');\n res.send('{\"q\":\"' + req.query.q + '\"}');",
    "event stream": "res.setHeader('Content-Type', 'text/event-stream');\n res.write(`data: ${JSON.stringify({ t: req.query.q })}\\n\\n`);",
    "file contents are not request data": "const fs = require('fs');\n const yaml = require('js-yaml');\n yaml.load(fs.readFileSync('./static/' + 'x.yml', 'utf8'));",
}


@pytest.mark.parametrize("name", sorted(SAFE))
def test_safe_flows_are_not_reported(tmp_path, name):
    assert run(tmp_path, {"app.js": handler(SAFE[name])}) == [], name


def test_a_file_read_by_a_tainted_path_is_a_traversal_but_its_contents_are_not_tainted(tmp_path):
    body = "const fs = require('fs');\n const yaml = require('js-yaml');\n yaml.load(fs.readFileSync('./d/' + req.params.key, 'utf8'));"
    assert kinds(run(tmp_path, {"app.js": handler(body)})) == ["path-traversal"]


def test_a_dao_in_another_file_built_with_a_constructor_function_and_a_closure(tmp_path):
    # NodeGoat's shape: methods assigned to `this`, the query built by a nested arrow function.
    found = run(tmp_path, {
        "routes.js": ("const Dao = require('./dao').Dao;\nfunction Handler(db) {\n  const dao = new Dao(db);\n"
                      "  this.show = (req, res, next) => {\n    const { threshold } = req.query;\n    dao.find(threshold, () => {});\n  };\n}\n"),
        "dao.js": ("const Dao = function (db) {\n  const col = db.collection('a');\n  this.find = (threshold, cb) => {\n"
                   "    const criteria = () => {\n      if (threshold) { return { $where: `this.s > '${threshold}'` }; }\n      return { u: 1 };\n    };\n"
                   "    col.find(criteria()).toArray(cb);\n  };\n};\nmodule.exports.Dao = Dao;\n"),
    })
    assert kinds(found) == ["nosql-injection"] and found[0]["path"] == "dao.js"


def test_a_helper_module_is_followed_through_require(tmp_path):
    found = run(tmp_path, {
        "app.js": handler("const util = require('./util');\n util.run(req.query.c);"),
        "util.js": "const { exec } = require('child_process');\nexports.run = function (c) { exec(c); };\n",
    })
    assert kinds(found) == ["command-injection"] and found[0]["path"] == "util.js"


def test_typescript_typed_request_parameters_are_recognized(tmp_path):
    assert kinds(run(tmp_path, {"app.ts": handler("db.query('SELECT ' + req.query.n);", ts=True)})) == ["sql-injection"]


def test_a_handler_returned_from_a_factory_function_is_found(tmp_path):
    src = ("module.exports = function login() {\n  return (req, res, next) => {\n"
           "    models.sequelize.query(`SELECT * FROM Users WHERE email = '${req.body.email || ''}'`);\n  };\n};\n")
    assert kinds(run(tmp_path, {"routes/login.js": src})) == ["sql-injection"]


def test_a_plain_function_parameter_is_not_assumed_tainted(tmp_path):
    assert run(tmp_path, {"lib.js": "function find(name) {\n  db.query('SELECT ' + name);\n}\n"}) == []


def test_html_content_type_still_reports_xss(tmp_path):
    body = "res.setHeader('Content-Type', 'text/html');\n res.send('<b>' + req.query.q + '</b>');"
    assert kinds(run(tmp_path, {"app.js": handler(body)})) == ["xss"]
